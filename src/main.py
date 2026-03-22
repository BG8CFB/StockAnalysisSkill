from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from loguru import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan：启动时创建 worker，关闭时优雅停止。"""
    from src.config import settings
    from src.core.task_queue import TaskQueue
    from src.core.scheduler import start_workers, stop_workers

    # 确保存储目录存在
    settings.tasks_dir.mkdir(parents=True, exist_ok=True)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)

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

    yield

    # 优雅关闭：向队列发送停止信号，等待 worker 完成
    logger.info("[服务] 正在关闭，等待 Worker 完成...")
    await stop_workers(task_queue, num_workers)
    await logger.complete()  # 等待 loguru 文件队列刷新完毕
    logger.info("[服务] 全部 Worker 已停止，服务退出")


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
