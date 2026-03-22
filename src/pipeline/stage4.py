from __future__ import annotations

import asyncio
import logging

from src.agents.base_agent import BaseAgent
from src.agents.config_loader import get_agent_config
from src.agents.llm_client import LLMClient
from src.core.task_store import append_task_log, save_agent_output, save_data_evidence, save_report
from src.data.calculator import CalculatedDataPacket
from src.pipeline.stage1 import Stage1Results
from src.pipeline.stage2 import Stage2Results
from src.pipeline.stage3 import Stage3Results
from src.pipeline.utils import run_with_stage_retry
from src.tools.tool_injector import inject_tools

logger = logging.getLogger(__name__)



def _format_final_context(
    stage1: Stage1Results,
    stage2: Stage2Results,
    stage3: Stage3Results,
    snapshot_ctx: str,
) -> str:
    """
    将所有阶段关键输出格式化为投资顾问的最终输入上下文。

    传入内容（按重要性排序）：
      1. 当前行情快照（报告头部数据参考）
      2. Stage 1 全量分析报告（不截断）
      3. 研究主管裁决报告（方向判断 + 置信度）
      4. 交易计划书（具体交易参数）
      5. 首席风控官最终裁决（批准/有条件/否决 + 调整建议）
      6. 量化风控数据（VaR 等数字摘要）

    注意：投资顾问不需要看多空辩论原文，研究主管报告已是其提炼结论。
    """
    lines = ["# 综合分析汇总（供投资顾问生成最终报告）\n"]

    # 1. 行情快照
    lines.append("## 一、当前行情快照")
    lines.append(snapshot_ctx)

    # 2. Stage 1 全量分析报告
    lines.append("\n## 二、多维度分析报告（Stage 1 输出）")
    stage1_ctx = stage1.format_for_context()
    lines.append(stage1_ctx)

    # 3. 研究主管裁决（完整）
    lines.append("\n## 三、研究主管裁决报告（Stage 2 输出）")
    lines.append(stage2.director_report)

    # 4. 交易计划书（完整）
    lines.append("\n## 四、交易计划书（Stage 2 输出）")
    lines.append(stage2.trading_plan)

    # 5. 首席风控官最终裁决（完整）
    lines.append("\n## 五、首席风控官最终裁决（Stage 3 输出）")
    lines.append(stage3.cro_report)

    # 6. 量化风控数据摘要
    if stage3.var_result and not stage3.var_result.error:
        var = stage3.var_result
        lines.append("\n## 六、量化风控数据摘要（Stage 3 计算）")
        lines.append(
            f"- VaR({var.confidence_level*100:.0f}%, {var.holding_days}日) "
            f"= {var.var_holding_pct:.2f}% / ¥{var.var_amount:,.0f}"
        )
        lines.append(f"- 超过 10% 阈值：{'是 ⚠' if var.exceeds_threshold else '否 ✓'}")

    if stage3.a_share_result:
        a = stage3.a_share_result
        lines.append(
            f"- A股综合风险评分：{a.composite_score:.1f}/100  "
            f"建议：{a.recommendation}"
        )

    return "\n\n".join(lines)


async def run_stage4(
    stock_code: str,
    task_id: str,
    stage1_results: Stage1Results,
    stage2_results: Stage2Results,
    stage3_results: Stage3Results,
    packet: CalculatedDataPacket,
    available_tools: set[str],
    market_rules: str,
    llm_client: LLMClient,
    task_semaphore: asyncio.Semaphore,
    cancel_event: asyncio.Event,
) -> str:
    """Stage 4: investment advisor generates final Markdown report. Returns report file path."""
    if cancel_event.is_set():
        raise asyncio.CancelledError

    # 构建行情快照（投资顾问使用 snapshot_tool）
    snapshot_ctx = inject_tools("investment_advisor", packet, available_tools)

    # 保存数据证据（快照工具输出）
    advisor_tools = get_agent_config("investment_advisor").get("tools", [])
    save_data_evidence(
        task_id, stock_code,
        "stage4_investment_advisor_data.md",
        "投资顾问", "investment_advisor", "stage4",
        advisor_tools, snapshot_ctx,
    )

    final_ctx = _format_final_context(stage1_results, stage2_results, stage3_results, snapshot_ctx)

    append_task_log(task_id, "[Stage4] ▶ 投资顾问 开始生成最终报告")
    advisor = BaseAgent("investment_advisor", llm_client, task_semaphore, cancel_event)

    report_content = await run_with_stage_retry(
        lambda ctx=final_ctx: advisor.run(ctx, market_rules),
        "Stage4/investment_advisor",
    )

    logger.info(f"[Stage4] Investment advisor report: {len(report_content)}ch")
    append_task_log(task_id, f"[Stage4] ✓ 投资顾问报告完成（{len(report_content)}字）")

    # 保存 Agent 原始输出
    save_agent_output(task_id, stock_code, "stage4_investment_advisor.md", report_content)

    # 保存最终报告到任务根目录（report.md）
    report_path = save_report(task_id, stock_code, report_content)
    logger.info(f"[Stage4] Report saved: {report_path}")
    append_task_log(task_id, f"[Stage4] ✓ 报告已保存 → {report_path}")

    return report_path


async def write_suspended_report(
    stock_code: str,
    task_id: str,
    suspend_reason: str | None,
) -> str:
    """停牌时写入简单报告，返回路径。"""
    from datetime import datetime

    content = f"""# {stock_code} 分析报告

## 当前状态：停牌

> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 任务ID：{task_id}

该股票当前处于停牌状态{f'（类型：{suspend_reason}）' if suspend_reason else ''}，系统无法进行正常的多维度分析。

**交易建议：禁止交易**

请等待股票复牌后重新提交分析任务。
"""
    report_path = save_report(task_id, stock_code, content)
    return report_path
