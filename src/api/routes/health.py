from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> dict:
    """服务健康检查。"""
    task_queue = getattr(request.app.state, "task_queue", None)

    if task_queue is None:
        return {"status": "starting", "queue_size": 0, "running_tasks": 0}

    from src.config import settings

    return {
        "status": "ok",
        "queue_size": task_queue.queue_size(),
        "running_tasks": task_queue.running_count(),
        "max_concurrent_tasks": settings.max_concurrent_tasks,
        "model_api_max_concurrency": settings.model_api_max_concurrency,
    }
