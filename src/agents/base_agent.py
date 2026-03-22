from __future__ import annotations

import asyncio
import logging

from src.agents.llm_client import LLMClient
from src.agents.config_loader import get_agent_config, get_global_rules
from src.tools.tool_injector import DATA_MISSING_MARKER

logger = logging.getLogger(__name__)


class BaseAgent:
    """
    基础智能体。所有 LLM 智能体的公共实现。

    提示词组装规则：
      System prompt = global_rules（从 config/global.yaml 加载）
                    + "\\n\\n---\\n\\n"
                    + 角色提示词（从 config/agents/*.yaml 的 prompt 字段直接读取）
                    + "\\n\\n---\\n\\n市场交易规则：\\n"
                    + market_rules（运行时动态注入）

      User message  = user_context（数据上下文，由信息工具注入或上游报告组装）

    数据缺失短路：
      若 user_context 以 DATA_MISSING_MARKER 开头，直接返回数据缺失通知，不调用 LLM。

    技能调用（use_skills 控制）：
      use_skills=true：技能作为 function calling 工具传入 LLM，AI 自主决定是否调用。
        按 Agent Skills 标准 (agentskills.io)，AI 只看到技能名和描述，
        调用时才加载完整内容。技能调用通过多轮对话完成，最终 content 为报告。
      use_skills=false：不传入任何技能工具，AI 不知道技能存在。
    """

    def __init__(
        self,
        agent_name: str,
        llm_client: LLMClient,
        task_semaphore: asyncio.Semaphore,
        cancel_event: asyncio.Event,
    ) -> None:
        self.agent_name = agent_name
        self._llm_client = llm_client
        self._task_semaphore = task_semaphore
        self._cancel_event = cancel_event

        config = get_agent_config(agent_name)
        self._role_prompt: str = config["prompt"]
        self._use_skills: bool = config["use_skills"]
        self._display_name: str = config["display_name"]

        self._global_rules: str = get_global_rules()

    def _build_system_prompt(self, market_rules: str) -> str:
        parts = [self._global_rules]
        if self._role_prompt:
            parts.append(self._role_prompt)
        if market_rules:
            parts.append(f"市场交易规则：\n{market_rules}")
        return "\n\n---\n\n".join(parts)

    async def run(
        self,
        user_context: str,
        market_rules: str,
    ) -> str:
        """
        执行智能体分析。

        1. 检查取消信号
        2. 检测数据缺失：若 user_context 以 DATA_MISSING_MARKER 开头，跳过 LLM
        3. 组装提示词（system = global_rules + role_prompt + market_rules）
        4. 构建技能工具定义（use_skills=true 时）
        5. 调用 LLM（支持多轮技能调用）

        返回 LLM 最终文本输出（报告内容）。
        """
        if self._cancel_event.is_set():
            raise asyncio.CancelledError

        # 短路：所有工具均无数据时不调用 LLM
        if user_context.startswith(DATA_MISSING_MARKER):
            detail = user_context[len(DATA_MISSING_MARKER):].strip()
            notice = (
                f"## ⚠ 数据缺失通知\n\n"
                f"**智能体**：`{self._display_name}`（`{self.agent_name}`）\n\n"
                f"本智能体所需的全部数据工具均无可用数据，**无有效数据支撑，不生成分析报告**。\n\n"
                f"### 缺失详情\n\n{detail}"
            )
            logger.warning(
                f"[{self.agent_name}] 数据全部缺失，跳过 LLM 调用，返回数据缺失通知"
            )
            return notice

        system_prompt = self._build_system_prompt(market_rules)

        # 构建技能工具（use_skills=true 时启用 function calling）
        tools = None
        tool_executor = None
        if self._use_skills:
            from src.tools.skills_loader import get_skill_tool_definitions, execute_skill_call
            skill_defs = get_skill_tool_definitions()
            if skill_defs:
                tools = skill_defs
                tool_executor = execute_skill_call

        logger.debug(
            f"[{self.agent_name}] 调用 LLM"
            f"（系统提示 {len(system_prompt)} 字，用户消息 {len(user_context)} 字，"
            f"技能工具={'%d个' % len(tools) if tools else '无'}）"
        )

        try:
            result = await self._llm_client.call(
                system_prompt=system_prompt,
                user_message=user_context,
                task_semaphore=self._task_semaphore,
                tools=tools,
                tool_executor=tool_executor,
            )
            logger.debug(f"[{self.agent_name}] LLM 返回 {len(result)} 字")
            return result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[{self.agent_name}] LLM 调用失败: {e}")
            return f"[{self._display_name} 分析失败：{e}，本智能体输出不可用]"
