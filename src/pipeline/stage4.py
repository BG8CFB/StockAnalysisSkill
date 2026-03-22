from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from src.agents.base_agent import BaseAgent
from src.agents.llm_client import LLMClient
from src.core.task_store import append_task_log
from src.data.calculator import CalculatedDataPacket
from src.pipeline.stage1 import Stage1Results
from src.pipeline.stage2 import Stage2Results
from src.pipeline.stage3 import Stage3Results
from src.tools.tool_injector import inject_tools

logger = logging.getLogger(__name__)


def _format_final_context(
    stage1: Stage1Results,
    stage2: Stage2Results,
    stage3: Stage3Results,
    snapshot_ctx: str,
) -> str:
    """将所有阶段输出格式化为投资顾问的最终输入上下文。"""
    lines = ["# 综合分析汇总（供投资顾问生成最终报告）\n"]

    # 1. 行情快照
    lines.append("## 一、当前行情快照")
    lines.append(snapshot_ctx)

    # 2. Stage 1 六维评分摘要（简表）
    lines.append("\n## 二、六维分析摘要")
    lines.append("（各维度完整报告见下方，此处为快速摘要）")
    lines.append(f"- **技术分析**（技术分析师）：{stage1.technical[:300]}...")
    lines.append(f"- **基本面分析**（基本面分析师）：{stage1.fundamental[:300]}...")
    lines.append(f"- **微观结构**（市场微观结构分析师）：{stage1.microstructure[:300]}...")
    lines.append(f"- **市场情绪**（情绪分析师）：{stage1.sentiment[:300]}...")
    lines.append(f"- **板块轮动**（板块分析师）：{stage1.sector[:300]}...")
    lines.append(f"- **资讯事件**（资讯分析师）：{stage1.news[:300]}...")

    # 3. Stage 2 交易计划书（完整）
    lines.append("\n## 三、交易计划书（Stage 2 输出）")
    lines.append(stage2.trading_plan)

    # 4. Stage 3 CRO 最终裁决（完整）
    lines.append("\n## 四、首席风控官最终裁决（Stage 3 输出）")
    lines.append(stage3.cro_report)

    # 5. 风控计算数据
    if stage3.var_result and not stage3.var_result.error:
        var = stage3.var_result
        lines.append("\n## 五、量化风控数据")
        lines.append(f"- VaR({var.confidence_level*100:.0f}%, {var.holding_days}日) = {var.var_holding_pct:.2f}% / ¥{var.var_amount:,.0f}")
        lines.append(f"- 是否超过10%阈值：{'是 ⚠️' if var.exceeds_threshold else '否 ✅'}")

    if stage3.a_share_result:
        a = stage3.a_share_result
        lines.append(f"- A股综合风险评分：{a.composite_score:.1f}/100  建议：{a.recommendation}")

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
    skills_list: str,
    llm_client: LLMClient,
    task_semaphore: asyncio.Semaphore,
    cancel_event: asyncio.Event,
) -> str:
    """
    Stage 4：投资顾问生成最终 Markdown 报告，写入文件。
    返回报告文件路径。
    """
    from src.config import settings

    if cancel_event.is_set():
        raise asyncio.CancelledError

    # 构建价格快照
    snapshot_ctx = inject_tools("investment_advisor", packet, available_tools)
    final_ctx = _format_final_context(stage1_results, stage2_results, stage3_results, snapshot_ctx)

    # 调用投资顾问
    append_task_log(task_id, "[Stage4] ▶ 投资顾问 开始生成最终报告")
    advisor = BaseAgent("investment_advisor", llm_client, task_semaphore, cancel_event)
    report_content = await advisor.run(final_ctx, market_rules, skills_list)
    logger.info(f"[Stage4] Investment advisor report: {len(report_content)}ch")
    append_task_log(task_id, f"[Stage4] ✓ 投资顾问报告完成（{len(report_content)}字）")

    # 写入文件
    date_str = datetime.now().strftime("%Y%m%d")
    report_dir = settings.reports_dir / date_str
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{stock_code}_{task_id}.md"
    report_path.write_text(report_content, encoding="utf-8")
    logger.info(f"[Stage4] Report saved: {report_path}")
    append_task_log(task_id, f"[Stage4] ✓ 报告已保存 → {report_path}")

    return str(report_path)


async def write_suspended_report(
    stock_code: str,
    task_id: str,
    suspend_reason: str | None,
) -> str:
    """停牌时写入简单报告，返回路径。"""
    from src.config import settings

    content = f"""# {stock_code} 分析报告

## 当前状态：停牌

> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 任务ID：{task_id}

该股票当前处于停牌状态{f'（类型：{suspend_reason}）' if suspend_reason else ''}，
系统无法进行正常的多维度分析。

**交易建议：禁止交易**

请等待股票复牌后重新提交分析任务。
"""
    date_str = datetime.now().strftime("%Y%m%d")
    report_dir = settings.reports_dir / date_str
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{stock_code}_{task_id}_suspended.md"
    report_path.write_text(content, encoding="utf-8")
    return str(report_path)
