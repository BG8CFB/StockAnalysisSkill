from __future__ import annotations

import asyncio
import logging
from typing import Optional

from src.config import settings
from src.core.task_queue import TaskQueue

logger = logging.getLogger(__name__)

# 全局 worker 任务列表（用于 shutdown 时 cancel）
_worker_tasks: list[asyncio.Task] = []


async def _worker(task_queue: TaskQueue, worker_id: int) -> None:
    """单个 worker：从队列取任务并执行流水线。"""
    logger.info(f"[调度] Worker-{worker_id} 就绪，等待任务")
    while True:
        task_id: Optional[str] = await task_queue._queue.get()
        if task_id is None:
            # 收到停止信号
            task_queue._queue.task_done()
            logger.info(f"[调度] Worker-{worker_id} 收到停止信号")
            break

        # 若任务在等待过程中已被取消，直接跳过
        if task_id not in task_queue._pending_ids and task_id not in task_queue._cancel_events:
            # 已在 cancel() 中被处理
            task_queue._queue.task_done()
            continue

        task_queue._pending_ids.discard(task_id)
        logger.info(f"[调度] Worker-{worker_id} 接收任务 {task_id}")

        # 创建取消 Event 并注册
        cancel_event = task_queue.get_cancel_event(task_id)
        # 创建 per-task 并发信号量
        task_semaphore = asyncio.Semaphore(settings.task_max_model_concurrency)

        task_queue._running_count += 1
        try:
            # 延迟导入避免循环依赖
            from src.pipeline.orchestrator import run_pipeline
            from src.agents.llm_client import LLMClient

            llm_client = LLMClient(task_queue.global_llm_semaphore)
            task_record = _get_task(task_id)
            if task_record is None:
                logger.warning(f"[调度] 任务 {task_id} 不存在，跳过")
                continue

            await run_pipeline(
                task_id=task_id,
                stock_code=task_record.stock_code,
                cancel_event=cancel_event,
                task_semaphore=task_semaphore,
                llm_client=llm_client,
            )
        except Exception as e:
            logger.exception(f"[调度] Worker-{worker_id} 任务 {task_id} 发生异常: {e}")
        finally:
            task_queue._running_count -= 1
            task_queue.cleanup_task(task_id)
            task_queue._queue.task_done()

    logger.info(f"[调度] Worker-{worker_id} 已停止")


async def start_workers(task_queue: TaskQueue, num_workers: int) -> list[asyncio.Task]:
    global _worker_tasks
    _worker_tasks = [
        asyncio.create_task(_worker(task_queue, i), name=f"worker-{i}")
        for i in range(num_workers)
    ]
    logger.info(f"[调度] 已启动 {num_workers} 个 Worker")
    return _worker_tasks


async def stop_workers(task_queue: TaskQueue, num_workers: int) -> None:
    for _ in range(num_workers):
        await task_queue._queue.put(None)
    if _worker_tasks:
        await asyncio.gather(*_worker_tasks, return_exceptions=True)
    logger.info("[调度] 全部 Worker 已停止")


def _get_task(task_id: str):
    from src.core.task_store import get_task
    return get_task(task_id)
