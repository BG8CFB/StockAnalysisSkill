from __future__ import annotations

import asyncio
import time

from loguru import logger

from src.agents.llm_client import LLMClient
from src.core.models import TaskStatus, StageStatus, PipelineConfig
from src.core.task_store import (
    update_task, append_task_log, get_task,
    load_agent_output,
)


# --------------------------------------------------------------------------- #
# 磁盘结果重建（断点续跑用）                                                     #
# --------------------------------------------------------------------------- #

def _load_stage1_from_disk(task_id: str, stock_code: str):
    """从磁盘重建 Stage1Results（阶段已完成时调用）。"""
    from src.pipeline.stage1 import Stage1Results
    from src.agents.config_loader import get_stage1_agents

    analysts = get_stage1_agents()
    results = Stage1Results(display_names={aid: dname for aid, dname in analysts})
    for agent_id, display_name in analysts:
        content = load_agent_output(task_id, stock_code, f"stage1_{agent_id}.md")
        results.reports[agent_id] = content or f"[{display_name} 磁盘文件缺失，本维度不可用]"
    return results


def _load_stage2_from_disk(task_id: str, stock_code: str):
    """从磁盘重建 Stage2Results（阶段已完成时调用）。"""
    from src.pipeline.stage2 import Stage2Results

    bull_rounds: list[str] = []
    bear_rounds: list[str] = []
    i = 0
    while True:
        bull = load_agent_output(task_id, stock_code, f"stage2_bull_r{i}.md")
        bear = load_agent_output(task_id, stock_code, f"stage2_bear_r{i}.md")
        if bull is None or bear is None:
            break
        bull_rounds.append(bull)
        bear_rounds.append(bear)
        i += 1

    director = load_agent_output(task_id, stock_code, "stage2_research_director.md") or ""
    trading_plan = load_agent_output(task_id, stock_code, "stage2_trading_planner.md") or ""

    return Stage2Results(
        bull_rounds=bull_rounds,
        bear_rounds=bear_rounds,
        director_report=director,
        trading_plan=trading_plan,
    )


def _load_stage3_from_disk(task_id: str, stock_code: str, packet):
    """
    从磁盘重建 Stage3Results。
    VaR / A股风险评分为纯代码计算，速度快，直接重算（无需序列化）。
    """
    from src.pipeline.stage3 import Stage3Results
    from src.tools.risk_calculator import calculate_var, calculate_a_share_risk
    from src.config import settings

    var_result = calculate_var(packet, settings.analysis_capital_base)
    a_share_result = None
    if stock_code.upper().endswith((".SZ", ".SH")):
        a_share_result = calculate_a_share_risk(packet)

    aggressive = load_agent_output(task_id, stock_code, "stage3_aggressive_risk_manager.md") or ""
    conservative = load_agent_output(task_id, stock_code, "stage3_conservative_risk_manager.md") or ""
    quant = load_agent_output(task_id, stock_code, "stage3_quant_risk_manager.md") or ""
    cro_report = load_agent_output(task_id, stock_code, "stage3_chief_risk_officer.md") or ""

    return Stage3Results(
        aggressive=aggressive,
        conservative=conservative,
        quant=quant,
        cro_report=cro_report,
        var_result=var_result,
        a_share_result=a_share_result,
    )


# --------------------------------------------------------------------------- #
# 主流水线                                                                      #
# --------------------------------------------------------------------------- #

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
    支持断点续跑：检查 task.stages_completed 跳过已完成阶段。
    """
    from src.data.tushare_adapter import fetch_all as tushare_fetch
    from src.data.akshare_adapter import merge_with_tushare
    from src.data.cleaner import clean
    from src.data.calculator import calculate
    from src.data.market_rules import get_market_rules
    from src.tools.skills_loader import scan_skills
    from src.pipeline.stage1 import run_stage1
    from src.pipeline.stage2 import run_stage2
    from src.pipeline.stage3 import run_stage3, SuspendedResult
    from src.pipeline.stage4 import run_stage4, write_suspended_report
    from src.config import settings

    with logger.contextualize(task_id=task_id, stock_code=stock_code):
        t_start = time.monotonic()

        try:
            # ── 断点续跑检测 ───────────────────────────────────────────────────
            task_record = get_task(task_id, stock_code)
            stages_completed = set(task_record.stages_completed if task_record else [])
            is_resuming = bool(stages_completed)

            # 获取流水线配置
            pipeline_config = task_record.pipeline_config if task_record else PipelineConfig()
            # 配置合并后应该已有确定值，但为兼容历史任务需处理 None 情况
            effective_stage2_enabled = pipeline_config.stage2_enabled if pipeline_config.stage2_enabled is not None else settings.pipeline_stage2_enabled
            effective_stage3_enabled = pipeline_config.get_effective_stage3_enabled(settings.pipeline_stage3_enabled)
            effective_debate_rounds = pipeline_config.get_effective_debate_rounds(settings.debate_rounds)

            if is_resuming:
                new_resume_count = (task_record.resume_count + 1) if task_record else 1
                update_task(
                    task_id, stock_code,
                    status=TaskStatus.RUNNING,
                    resume_count=new_resume_count,
                    current_stage="data_fetch",
                )
                append_task_log(
                    task_id,
                    f"[Pipeline] 断点续跑（第 {new_resume_count} 次），"
                    f"已完成阶段: {sorted(stages_completed)}",
                    stock_code,
                )
                logger.info(
                    f"[Pipeline] 断点续跑 #{new_resume_count}，股票: {stock_code}，"
                    f"已完成阶段: {sorted(stages_completed)}"
                )
            else:
                update_task(task_id, stock_code, status=TaskStatus.RUNNING, current_stage="data_fetch")
                logger.info(f"[Pipeline] 任务启动，股票: {stock_code}")
                append_task_log(task_id, f"[Pipeline] 任务启动，股票: {stock_code}", stock_code)

            # ── 数据获取（每次都重新拉取，保证数据新鲜）────────────────────────
            append_task_log(task_id, "[数据] 开始拉取市场数据", stock_code)
            if settings.tushare_token:
                # 有 Tushare Token：Tushare 为主，AkShare 补充
                raw, available = await tushare_fetch(stock_code)
                # 关键修复：TU Token无效或完全失败时，回退到AkShare主模式
                if not available or len(available) == 0:
                    logger.warning("[Pipeline] Tushare 未返回任何可用工具，回退到 AkShare 主数据源")
                    append_task_log(task_id, "[数据] ⚠ Tushare 无可用数据，回退到 AkShare 主数据源", stock_code)
                    from src.data.akshare_adapter import fetch_all as akshare_fetch_all
                    raw, available = await akshare_fetch_all(stock_code)
                elif settings.akshare_enabled:
                    raw, available = await merge_with_tushare(raw, available, stock_code)
            else:
                # 无 Tushare Token：直接使用 AkShare 作为主数据源
                from src.data.akshare_adapter import fetch_all as akshare_fetch_all
                logger.info("[Pipeline] 未配置 Tushare Token，使用 AkShare 作为主数据源")
                append_task_log(task_id, "[数据] 未配置 Tushare Token，使用 AkShare 主数据源", stock_code)
                raw, available = await akshare_fetch_all(stock_code)

            # ── 宏观数据获取（AKShare）──────────────────────────────────────────
            from src.data.macro_adapter import fetch_macro_data
            try:
                macro_raw, macro_available = await fetch_macro_data(stock_code)
                if macro_raw:
                    raw["macro_data"] = macro_raw
                available.update(macro_available)
                logger.info(f"[Pipeline] 宏观数据获取完成，可用宏观工具: {macro_available}")
                append_task_log(task_id, f"[数据] 宏观数据获取完成，可用工具: {len(macro_available)} 个", stock_code)
            except Exception as e:
                logger.warning(f"[Pipeline] 宏观数据获取失败: {e}")
                append_task_log(task_id, f"[数据] ⚠ 宏观数据获取失败: {e}", stock_code)

            logger.info(f"[Pipeline] 数据拉取完成，可用工具: {available}")
            append_task_log(task_id, f"[数据] 数据拉取完成，可用工具: {len(available)} 个", stock_code)

            # ── 数据清洗 ──────────────────────────────────────────────────────
            packet_clean = clean(raw, available)

            # 停牌直接结束
            if packet_clean.is_suspended:
                logger.info(f"[Pipeline] {stock_code} 检测到停牌，生成停牌报告")
                append_task_log(task_id, "[Pipeline] ⚠ 检测到停牌，生成停牌报告", stock_code)
                report_path = await write_suspended_report(stock_code, task_id, None)
                update_task(
                    task_id, stock_code,
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

            # ── 计算指标 ──────────────────────────────────────────────────────
            packet = calculate(packet_clean)
            logger.info(f"[Pipeline] 指标计算完成，股票: {stock_code}")

            # ── 加载市场规则 + 预扫描技能（所有阶段复用）────────────────────
            market_rules = get_market_rules(stock_code)
            scan_skills()  # 预加载技能元数据，后续 BaseAgent 按需使用

            # ── Stage 1 ───────────────────────────────────────────────────────
            if cancel_event.is_set():
                raise asyncio.CancelledError

            if "stage1" in stages_completed:
                logger.info("[Pipeline] 断点续跑：Stage1 已完成，从磁盘加载")
                append_task_log(task_id, "[Stage1] 断点恢复：已完成，从磁盘加载", stock_code)
                stage1_results = _load_stage1_from_disk(task_id, stock_code)
            else:
                update_task(
                    task_id, stock_code,
                    current_stage="stage1",
                    stage_progress={"stage1": StageStatus.RUNNING},
                )
                stage1_results = await run_stage1(
                    task_id=task_id,
                    stock_code=stock_code,
                    packet=packet,
                    available_tools=packet.available_tools,
                    market_rules=market_rules,
                    stage1_agents=pipeline_config.stage1_agents,
                    llm_client=llm_client,
                    task_semaphore=task_semaphore,
                    cancel_event=cancel_event,
                )
                update_task(
                    task_id, stock_code,
                    current_stage="stage2",
                    stage_progress={"stage1": StageStatus.COMPLETED, "stage2": StageStatus.RUNNING},
                )
                logger.info(f"[Pipeline] Stage 1 完成，股票: {stock_code}")

            # ── Stage 2 ───────────────────────────────────────────────────────
            if cancel_event.is_set():
                raise asyncio.CancelledError

            if not effective_stage2_enabled:
                # Stage 2 被禁用，生成空结果
                from src.pipeline.stage2 import Stage2Results
                stage2_results = Stage2Results(
                    bull_rounds=[],
                    bear_rounds=[],
                    director_report="（Stage 2 多空辩论已禁用）",
                    trading_plan="（Stage 2 已禁用，无交易计划）",
                )
                logger.info("[Pipeline] Stage 2 已禁用，跳过")
                append_task_log(task_id, "[Pipeline] Stage 2 已禁用，跳过", stock_code)
                update_task(
                    task_id, stock_code,
                    current_stage="stage3",
                    stage_progress={"stage2": StageStatus.SKIPPED, "stage3": StageStatus.RUNNING},
                )
            elif "stage2" in stages_completed:
                logger.info("[Pipeline] 断点续跑：Stage2 已完成，从磁盘加载")
                append_task_log(task_id, "[Stage2] 断点恢复：已完成，从磁盘加载", stock_code)
                stage2_results = _load_stage2_from_disk(task_id, stock_code)
            else:
                if "stage1" in stages_completed:
                    # 本次运行从 stage2 开始，需先更新进度
                    update_task(
                        task_id, stock_code,
                        current_stage="stage2",
                        stage_progress={"stage2": StageStatus.RUNNING},
                    )
                stage2_results = await run_stage2(
                    task_id=task_id,
                    stock_code=stock_code,
                    stage1_results=stage1_results,
                    packet=packet,
                    available_tools=packet.available_tools,
                    market_rules=market_rules,
                    llm_client=llm_client,
                    task_semaphore=task_semaphore,
                    cancel_event=cancel_event,
                    debate_rounds=effective_debate_rounds,
                )
                update_task(
                    task_id, stock_code,
                    current_stage="stage3",
                    stage_progress={"stage2": StageStatus.COMPLETED, "stage3": StageStatus.RUNNING},
                )
                logger.info(f"[Pipeline] Stage 2 完成，股票: {stock_code}")

            # ── Stage 3 ───────────────────────────────────────────────────────
            if cancel_event.is_set():
                raise asyncio.CancelledError

            if not effective_stage3_enabled:
                # Stage 3 被禁用（或 Stage 2 被禁用导致强制禁用），生成空结果
                from src.pipeline.stage3 import Stage3Results
                from src.tools.risk_calculator import calculate_var, calculate_a_share_risk
                var_result = calculate_var(packet, settings.analysis_capital_base)
                a_share_result = None
                if stock_code.upper().endswith((".SZ", ".SH")):
                    a_share_result = calculate_a_share_risk(packet)
                stage3_result = Stage3Results(
                    aggressive="（Stage 3 风控已禁用）",
                    conservative="（Stage 3 风控已禁用）",
                    quant="（Stage 3 风控已禁用）",
                    cro_report="（Stage 3 风控已禁用，无 CRO 报告）",
                    var_result=var_result,
                    a_share_result=a_share_result,
                )
                logger.info("[Pipeline] Stage 3 已禁用，跳过")
                append_task_log(task_id, "[Pipeline] Stage 3 已禁用，跳过", stock_code)
                update_task(
                    task_id, stock_code,
                    current_stage="stage4",
                    stage_progress={"stage3": StageStatus.SKIPPED, "stage4": StageStatus.RUNNING},
                )
            elif "stage3" in stages_completed:
                logger.info("[Pipeline] 断点续跑：Stage3 已完成，从磁盘加载")
                append_task_log(task_id, "[Stage3] 断点恢复：已完成，从磁盘加载", stock_code)
                stage3_result = _load_stage3_from_disk(task_id, stock_code, packet)
            else:
                if "stage2" in stages_completed:
                    update_task(
                        task_id, stock_code,
                        current_stage="stage3",
                        stage_progress={"stage3": StageStatus.RUNNING},
                    )
                stage3_result = await run_stage3(
                    task_id=task_id,
                    stage1_results=stage1_results,
                    stage2_results=stage2_results,
                    packet=packet,
                    stock_code=stock_code,
                    market_rules=market_rules,
                    llm_client=llm_client,
                    task_semaphore=task_semaphore,
                    cancel_event=cancel_event,
                )

            # Stage 3 停牌结果（仅当 Stage 3 实际执行时才检测）
            if effective_stage3_enabled and isinstance(stage3_result, SuspendedResult):
                logger.info("[Pipeline] Stage3 检测到停牌，生成停牌报告")
                append_task_log(task_id, "[Pipeline] ⚠ Stage3 检测到停牌，生成停牌报告", stock_code)
                report_path = await write_suspended_report(stock_code, task_id, stage3_result.reason)
                update_task(
                    task_id, stock_code,
                    status=TaskStatus.COMPLETED,
                    current_stage=None,
                    stage_progress={
                        "stage1": StageStatus.COMPLETED,
                        "stage2": StageStatus.COMPLETED if effective_stage2_enabled else StageStatus.SKIPPED,
                        "stage3": StageStatus.SKIPPED,
                        "stage4": StageStatus.SKIPPED,
                    },
                    report_path=report_path,
                )
                return

            if effective_stage3_enabled and "stage3" not in stages_completed:
                update_task(
                    task_id, stock_code,
                    current_stage="stage4",
                    stage_progress={"stage3": StageStatus.COMPLETED, "stage4": StageStatus.RUNNING},
                )
                logger.info(f"[Pipeline] Stage 3 完成，股票: {stock_code}")

            # ── Stage 4 ───────────────────────────────────────────────────────
            if cancel_event.is_set():
                raise asyncio.CancelledError

            if "stage3" in stages_completed:
                # 本次运行从 stage4 开始，需先更新进度
                update_task(
                    task_id, stock_code,
                    current_stage="stage4",
                    stage_progress={"stage4": StageStatus.RUNNING},
                )

            report_path = await run_stage4(
                stock_code=stock_code,
                task_id=task_id,
                stage1_results=stage1_results,
                stage2_results=stage2_results,
                stage3_results=stage3_result,
                packet=packet,
                available_tools=packet.available_tools,
                market_rules=market_rules,
                llm_client=llm_client,
                task_semaphore=task_semaphore,
                cancel_event=cancel_event,
            )

            update_task(
                task_id, stock_code,
                status=TaskStatus.COMPLETED,
                current_stage=None,
                current_agent=None,
                stage_progress={"stage4": StageStatus.COMPLETED},
                report_path=report_path,
            )
            elapsed = time.monotonic() - t_start
            elapsed_str = f"{int(elapsed // 60)}m{int(elapsed % 60)}s"
            logger.info(f"[Pipeline] 分析完成，总耗时 {elapsed_str}，报告: {report_path}")
            append_task_log(task_id, f"[Pipeline] ✓ 分析完成，总耗时 {elapsed_str}", stock_code)

        except asyncio.CancelledError:
            logger.info(f"[Pipeline] 任务已取消，stock_code={stock_code}")
            append_task_log(task_id, "[Pipeline] 任务已取消", stock_code)
            update_task(task_id, stock_code, status=TaskStatus.CANCELLED, current_stage=None, current_agent=None)

        except Exception as e:
            logger.exception(f"[Pipeline] 任务失败，stock_code={stock_code} — {e}")
            append_task_log(task_id, f"[Pipeline] ✗ 任务失败: {e}", stock_code)
            update_task(task_id, stock_code, status=TaskStatus.FAILED, current_stage=None, current_agent=None, error=str(e))
