from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from src.agents.base_agent import BaseAgent
from src.agents.config_loader import get_agent_config
from src.agents.llm_client import LLMClient
from src.core.task_store import (
    append_task_log,
    load_agent_output,
    save_agent_output,
    save_data_evidence,
)
from src.data.calculator import CalculatedDataPacket
from src.pipeline.stage1 import Stage1Results
from src.pipeline.utils import run_with_stage_retry
from src.tools.tool_injector import inject_tools

logger = logging.getLogger(__name__)



@dataclass
class Stage2Results:
    bull_rounds: list[str] = field(default_factory=list)   # Round 0 = 初始报告，1+ = 辩论回应
    bear_rounds: list[str] = field(default_factory=list)
    director_report: str = ""
    trading_plan: str = ""


# --------------------------------------------------------------------------- #
# 上下文格式化                                                                   #
# --------------------------------------------------------------------------- #

def _make_bull_context(stage1: Stage1Results, round_idx: int, bear_last: str) -> str:
    """
    构建看涨分析师的输入上下文，并注入轮次标记，使 LLM 能明确判断当前模式。

    Round 0：独立分析模式，不含对手观点。
    Round N (N≥1)：辩论反驳模式，包含看跌方最新论点，要求 LLM 仅输出反驳章节。
    """
    stage1_ctx = stage1.format_for_context()
    if round_idx == 0:
        return (
            "**[第0轮 — 独立分析模式]**\n"
            "请基于以下 Stage 1 分析报告，独立输出完整的看涨分析报告（格式见角色提示词 Round 0 部分）。\n\n"
            + stage1_ctx
        )
    else:
        return (
            f"**[第{round_idx}轮 — 辩论反驳模式]**\n"
            f"请针对看跌分析师第{round_idx}轮论点逐一反驳。\n"
            f"**禁止重复输出完整报告**，仅输出辩论反驳章节（格式见角色提示词 Round 1+ 部分）。\n\n"
            + stage1_ctx
            + f"\n\n---\n\n**看跌方第{round_idx}轮论点**：\n{bear_last}"
        )


def _make_bear_context(stage1: Stage1Results, round_idx: int, bull_last: str) -> str:
    """构建看跌分析师的输入上下文，逻辑与 _make_bull_context 对称。"""
    stage1_ctx = stage1.format_for_context()
    if round_idx == 0:
        return (
            "**[第0轮 — 独立分析模式]**\n"
            "请基于以下 Stage 1 分析报告，独立输出完整的看跌分析报告（格式见角色提示词 Round 0 部分）。\n\n"
            + stage1_ctx
        )
    else:
        return (
            f"**[第{round_idx}轮 — 辩论反驳模式]**\n"
            f"请针对看涨分析师第{round_idx}轮论点逐一反驳。\n"
            f"**禁止重复输出完整报告**，仅输出辩论反驳章节（格式见角色提示词 Round 1+ 部分）。\n\n"
            + stage1_ctx
            + f"\n\n---\n\n**看涨方第{round_idx}轮论点**：\n{bull_last}"
        )


def _format_director_context(stage1: Stage1Results, bull_rounds: list[str], bear_rounds: list[str]) -> str:
    """
    构建研究主管的输入上下文：Stage 1 全量报告 + 完整辩论记录。
    研究主管需要看到全部原始论据才能做出公正裁决。
    """
    stage1_ctx = stage1.format_for_context()
    lines = [stage1_ctx, "\n## 多空辩论完整记录"]

    for i, (bull, bear) in enumerate(zip(bull_rounds, bear_rounds)):
        label = "初始论点" if i == 0 else f"第{i}轮反驳"
        lines.append(f"\n### {label}")
        lines.append(f"**看涨方**：\n{bull}")
        lines.append(f"\n**看跌方**：\n{bear}")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Stage 2 主流程                                                                #
# --------------------------------------------------------------------------- #

async def run_stage2(
    task_id: str,
    stock_code: str,
    stage1_results: Stage1Results,
    packet: CalculatedDataPacket,
    available_tools: set[str],
    market_rules: str,
    llm_client: LLMClient,
    task_semaphore: asyncio.Semaphore,
    cancel_event: asyncio.Event,
    debate_rounds: int = 2,
) -> Stage2Results:
    """
    Stage 2：多空辩论 → 研究主管裁决 → 交易计划师。

    Round 0（并行）：看涨/看跌同时输出初始完整报告
    Rounds 1..N（串行）：交替反驳，仅输出辩论章节（不重复完整报告）
      - 看涨先，看跌后
      - 每轮上下文含显式轮次标记，LLM 据此判断使用哪种输出模式
    研究主管：收到 Stage 1 全量报告 + 完整辩论记录
    交易计划师：收到 Stage 1 全量报告 + 研究主管裁决报告 + 行情快照工具

    支持断点恢复：已完成的轮次/智能体输出从磁盘加载，跳过重新运行。
    关键串行智能体（研究主管、交易计划师）支持自动重试。
    """
    append_task_log(task_id, "[Stage2] 启动多空辩论流程")

    bull = BaseAgent("bull_researcher", llm_client, task_semaphore, cancel_event)
    bear = BaseAgent("bear_researcher", llm_client, task_semaphore, cancel_event)
    director = BaseAgent("research_director", llm_client, task_semaphore, cancel_event)
    planner = BaseAgent("trading_planner", llm_client, task_semaphore, cancel_event)

    bull_rounds: list[str] = []
    bear_rounds: list[str] = []

    # ── 断点恢复：从磁盘加载已完成的辩论轮次 ─────────────────────────────────
    i = 0
    while True:
        cached_bull = load_agent_output(task_id, stock_code, f"stage2_bull_r{i}.md")
        cached_bear = load_agent_output(task_id, stock_code, f"stage2_bear_r{i}.md")
        if cached_bull is None or cached_bear is None:
            break
        bull_rounds.append(cached_bull)
        bear_rounds.append(cached_bear)
        i += 1
    if bull_rounds:
        append_task_log(task_id, f"[Stage2] 断点恢复：已加载 {len(bull_rounds)} 轮辩论记录")

    # ── Round 0：并行输出独立报告 ──────────────────────────────────────────────
    if not bull_rounds:  # not yet loaded from disk
        if cancel_event.is_set():
            raise asyncio.CancelledError

        t0 = time.monotonic()
        append_task_log(task_id, "[Stage2] ▶ Round 0：看涨/看跌分析师并行启动（独立报告）")
        bull_r0, bear_r0 = await asyncio.gather(
            bull.run(_make_bull_context(stage1_results, 0, ""), market_rules),
            bear.run(_make_bear_context(stage1_results, 0, ""), market_rules),
        )
        bull_rounds.append(bull_r0)
        bear_rounds.append(bear_r0)
        elapsed = time.monotonic() - t0
        logger.info(f"[Stage2] Round 0 completed: bull={len(bull_r0)}ch bear={len(bear_r0)}ch ({elapsed:.1f}s)")
        append_task_log(task_id, f"[Stage2] ✓ Round 0 完成（看涨{len(bull_r0)}字，看跌{len(bear_r0)}字）")
        save_agent_output(task_id, stock_code, "stage2_bull_r0.md", bull_r0)
        save_agent_output(task_id, stock_code, "stage2_bear_r0.md", bear_r0)
    else:
        append_task_log(task_id, "[Stage2] 断点恢复：Round 0 已从磁盘加载，跳过")

    # ── Rounds 1..N：串行辩论（仅输出反驳章节）────────────────────────────────
    for i in range(1, debate_rounds + 1):
        if i < len(bull_rounds):
            # This round already loaded from disk
            append_task_log(task_id, f"[Stage2] 断点恢复：Round {i} 已从磁盘加载，跳过")
            continue

        if cancel_event.is_set():
            raise asyncio.CancelledError

        # 看涨方先反驳
        t0 = time.monotonic()
        append_task_log(task_id, f"[Stage2] ▶ Round {i}：看涨方反驳（仅辩论章节）")
        bull_ctx = _make_bull_context(stage1_results, i, bear_rounds[-1])
        bull_rebuttal = await bull.run(bull_ctx, market_rules)
        bull_rounds.append(bull_rebuttal)
        elapsed = time.monotonic() - t0
        logger.info(f"[Stage2] Round {i} bull rebuttal: {len(bull_rebuttal)}ch ({elapsed:.1f}s)")
        append_task_log(task_id, f"[Stage2] ✓ Round {i} 看涨方完成（{len(bull_rebuttal)}字）")

        if cancel_event.is_set():
            raise asyncio.CancelledError

        # 看跌方再反驳
        t0 = time.monotonic()
        append_task_log(task_id, f"[Stage2] ▶ Round {i}：看跌方反驳（仅辩论章节）")
        bear_ctx = _make_bear_context(stage1_results, i, bull_rounds[-1])
        bear_rebuttal = await bear.run(bear_ctx, market_rules)
        bear_rounds.append(bear_rebuttal)
        elapsed = time.monotonic() - t0
        logger.info(f"[Stage2] Round {i} bear rebuttal: {len(bear_rebuttal)}ch ({elapsed:.1f}s)")
        append_task_log(task_id, f"[Stage2] ✓ Round {i} 看跌方完成（{len(bear_rebuttal)}字）")

        save_agent_output(task_id, stock_code, f"stage2_bull_r{i}.md", bull_rebuttal)
        save_agent_output(task_id, stock_code, f"stage2_bear_r{i}.md", bear_rebuttal)

    # ── 研究主管裁决（Stage 1 全量 + 完整辩论记录）────────────────────────────
    cached_director = load_agent_output(task_id, stock_code, "stage2_research_director.md")
    if cached_director is not None:
        director_report = cached_director
        append_task_log(task_id, "[Stage2] 断点恢复：研究主管报告已从磁盘加载，跳过")
    else:
        if cancel_event.is_set():
            raise asyncio.CancelledError

        append_task_log(task_id, "[Stage2] ▶ 研究主管 开始综合裁决")
        debate_ctx = _format_director_context(stage1_results, bull_rounds, bear_rounds)
        director_report = await run_with_stage_retry(
            lambda ctx=debate_ctx: director.run(ctx, market_rules),
            "Stage2/research_director",
        )
        save_agent_output(task_id, stock_code, "stage2_research_director.md", director_report)
        append_task_log(task_id, f"[Stage2] ✓ 研究主管裁决完成（{len(director_report)}字）")

    # ── 交易计划师（Stage 1 全量 + 研究主管报告 + 行情快照）─────────────────
    cached_plan = load_agent_output(task_id, stock_code, "stage2_trading_planner.md")
    if cached_plan is not None:
        trading_plan = cached_plan
        append_task_log(task_id, "[Stage2] 断点恢复：交易计划书已从磁盘加载，跳过")
    else:
        if cancel_event.is_set():
            raise asyncio.CancelledError

        append_task_log(task_id, "[Stage2] ▶ 交易计划师 开始制定交易方案")
        snapshot_ctx = inject_tools("trading_planner", packet, available_tools)
        # Save data evidence
        planner_tools = get_agent_config("trading_planner").get("tools", [])
        save_data_evidence(
            task_id, stock_code,
            "stage2_trading_planner_data.md",
            "交易计划师", "trading_planner", "stage2",
            planner_tools, snapshot_ctx,
        )
        stage1_ctx = stage1_results.format_for_context()
        planner_input = (
            stage1_ctx
            + "\n\n---\n\n"
            + "## 研究主管裁决报告\n\n"
            + director_report
            + "\n\n---\n\n"
            + snapshot_ctx
        )
        trading_plan = await run_with_stage_retry(
            lambda inp=planner_input: planner.run(inp, market_rules),
            "Stage2/trading_planner",
        )
        save_agent_output(task_id, stock_code, "stage2_trading_planner.md", trading_plan)
        append_task_log(task_id, f"[Stage2] ✓ 交易计划书完成（{len(trading_plan)}字）")

    append_task_log(task_id, "[Stage2] ✓ 多空辩论流程全部完成")

    return Stage2Results(
        bull_rounds=bull_rounds,
        bear_rounds=bear_rounds,
        director_report=director_report,
        trading_plan=trading_plan,
    )
