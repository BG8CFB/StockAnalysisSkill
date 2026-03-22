from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from src.agents.base_agent import BaseAgent
from src.agents.config_loader import get_agent_config, get_stage1_agents
from src.agents.llm_client import LLMClient
from src.core.task_store import append_task_log, save_agent_output, save_data_evidence
from src.data.calculator import CalculatedDataPacket
from src.tools.tool_injector import inject_tools

logger = logging.getLogger(__name__)


@dataclass
class Stage1Results:
    """
    Stage 1 分析结果。

    reports: dict[agent_id, report_text]
        键为 agent_id（如 "technical_analyst"），值为该智能体的完整输出文本。
        Stage 1 智能体由 config/agents/stage1.yaml 动态配置，数量不固定。

    display_names: dict[agent_id, display_name]
        用于日志和报告中的中文展示名。
    """
    reports: dict[str, str] = field(default_factory=dict)
    display_names: dict[str, str] = field(default_factory=dict)

    def format_for_context(self, max_chars_per_agent: int = 0) -> str:
        """
        将所有 Stage 1 报告格式化为下游智能体的输入上下文。

        参数：
            max_chars_per_agent: 每个报告的最大字符数，0 表示不截断。

        返回格式：
            ## Stage 1 分析报告汇总
            ### [显示名]
            [报告内容]
            ...
        """
        if not self.reports:
            return "## Stage 1 分析报告汇总\n\n（无可用分析报告）"

        lines = ["## Stage 1 分析报告汇总"]
        for agent_id, report in self.reports.items():
            name = self.display_names.get(agent_id, agent_id)
            text = report
            if max_chars_per_agent > 0 and len(text) > max_chars_per_agent:
                text = text[:max_chars_per_agent] + f"\n\n...（摘要截至 {max_chars_per_agent} 字）"
            lines.append(f"\n### {name}\n{text}")

        return "\n".join(lines)


async def run_stage1(
    task_id: str,
    stock_code: str,
    packet: CalculatedDataPacket,
    available_tools: set[str],
    market_rules: str,
    llm_client: LLMClient,
    task_semaphore: asyncio.Semaphore,
    cancel_event: asyncio.Event,
) -> Stage1Results:
    """
    Stage 1：所有 Stage 1 分析师并行运行。

    智能体列表从 config/agents/stage1.yaml 动态加载，用户可在该文件的 agents 列表中
    增删条目来添加或移除分析师，无需修改代码。

    任何一个分析师失败不影响其他分析师（return_exceptions=True）。
    全部失败时抛出 RuntimeError 终止 Stage 1。
    """
    analysts = get_stage1_agents()   # [(agent_id, display_name), ...]
    count = len(analysts)

    logger.info(f"[Stage1] 启动 {count} 个分析师并行分析，股票: {stock_code}")
    append_task_log(task_id, f"[Stage1] 启动 {count} 个分析师并行分析")

    async def run_analyst(agent_id: str, display_name: str) -> tuple[str, str]:
        if cancel_event.is_set():
            raise asyncio.CancelledError
        t0 = time.monotonic()
        logger.info(f"[Stage1] ▶ {display_name} 开始")
        append_task_log(task_id, f"[Stage1] ▶ {display_name} 开始")

        agent = BaseAgent(agent_id, llm_client, task_semaphore, cancel_event)
        user_context = inject_tools(agent_id, packet, available_tools)

        agent_tools = get_agent_config(agent_id).get("tools", [])
        save_data_evidence(
            task_id, stock_code,
            f"stage1_{agent_id}_data.md",
            display_name, agent_id, "stage1",
            agent_tools, user_context,
        )

        result = await agent.run(user_context, market_rules)

        save_agent_output(task_id, stock_code, f"stage1_{agent_id}.md", result)

        elapsed = time.monotonic() - t0
        msg = f"[Stage1] ✓ {display_name} 完成（{elapsed:.1f}s，{len(result)}字）"
        logger.info(msg)
        append_task_log(task_id, msg)
        return agent_id, result

    # 并行启动，失败不中断（return_exceptions=True）
    coroutines = [run_analyst(aid, dname) for aid, dname in analysts]
    outcomes = await asyncio.gather(*coroutines, return_exceptions=True)

    results = Stage1Results(
        display_names={aid: dname for aid, dname in analysts},
    )
    for (agent_id, display_name), outcome in zip(analysts, outcomes):
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        elif isinstance(outcome, Exception):
            msg = f"[Stage1] ✗ {display_name} 失败: {outcome}"
            logger.error(msg)
            append_task_log(task_id, msg)
            results.reports[agent_id] = (
                f"[{display_name} 执行失败：{outcome}，本维度不可用]"
            )
        else:
            _, text = outcome
            results.reports[agent_id] = text

    all_failed = all(isinstance(o, Exception) for o in outcomes)
    if all_failed:
        raise RuntimeError(f"[Stage1] 全部 {count} 位分析师均失败，Stage1 不可用")

    append_task_log(task_id, f"[Stage1] ✓ 全部 {count} 位分析师完成")
    return results
