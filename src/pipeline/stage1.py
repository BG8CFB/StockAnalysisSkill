from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from src.agents.base_agent import BaseAgent
from src.agents.llm_client import LLMClient
from src.core.task_store import append_task_log
from src.data.calculator import CalculatedDataPacket
from src.tools.tool_injector import inject_tools

logger = logging.getLogger(__name__)

_ANALYST_CN = {
    "technical_analyst": "技术分析师",
    "fundamental_analyst": "基本面分析师",
    "microstructure_analyst": "市场微观结构分析师",
    "sentiment_analyst": "市场情绪分析师",
    "sector_analyst": "板块轮动分析师",
    "news_analyst": "资讯事件分析师",
}


@dataclass
class Stage1Results:
    technical: str = ""
    fundamental: str = ""
    microstructure: str = ""
    sentiment: str = ""
    sector: str = ""
    news: str = ""
    scores: dict = field(default_factory=dict)


async def run_stage1(
    task_id: str,
    stock_code: str,
    packet: CalculatedDataPacket,
    available_tools: set[str],
    market_rules: str,
    skills_list: str,
    llm_client: LLMClient,
    task_semaphore: asyncio.Semaphore,
    cancel_event: asyncio.Event,
) -> Stage1Results:
    """
    Stage 1：六位专项分析师并行运行。
    每个分析师通过 tool_injector 获取其专属数据上下文，然后调用 LLM。
    任何一个分析师失败不影响其他分析师。
    """
    logger.info(f"[Stage1] 启动 6 个分析师并行分析，股票: {stock_code}")
    append_task_log(task_id, "[Stage1] 启动 6 个分析师并行分析")

    analysts = [
        ("technical_analyst", "technical"),
        ("fundamental_analyst", "fundamental"),
        ("microstructure_analyst", "microstructure"),
        ("sentiment_analyst", "sentiment"),
        ("sector_analyst", "sector"),
        ("news_analyst", "news"),
    ]

    async def run_analyst(agent_name: str, field_name: str) -> tuple[str, str]:
        if cancel_event.is_set():
            raise asyncio.CancelledError
        cn_name = _ANALYST_CN.get(agent_name, agent_name)
        t0 = time.monotonic()
        logger.info(f"[Stage1] ▶ {cn_name} 开始")
        append_task_log(task_id, f"[Stage1] ▶ {cn_name} 开始")
        agent = BaseAgent(agent_name, llm_client, task_semaphore, cancel_event)
        user_context = inject_tools(agent_name, packet, available_tools)
        result = await agent.run(user_context, market_rules, skills_list)
        elapsed = time.monotonic() - t0
        msg = f"[Stage1] ✓ {cn_name} 完成（{elapsed:.1f}s，{len(result)}字）"
        logger.info(msg)
        append_task_log(task_id, msg)
        return field_name, result

    # 并行启动所有分析师，失败不中断（return_exceptions=True）
    coroutines = [run_analyst(name, field) for name, field in analysts]
    outcomes = await asyncio.gather(*coroutines, return_exceptions=True)

    results = Stage1Results()
    for (_, field_name), outcome in zip(analysts, outcomes):
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        elif isinstance(outcome, Exception):
            msg = f"[Stage1] ✗ {field_name} 失败: {outcome}"
            logger.error(msg)
            append_task_log(task_id, msg)
            setattr(results, field_name, f"[{field_name}分析师执行失败：{outcome}，本维度不可用]")
        else:
            _, text = outcome
            setattr(results, field_name, text)

    append_task_log(task_id, "[Stage1] ✓ 全部 6 位分析师完成")
    return results
