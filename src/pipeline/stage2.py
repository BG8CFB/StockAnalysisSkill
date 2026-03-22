from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from src.agents.base_agent import BaseAgent
from src.agents.llm_client import LLMClient
from src.core.task_store import append_task_log
from src.data.calculator import CalculatedDataPacket
from src.pipeline.stage1 import Stage1Results
from src.tools.tool_injector import inject_tools

logger = logging.getLogger(__name__)


@dataclass
class Stage2Results:
    bull_rounds: list[str] = field(default_factory=list)
    bear_rounds: list[str] = field(default_factory=list)
    director_report: str = ""
    trading_plan: str = ""


def _format_stage1_context(stage1: Stage1Results) -> str:
    """将 Stage1 六维报告格式化为 Stage2 输入上下文。"""
    return "\n\n".join([
        "## Stage 1 分析报告汇总",
        f"### 技术分析\n{stage1.technical}",
        f"### 基本面分析\n{stage1.fundamental}",
        f"### 市场微观结构分析\n{stage1.microstructure}",
        f"### 市场情绪分析\n{stage1.sentiment}",
        f"### 板块轮动分析\n{stage1.sector}",
        f"### 资讯事件分析\n{stage1.news}",
    ])


def _format_debate_context(
    stage1: Stage1Results,
    bull_rounds: list[str],
    bear_rounds: list[str],
) -> str:
    """将辩论记录格式化为研究主管的输入上下文。"""
    stage1_ctx = _format_stage1_context(stage1)
    debate_lines = [stage1_ctx, "\n## 多空辩论记录"]

    for i, (bull, bear) in enumerate(zip(bull_rounds, bear_rounds)):
        round_label = "初始论点" if i == 0 else f"第{i}轮反驳"
        debate_lines.append(f"\n### {round_label}")
        debate_lines.append(f"**看涨方**：\n{bull}")
        debate_lines.append(f"\n**看跌方**：\n{bear}")

    return "\n".join(debate_lines)


async def run_stage2(
    task_id: str,
    stage1_results: Stage1Results,
    packet: CalculatedDataPacket,
    available_tools: set[str],
    market_rules: str,
    skills_list: str,
    llm_client: LLMClient,
    task_semaphore: asyncio.Semaphore,
    cancel_event: asyncio.Event,
    debate_rounds: int = 2,
) -> Stage2Results:
    """
    Stage 2：多空辩论 → 研究主管裁决 → 交易计划师。

    Round 0（并行）：看涨/看跌同时输出初始论点
    Rounds 1..N（串行）：交替反驳，看涨先，看跌后
    最后：研究主管汇总裁决 → 交易计划师生成交易参数
    """
    append_task_log(task_id, "[Stage2] 启动多空辩论流程")

    stage1_ctx = _format_stage1_context(stage1_results)

    bull = BaseAgent("bull_researcher", llm_client, task_semaphore, cancel_event)
    bear = BaseAgent("bear_researcher", llm_client, task_semaphore, cancel_event)
    director = BaseAgent("research_director", llm_client, task_semaphore, cancel_event)
    planner = BaseAgent("trading_planner", llm_client, task_semaphore, cancel_event)

    bull_rounds: list[str] = []
    bear_rounds: list[str] = []

    # Round 0：并行
    if cancel_event.is_set():
        raise asyncio.CancelledError

    append_task_log(task_id, "[Stage2] ▶ Round 0：看涨/看跌分析师并行启动")
    bull_r0, bear_r0 = await asyncio.gather(
        bull.run(stage1_ctx, market_rules, skills_list),
        bear.run(stage1_ctx, market_rules, skills_list),
    )
    bull_rounds.append(bull_r0)
    bear_rounds.append(bear_r0)
    logger.info(f"[Stage2] Round 0 completed: bull={len(bull_r0)}ch bear={len(bear_r0)}ch")
    append_task_log(task_id, f"[Stage2] ✓ Round 0 完成（看涨{len(bull_r0)}字，看跌{len(bear_r0)}字）")

    # Rounds 1..N：串行辩论
    for i in range(1, debate_rounds + 1):
        if cancel_event.is_set():
            raise asyncio.CancelledError

        append_task_log(task_id, f"[Stage2] ▶ Round {i}：看涨方反驳")
        bull_ctx = stage1_ctx + f"\n\n**看跌方最新论点**：\n{bear_rounds[-1]}"
        bull_rebuttal = await bull.run(bull_ctx, market_rules, skills_list)
        bull_rounds.append(bull_rebuttal)
        logger.info(f"[Stage2] Round {i} bull rebuttal: {len(bull_rebuttal)}ch")
        append_task_log(task_id, f"[Stage2] ✓ Round {i} 看涨方完成（{len(bull_rebuttal)}字）")

        if cancel_event.is_set():
            raise asyncio.CancelledError

        append_task_log(task_id, f"[Stage2] ▶ Round {i}：看跌方反驳")
        bear_ctx = stage1_ctx + f"\n\n**看涨方最新论点**：\n{bull_rounds[-1]}"
        bear_rebuttal = await bear.run(bear_ctx, market_rules, skills_list)
        bear_rounds.append(bear_rebuttal)
        logger.info(f"[Stage2] Round {i} bear rebuttal: {len(bear_rebuttal)}ch")
        append_task_log(task_id, f"[Stage2] ✓ Round {i} 看跌方完成（{len(bear_rebuttal)}字）")

    # 研究主管裁决
    if cancel_event.is_set():
        raise asyncio.CancelledError

    append_task_log(task_id, "[Stage2] ▶ 研究主管 开始综合裁决")
    debate_ctx = _format_debate_context(stage1_results, bull_rounds, bear_rounds)
    director_report = await director.run(debate_ctx, market_rules, skills_list)
    logger.info(f"[Stage2] Director report: {len(director_report)}ch")
    append_task_log(task_id, f"[Stage2] ✓ 研究主管裁决完成（{len(director_report)}字）")

    # 交易计划师：研究主管报告 + 价格快照
    if cancel_event.is_set():
        raise asyncio.CancelledError

    append_task_log(task_id, "[Stage2] ▶ 交易计划师 开始制定交易方案")
    snapshot_ctx = inject_tools("trading_planner", packet, available_tools)
    planner_input = director_report + "\n\n---\n\n" + snapshot_ctx
    trading_plan = await planner.run(planner_input, market_rules, skills_list)
    logger.info(f"[Stage2] Trading plan: {len(trading_plan)}ch")
    append_task_log(task_id, f"[Stage2] ✓ 交易计划书完成（{len(trading_plan)}字）")

    append_task_log(task_id, "[Stage2] ✓ 多空辩论流程全部完成")

    return Stage2Results(
        bull_rounds=bull_rounds,
        bear_rounds=bear_rounds,
        director_report=director_report,
        trading_plan=trading_plan,
    )
