from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from src.agents.base_agent import BaseAgent
from src.agents.llm_client import LLMClient
from src.core.task_store import append_task_log, save_agent_output, load_agent_output, save_data_evidence
from src.data.calculator import CalculatedDataPacket
from src.pipeline.stage1 import Stage1Results
from src.pipeline.stage2 import Stage2Results
from src.pipeline.utils import run_with_stage_retry
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


# --------------------------------------------------------------------------- #
# 上下文格式化                                                                   #
# --------------------------------------------------------------------------- #

def _format_risk_manager_context(
    stage1: Stage1Results,
    director_report: str,
    trading_plan: str,
    risk_ctx: str,
) -> str:
    """
    构建三位风控师的共同输入上下文。

    包含：
      1. Stage 1 全量分析报告（提供背景认知）
      2. 研究主管裁决报告（提供方向判断和置信度依据）
      3. 交易计划书（待评估的具体交易参数）
      4. 量化风险指标（VaR / A股特有风险等）

    风控师的职责是评估交易计划的风险，理解交易背后的逻辑（Stage 1 + 研究主管）
    是做出专业风险判断的必要前提，不应只看数字而不了解基本面/技术面背景。
    """
    stage1_ctx = stage1.format_for_context()

    return "\n\n".join([
        "## 一、Stage 1 分析背景摘要",
        stage1_ctx,
        "## 二、研究主管裁决报告",
        director_report,
        "## 三、交易计划书（待风险评估）",
        trading_plan,
        "## 四、量化风险指标",
        risk_ctx,
    ])


def _format_cro_context(
    stage1: Stage1Results,
    director_report: str,
    agg: str,
    con: str,
    qnt: str,
    trading_plan: str,
    risk_ctx: str,
) -> str:
    """
    构建首席风控官的输入上下文。

    CRO 需要 Stage 1 全量报告和研究主管报告作为事实依据，
    以便验证三位风控师意见的准确性和完整性。
    """
    stage1_ctx = stage1.format_for_context()
    return "\n\n".join([
        "## 一、Stage 1 分析报告",
        stage1_ctx,
        "## 二、研究主管裁决报告",
        director_report,
        "## 三、交易计划书",
        trading_plan,
        "## 四、量化风险指标",
        risk_ctx,
        "## 五、三位风控师意见",
        f"### 激进风控师\n{agg}",
        f"### 保守风控师\n{con}",
        f"### 量化风控师\n{qnt}",
    ])


# --------------------------------------------------------------------------- #
# Stage 3 主流程                                                                #
# --------------------------------------------------------------------------- #

async def run_stage3(
    task_id: str,
    stage1_results: Stage1Results,
    stage2_results: Stage2Results,
    packet: CalculatedDataPacket,
    stock_code: str,
    market_rules: str,
    llm_client: LLMClient,
    task_semaphore: asyncio.Semaphore,
    cancel_event: asyncio.Event,
) -> Stage3Results | SuspendedResult:
    """
    Stage 3：风控流程。

    Step 0（纯代码）：停牌前置检查
    Step 1（纯代码）：VaR 计算 + A股特有风险评分
    Step 2（并行 LLM）：三位风控师独立评估
      输入：Stage 1 全量报告 + 研究主管报告 + 交易计划书 + 量化风险指标
    Step 3（串行 LLM）：首席风控官综合裁决
      输入：Stage 1 全量报告 + 研究主管报告 + 交易计划书 + 量化风险指标 + 三位风控师意见
    """
    from src.config import settings

    # Step 0：停牌前置检查
    suspension = check_suspension(packet)
    if suspension.is_suspended:
        logger.info(f"[Stage3] Stock {stock_code} is suspended, skipping AI risk agents")
        return SuspendedResult(reason=suspension.suspend_type)

    # Step 1：量化风控计算（纯代码，始终执行）
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

    # 保存风控经理组的数据证据（纯量化计算，无数据工具）
    save_data_evidence(
        task_id, stock_code,
        "stage3_risk_managers_data.md",
        "风控经理组（激进/保守/量化）", "risk_managers", "stage3",
        [],  # no data tools — pure quantitative calculation
        risk_ctx,
    )

    if cancel_event.is_set():
        raise asyncio.CancelledError

    # Step 2：三位风控师并行（共享同一份上下文，但各自独立分析）
    # 始终构建 rm_ctx，以便 CRO 在断点恢复时也能拿到 cro_ctx
    rm_ctx = _format_risk_manager_context(
        stage1=stage1_results,
        director_report=stage2_results.director_report,
        trading_plan=stage2_results.trading_plan,
        risk_ctx=risk_ctx,
    )

    cached_agg = load_agent_output(task_id, stock_code, "stage3_aggressive_risk_manager.md")
    cached_con = load_agent_output(task_id, stock_code, "stage3_conservative_risk_manager.md")
    cached_qnt = load_agent_output(task_id, stock_code, "stage3_quant_risk_manager.md")

    if cached_agg and cached_con and cached_qnt:
        agg_r, con_r, qnt_r = cached_agg, cached_con, cached_qnt
        append_task_log(task_id, "[Stage3] 断点恢复：3 位风控经理报告已从磁盘加载，跳过")
    else:
        append_task_log(task_id, "[Stage3] ▶ 启动 3 位风控经理并行分析")

        agg_rm = BaseAgent("aggressive_risk_manager", llm_client, task_semaphore, cancel_event)
        con_rm = BaseAgent("conservative_risk_manager", llm_client, task_semaphore, cancel_event)
        qnt_rm = BaseAgent("quant_risk_manager", llm_client, task_semaphore, cancel_event)

        agg_r, con_r, qnt_r = await asyncio.gather(
            agg_rm.run(rm_ctx, market_rules),
            con_rm.run(rm_ctx, market_rules),
            qnt_rm.run(rm_ctx, market_rules),
            return_exceptions=True,
        )

        def _extract(result, name: str) -> str:
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                logger.error(f"[Stage3] {name} failed: {result}")
                return f"[{name}执行失败：{result}，本维度不可用]"
            return result

        agg_r = _extract(agg_r, "aggressive_risk_manager")
        con_r = _extract(con_r, "conservative_risk_manager")
        qnt_r = _extract(qnt_r, "quant_risk_manager")

        save_agent_output(task_id, stock_code, "stage3_aggressive_risk_manager.md", agg_r)
        save_agent_output(task_id, stock_code, "stage3_conservative_risk_manager.md", con_r)
        save_agent_output(task_id, stock_code, "stage3_quant_risk_manager.md", qnt_r)

        logger.info(f"[Stage3] Three risk managers completed: {len(agg_r)}/{len(con_r)}/{len(qnt_r)} chars")
        append_task_log(
            task_id,
            f"[Stage3] ✓ 3 位风控经理完成（激进{len(agg_r)}字/保守{len(con_r)}字/量化{len(qnt_r)}字）",
        )

    # Step 3：首席风控官综合裁决
    cached_cro = load_agent_output(task_id, stock_code, "stage3_chief_risk_officer.md")
    if cached_cro is not None:
        cro_report = cached_cro
        append_task_log(task_id, "[Stage3] 断点恢复：首席风控官报告已从磁盘加载，跳过")
    else:
        if cancel_event.is_set():
            raise asyncio.CancelledError
        append_task_log(task_id, "[Stage3] ▶ 首席风控官 开始综合裁决")
        cro = BaseAgent("chief_risk_officer", llm_client, task_semaphore, cancel_event)
        cro_ctx = _format_cro_context(
            stage1=stage1_results,
            director_report=stage2_results.director_report,
            agg=agg_r,
            con=con_r,
            qnt=qnt_r,
            trading_plan=stage2_results.trading_plan,
            risk_ctx=risk_ctx,
        )
        cro_report = await run_with_stage_retry(
            lambda ctx=cro_ctx: cro.run(ctx, market_rules),
            "Stage3/chief_risk_officer",
        )
        save_agent_output(task_id, stock_code, "stage3_chief_risk_officer.md", cro_report)
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
