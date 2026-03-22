from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

from loguru import logger

T = TypeVar("T")


async def run_with_stage_retry(
    coro_factory: Callable[[], Awaitable[T]],
    stage_name: str,
    max_retries: int = 2,
    delay: float = 30.0,
) -> T:
    """
    为关键串行智能体提供重试保障。

    参数：
        coro_factory: 无参数的协程工厂，每次重试时调用以创建新协程。
        stage_name:   用于日志的阶段/智能体名称。
        max_retries:  最大重试次数（不含首次尝试）。
        delay:        重试前等待秒数。

    异常传播：
        asyncio.CancelledError 立即重新抛出，不重试。
        超过 max_retries 后抛出 RuntimeError，包含最后一次异常信息。
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            logger.warning(
                f"[{stage_name}] 第 {attempt}/{max_retries} 次重试，等待 {delay:.0f}s..."
            )
            await asyncio.sleep(delay)
        try:
            return await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_exc = e
            logger.error(f"[{stage_name}] 第 {attempt + 1} 次尝试失败: {e}")

    raise RuntimeError(
        f"[{stage_name}] 已重试 {max_retries} 次，仍失败: {last_exc}"
    ) from last_exc
