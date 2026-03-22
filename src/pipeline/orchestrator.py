from __future__ import annotations

import asyncio
import time

from loguru import logger

from src.agents.llm_client import LLMClient
from src.core.models import TaskStatus, StageStatus
from src.core.task_store import update_task, append_task_log


async def run_pipeline(
    task_id: str,
    stock_code: str,
    cancel_event: asyncio.Event,
    task_semaphore: asyncio.Semaphore,
    llm_client: LLMClient,
) -> None:
    """
    主流水线入口。由 scheduler worker 调用。
    驱动四个阶段，写进度，处理取消/失败。
    """
    from src.data.tushare_adapter import fetch_all as tushare_fetch
    from src.data.akshare_adapter import merge_with_tushare
    from src.data.cleaner import clean
    from src.data.calculator import calculate
    from src.data.market_rules import get_market_rules
    from src.tools.skills_loader import load_skills_list
    from src.pipeline.stage1 import run_stage1
    from src.pipeline.stage2 import run_stage2
    from src.pipeline.stage3 import run_stage3, SuspendedResult
    from src.pipeline.stage4 import run_stage4, write_suspended_report

    with logger.contextualize(task_id=task_id, stock_code=stock_code):
        t_start = time.monotonic()

        try:
            # 更新状态 → RUNNING
            update_task(task_id, status=TaskStatus.RUNNING, current_stage="data_fetch")
            logger.info(f"[Pipeline] 任务启动，股票: {stock_code}")
            append_task_log(task_id, f"[Pipeline] 任务启动，股票: {stock_code}")

            # ── 数据获取 ─────────────────────────────────────────────────────────
            append_task_log(task_id, "[数据] 开始拉取市场数据")
            raw, available = await tushare_fetch(stock_code)
            # AkShare 补充缺失字段（仅当启用时）
            from src.config import settings as _settings
            if _settings.akshare_enabled:
                raw, available = await merge_with_tushare(raw, available, stock_code)
            logger.info(f"[Pipeline] 数据拉取完成，可用工具: {available}")
            append_task_log(task_id, f"[数据] 数据拉取完成，可用工具: {len(available)} 个")

            # ── 数据清洗 ─────────────────────────────────────────────────────────
            packet_clean = clean(raw, available)

            # 停牌直接结束
            if packet_clean.is_suspended:
                logger.info(f"[Pipeline] {stock_code} 检测到停牌，生成停牌报告")
                append_task_log(task_id, "[Pipeline] ⚠ 检测到停牌，生成停牌报告")
                report_path = await write_suspended_report(stock_code, task_id, None)
                update_task(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    current_stage=None,
                    stage_progress={
                        "stage1": StageStatus.SKIPPED,
                        "stage2": StageStatus.SKIPPED,
                        "stage3": StageStatus.SKIPPED,
                        "stage4": StageStatus.SKIPPED,
                    },
                    report_path=report_path,
                )
                return

            # ── 计算指标 ─────────────────────────────────────────────────────────
            packet = calculate(packet_clean)
            logger.info(f"[Pipeline] 指标计算完成，股票: {stock_code}")

            # ── 加载市场规则和技能列表（所有阶段复用）────────────────────────────
            market_rules = get_market_rules(stock_code)
            skills_list = load_skills_list()

            # ── Stage 1 ───────────────────────────────────────────────────────────
            if cancel_event.is_set():
                raise asyncio.CancelledError

            update_task(
                task_id,
                current_stage="stage1",
                stage_progress={"stage1": StageStatus.RUNNING},
            )
            stage1_results = await run_stage1(
                task_id=task_id,
                stock_code=stock_code,
                packet=packet,
                available_tools=packet.available_tools,
                market_rules=market_rules,
                skills_list=skills_list,
                llm_client=llm_client,
                task_semaphore=task_semaphore,
                cancel_event=cancel_event,
            )
            update_task(
                task_id,
                current_stage="stage2",
                stage_progress={"stage1": StageStatus.COMPLETED, "stage2": StageStatus.RUNNING},
            )
            logger.info(f"[Pipeline] Stage 1 完成，股票: {stock_code}")

            # ── Stage 2 ───────────────────────────────────────────────────────────
            if cancel_event.is_set():
                raise asyncio.CancelledError

            from src.config import settings
            stage2_results = await run_stage2(
                task_id=task_id,
                stage1_results=stage1_results,
                packet=packet,
                available_tools=packet.available_tools,
                market_rules=market_rules,
                skills_list=skills_list,
                llm_client=llm_client,
                task_semaphore=task_semaphore,
                cancel_event=cancel_event,
                debate_rounds=settings.debate_rounds,
            )
            update_task(
                task_id,
                current_stage="stage3",
                stage_progress={"stage2": StageStatus.COMPLETED, "stage3": StageStatus.RUNNING},
            )
            logger.info(f"[Pipeline] Stage 2 完成，股票: {stock_code}")

            # ── Stage 3 ───────────────────────────────────────────────────────────
            if cancel_event.is_set():
                raise asyncio.CancelledError

            stage3_result = await run_stage3(
                task_id=task_id,
                stage2_results=stage2_results,
                packet=packet,
                stock_code=stock_code,
                market_rules=market_rules,
                skills_list=skills_list,
                llm_client=llm_client,
                task_semaphore=task_semaphore,
                cancel_event=cancel_event,
            )

            # Stage 3 停牌结果（二次检测）
            if isinstance(stage3_result, SuspendedResult):
                logger.info(f"[Pipeline] Stage3 检测到停牌，生成停牌报告")
                append_task_log(task_id, "[Pipeline] ⚠ Stage3 检测到停牌，生成停牌报告")
                report_path = await write_suspended_report(stock_code, task_id, stage3_result.reason)
                update_task(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    current_stage=None,
                    stage_progress={
                        "stage1": StageStatus.COMPLETED,
                        "stage2": StageStatus.COMPLETED,
                        "stage3": StageStatus.SKIPPED,
                        "stage4": StageStatus.SKIPPED,
                    },
                    report_path=report_path,
                )
                return

            update_task(
                task_id,
                current_stage="stage4",
                stage_progress={"stage3": StageStatus.COMPLETED, "stage4": StageStatus.RUNNING},
            )
            logger.info(f"[Pipeline] Stage 3 完成，股票: {stock_code}")

            # ── Stage 4 ───────────────────────────────────────────────────────────
            if cancel_event.is_set():
                raise asyncio.CancelledError

            report_path = await run_stage4(
                stock_code=stock_code,
                task_id=task_id,
                stage1_results=stage1_results,
                stage2_results=stage2_results,
                stage3_results=stage3_result,
                packet=packet,
                available_tools=packet.available_tools,
                market_rules=market_rules,
                skills_list=skills_list,
                llm_client=llm_client,
                task_semaphore=task_semaphore,
                cancel_event=cancel_event,
            )

            update_task(
                task_id,
                status=TaskStatus.COMPLETED,
                current_stage=None,
                stage_progress={"stage4": StageStatus.COMPLETED},
                report_path=report_path,
            )
            elapsed = time.monotonic() - t_start
            elapsed_str = f"{int(elapsed // 60)}m{int(elapsed % 60)}s"
            logger.info(f"[Pipeline] 分析完成，总耗时 {elapsed_str}，报告: {report_path}")
            append_task_log(task_id, f"[Pipeline] ✓ 分析完成，总耗时 {elapsed_str}")

        except asyncio.CancelledError:
            logger.info(f"[Pipeline] 任务已取消，stock_code={stock_code}")
            append_task_log(task_id, "[Pipeline] 任务已取消")
            update_task(task_id, status=TaskStatus.CANCELLED, current_stage=None)

        except Exception as e:
            logger.exception(f"[Pipeline] 任务失败，stock_code={stock_code} — {e}")
            append_task_log(task_id, f"[Pipeline] ✗ 任务失败: {e}")
            update_task(task_id, status=TaskStatus.FAILED, current_stage=None, error=str(e))
