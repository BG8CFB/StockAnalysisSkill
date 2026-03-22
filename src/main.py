from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from loguru import logger


def _recover_orphaned_tasks() -> None:
    """
    服务启动时处理上次崩溃/重启遗留的 RUNNING/PENDING 任务。

    策略：
      - PENDING 任务：直接标记为 FAILED（从未启动，无断点数据，无法续跑）
      - RUNNING 任务且 stages_completed 非空：重新入队，等待断点续跑
      - RUNNING 任务且 stages_completed 为空：标记为 FAILED（未完成任何阶段）

    注意：此函数在 worker 启动前同步执行，不存在竞争条件。
    """
    from src.core.task_store import list_tasks, update_task, append_task_log, get_task
    from src.core.models import TaskStatus

    # 处理 PENDING 任务（从未启动）
    for task in list_tasks(status=TaskStatus.PENDING, limit=200):
        logger.warning(
            f"[服务] 发现遗留 PENDING 任务 {task.task_id}"
            f"（股票={task.stock_code}），标记为 FAILED"
        )
        append_task_log(task.task_id, "[Pipeline] 服务重启，任务中断，标记为失败", task.stock_code)
        update_task(
            task.task_id, task.stock_code,
            status=TaskStatus.FAILED,
            current_stage=None,
            error="服务重启导致任务中断，请重新提交",
        )

    # 处理 RUNNING 任务
    for task in list_tasks(status=TaskStatus.RUNNING, limit=200):
        if task.stages_completed:
            # 有已完成阶段：等待 worker 启动后续跑，先标记为 PENDING 以便重新入队
            logger.info(
                f"[服务] 发现可续跑任务 {task.task_id}"
                f"（股票={task.stock_code}，已完成: {task.stages_completed}），将重新入队"
            )
            append_task_log(
                task.task_id,
                f"[Pipeline] 服务重启，已完成阶段: {task.stages_completed}，等待断点续跑",
                task.stock_code,
            )
            update_task(
                task.task_id, task.stock_code,
                status=TaskStatus.PENDING,
                current_stage=None,
                current_agent=None,
            )
        else:
            # 未完成任何阶段：直接标记为失败
            logger.warning(
                f"[服务] 发现遗留 RUNNING 任务 {task.task_id}"
                f"（股票={task.stock_code}，无已完成阶段），标记为 FAILED"
            )
            append_task_log(task.task_id, "[Pipeline] 服务重启，任务中断，标记为失败", task.stock_code)
            update_task(
                task.task_id, task.stock_code,
                status=TaskStatus.FAILED,
                current_stage=None,
                current_agent=None,
                error="服务重启导致任务中断，请重新提交",
            )


async def _requeue_resumable_tasks(task_queue) -> None:
    """将恢复后变为 PENDING 的可续跑任务重新入队。"""
    from src.core.task_store import list_tasks
    from src.core.models import TaskStatus

    resumable = list_tasks(status=TaskStatus.PENDING, limit=200)
    for task in resumable:
        if task.stages_completed:  # 只重入队有断点数据的任务
            try:
                await task_queue.enqueue(task.task_id)
                logger.info(f"[服务] 续跑任务已入队: {task.task_id}（股票={task.stock_code}）")
            except Exception as e:
                logger.error(f"[服务] 续跑任务入队失败 {task.task_id}: {e}")


def _write_pid_file() -> None:
    """写入 PID 文件，供 start_service.py 检测进程存活。"""
    import os
    pid_file = Path(__file__).resolve().parent.parent / ".service.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def _remove_pid_file() -> None:
    """删除 PID 文件（服务正常退出时调用）。"""
    pid_file = Path(__file__).resolve().parent.parent / ".service.pid"
    pid_file.unlink(missing_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan：启动时创建 worker，关闭时优雅停止。"""
    from src.config import settings
    from src.core.task_queue import TaskQueue
    from src.core.scheduler import start_workers, stop_workers
    from src.core.task_store import cleanup_expired_tasks
    import asyncio

    # 写入 PID 文件（让 start_service.py 可以检测进程状态）
    _write_pid_file()

    # 确保存储目录存在
    settings.tasks_dir.mkdir(parents=True, exist_ok=True)

    # 恢复僵尸任务（必须在 worker 启动前执行，避免竞争）
    _recover_orphaned_tasks()

    # 创建任务队列并挂载到 app.state
    task_queue = TaskQueue()
    app.state.task_queue = task_queue

    # 启动 worker 协程
    num_workers = settings.max_concurrent_tasks
    await start_workers(task_queue, num_workers)
    logger.info(
        f"[服务] Stock Analysis Skill 启动，监听 "
        f"{settings.server_host}:{settings.server_port}，Worker 数: {num_workers}"
    )

    # 重新入队可续跑任务（worker 已启动，可以安全入队）
    await _requeue_resumable_tasks(task_queue)

    # 清理过期任务文件（后台异步，不阻塞启动）
    asyncio.ensure_future(cleanup_expired_tasks())

    yield

    # 优雅关闭：向队列发送停止信号，等待 worker 完成
    logger.info("[服务] 正在关闭，等待 Worker 完成...")
    await stop_workers(task_queue, num_workers)
    await logger.complete()  # 等待 loguru 文件队列刷新完毕
    logger.info("[服务] 全部 Worker 已停止，服务退出")
    _remove_pid_file()


def create_app() -> FastAPI:
    from src.logging_config import setup_logging
    setup_logging()  # 必须在 FastAPI 实例创建之前调用

    from src.api.app import create_app as _create_app
    app = _create_app()
    app.router.lifespan_context = lifespan
    return app


app = create_app()


if __name__ == "__main__":
    from src.config import settings
    uvicorn.run(
        "src.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=False,
        log_config=None,  # 禁用 uvicorn 默认 dictConfig，由 setup_logging 接管
    )
