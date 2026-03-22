from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.agents.llm_client import LLMClient

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "docs" / "prompts"
_prompt_cache: dict[str, str] = {}


def _load_prompt_file(filename: str) -> str:
    """加载提示词文件，首次读取后内存缓存。"""
    if filename in _prompt_cache:
        return _prompt_cache[filename]
    path = _PROMPTS_DIR / filename
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        content = f"[提示词文件 {filename} 未找到]"
        logger.error(f"[BaseAgent] 提示词文件未找到: {path}")
    _prompt_cache[filename] = content
    return content


class BaseAgent:
    """
    基础智能体。所有 LLM 智能体的公共实现。

    提示词组装规则：
      System prompt = global_rules.md 内容
                    + "\\n\\n---\\n\\n"
                    + 角色提示词
                    + "\\n\\n---\\n\\n市场交易规则：\\n"
                    + market_rules（运行时动态注入）

      User message  = "（可用技能列表：\\n"
                    + skills_list
                    + "\\n---\\n\\n）\\n\\n"
                    + user_context（数据上下文）
    """

    def __init__(
        self,
        agent_name: str,
        llm_client: LLMClient,
        task_semaphore: asyncio.Semaphore,
        cancel_event: asyncio.Event,
    ) -> None:
        from src.agents.registry import AGENT_CONFIGS
        self.agent_name = agent_name
        self._llm_client = llm_client
        self._task_semaphore = task_semaphore
        self._cancel_event = cancel_event

        config = AGENT_CONFIGS.get(agent_name, {})
        self._prompt_file: str = config.get("prompt_file", "")
        self._role_prompt: str = _load_prompt_file(self._prompt_file) if self._prompt_file else ""
        self._global_rules: str = _load_prompt_file("global_rules.md")

    def _build_system_prompt(self, market_rules: str) -> str:
        parts = [self._global_rules]
        if self._role_prompt:
            parts.append(self._role_prompt)
        if market_rules:
            parts.append(f"市场交易规则：\n{market_rules}")
        return "\n\n---\n\n".join(parts)

    def _build_user_message(self, user_context: str, skills_list: str) -> str:
        if skills_list:
            header = f"（可用技能列表：\n{skills_list}\n---\n\n）\n\n"
        else:
            header = ""
        return header + user_context

    async def run(
        self,
        user_context: str,
        market_rules: str,
        skills_list: str,
    ) -> str:
        """
        执行智能体分析。
        1. 检查取消信号
        2. 组装提示词
        3. 调用 LLM
        返回 LLM 文本输出。
        """
        if self._cancel_event.is_set():
            raise asyncio.CancelledError

        system_prompt = self._build_system_prompt(market_rules)
        user_message = self._build_user_message(user_context, skills_list)

        logger.debug(f"[{self.agent_name}] 调用 LLM（系统提示 {len(system_prompt)} 字，用户消息 {len(user_message)} 字）")

        try:
            result = await self._llm_client.call(
                system_prompt=system_prompt,
                user_message=user_message,
                task_semaphore=self._task_semaphore,
            )
            logger.debug(f"[{self.agent_name}] LLM 返回 {len(result)} 字")
            return result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[{self.agent_name}] LLM 调用失败: {e}")
            return f"[{self.agent_name} 分析失败：{e}，本智能体输出不可用]"
