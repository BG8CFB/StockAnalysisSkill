from __future__ import annotations

import logging
import sys

from loguru import logger

_logging_configured = False


class InterceptHandler(logging.Handler):
    """拦截标准 logging 并转发给 loguru，确保所有第三方库日志经过 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 0
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging() -> None:
    """初始化 loguru 日志系统。必须在 FastAPI 实例创建之前调用。"""
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    from src.config import settings

    logger.remove()  # 移除 loguru 默认 handler

    if settings.log_console_enabled:
        logger.add(
            sys.stderr,
            level=settings.log_level,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan> | "
                "{message}"
            ),
            colorize=True,
        )

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(settings.log_dir / "app-{time:YYYYMMDD}.log"),
        level=settings.log_level,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "{name}:{function}:{line} | "
            "{message}"
        ),
        rotation="00:00",
        retention=f"{settings.log_retention_days} days",
        encoding="utf-8",
        enqueue=True,
    )

    # 全量接管标准 logging（含 uvicorn / fastapi / httpx 等所有第三方库）
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    logger.info(f"[服务] 日志系统初始化完成，日志目录: {settings.log_dir}")
