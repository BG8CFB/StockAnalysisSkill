from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from src.agents.base_agent import BaseAgent
from src.agents.llm_client import LLMClient
from src.core.task_store import append_task_log
from src.data.calculator import CalculatedDataPacket
from src.pipeline.stage2 import Stage2Results
from src.tools.risk_calculator import (
    VaRResult, AShareRiskResult,
    check_suspension, calculate_var, calculate_a_share_risk, format_risk_results,
)

logger = logging.getLogger(__name__)


@dataclass
class Stage3Results:
    aggressive: str = ""
    conservative: str = ""
    quant: str = ""
    cro_report: str = ""
    var_result: Optional[VaRResult] = None
    a_share_result: Optional[AShareRiskResult] = None


@dataclass
class SuspendedResult:
    reason: Optional[str] = None


def _format_three_rm_context(
    agg: str,
    con: str,
    qnt: str,
    trading_plan: str,
    risk_ctx: str,
) -> str:
    return "\n\n".join([
        "## 交易计划书",
        trading_plan,
        "## 风险量化指标",
        risk_ctx,
        "## 三位风控师意见",
        f"### 激进风控师\n{agg}",
        f"### 保守风控师\n{con}",
        f"### 量化风控师\n{qnt}",
    ])


async def run_stage3(
    task_id: str,
    stage2_results: Stage2Results,
    packet: CalculatedDataPacket,
    stock_code: str,
    market_rules: str,
    skills_list: str,
    llm_client: LLMClient,
    task_semaphore: asyncio.Semaphore,
    cancel_event: asyncio.Event,
) -> Stage3Results | SuspendedResult:
    """
    Stage 3：风控流程。

    Step 0（纯代码）：停牌前置检查
    Step 1（纯代码）：VaR 计算 + A股特有风险评分
    Step 2（并行 LLM）：三位风控师
    Step 3（串行 LLM）：首席风控官综合裁决
    """
    from src.config import settings

    # Step 0：停牌前置检查
    suspension = check_suspension(packet)
    if suspension.is_suspended:
        logger.info(f"[Stage3] Stock {stock_code} is suspended, skipping AI risk agents")
        return SuspendedResult(reason=suspension.suspend_type)

    # Step 1：量化风控计算（纯代码）
    append_task_log(task_id, "[Stage3] ▶ VaR 计算开始")
    var_result = calculate_var(packet, settings.analysis_capital_base)
    logger.info(
        f"[Stage3] VaR({var_result.confidence_level*100:.0f}%, {var_result.holding_days}d) = "
        f"{var_result.var_holding_pct:.2f}% / ¥{var_result.var_amount:,.0f}"
    )
    append_task_log(
        task_id,
        f"[Stage3] ✓ VaR({var_result.confidence_level*100:.0f}%, {var_result.holding_days}日)"
        f" = {var_result.var_holding_pct:.2f}% / ¥{var_result.var_amount:,.0f}",
    )

    code_upper = stock_code.upper()
    a_share_result = None
    if code_upper.endswith((".SZ", ".SH")):
        a_share_result = calculate_a_share_risk(packet)
        logger.info(
            f"[Stage3] A-share risk composite={a_share_result.composite_score:.1f} "
            f"recommendation={a_share_result.recommendation}"
        )

    risk_ctx = format_risk_results(var_result, a_share_result)

    if cancel_event.is_set():
        raise asyncio.CancelledError

    # Step 2：三位风控师并行
    append_task_log(task_id, "[Stage3] ▶ 启动 3 位风控经理并行分析")
    trading_plan_ctx = stage2_results.trading_plan
    full_ctx = trading_plan_ctx + "\n\n---\n\n" + risk_ctx

    agg_rm = BaseAgent("aggressive_risk_manager", llm_client, task_semaphore, cancel_event)
    con_rm = BaseAgent("conservative_risk_manager", llm_client, task_semaphore, cancel_event)
    qnt_rm = BaseAgent("quant_risk_manager", llm_client, task_semaphore, cancel_event)

    agg_r, con_r, qnt_r = await asyncio.gather(
        agg_rm.run(full_ctx, market_rules, skills_list),
        con_rm.run(full_ctx, market_rules, skills_list),
        qnt_rm.run(full_ctx, market_rules, skills_list),
        return_exceptions=True,
    )

    def _extract(result, name: str) -> str:
        if isinstance(result, Exception):
            logger.error(f"[Stage3] {name} failed: {result}")
            return f"[{name}执行失败：{result}，本维度不可用]"
        if isinstance(result, asyncio.CancelledError):
            raise result
        return result

    agg_r = _extract(agg_r, "aggressive_risk_manager")
    con_r = _extract(con_r, "conservative_risk_manager")
    qnt_r = _extract(qnt_r, "quant_risk_manager")

    logger.info(f"[Stage3] Three risk managers completed: {len(agg_r)}/{len(con_r)}/{len(qnt_r)} chars")
    append_task_log(
        task_id,
        f"[Stage3] ✓ 3 位风控经理完成（激进{len(agg_r)}字/保守{len(con_r)}字/量化{len(qnt_r)}字）",
    )

    if cancel_event.is_set():
        raise asyncio.CancelledError

    # Step 3：首席风控官综合裁决（串行）
    append_task_log(task_id, "[Stage3] ▶ 首席风控官 开始综合裁决")
    cro = BaseAgent("chief_risk_officer", llm_client, task_semaphore, cancel_event)
    cro_ctx = _format_three_rm_context(agg_r, con_r, qnt_r, trading_plan_ctx, risk_ctx)
    cro_report = await cro.run(cro_ctx, market_rules, skills_list)
    logger.info(f"[Stage3] CRO report: {len(cro_report)}ch")
    append_task_log(task_id, f"[Stage3] ✓ 首席风控官裁决完成（{len(cro_report)}字）")

    return Stage3Results(
        aggressive=agg_r,
        conservative=con_r,
        quant=qnt_r,
        cro_report=cro_report,
        var_result=var_result,
        a_share_result=a_share_result,
    )
