from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.routes.tasks import router as tasks_router
from src.api.routes.health import router as health_router


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。lifespan 由 main.py 注入。"""
    app = FastAPI(
        title="Stock Analysis Skill",
        description="Multi-agent stock analysis pipeline exposed as an HTTP Skill API",
        version="0.1.0",
    )
    app.include_router(tasks_router)
    app.include_router(health_router)
    return app
