from __future__ import annotations

import asyncio
import logging
from typing import Optional

from src.config import settings
from src.core.models import TaskStatus
from src.core.task_store import update_task

logger = logging.getLogger(__name__)


class QueueFullError(Exception):
    pass


class TaskQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[Optional[str]] = asyncio.Queue(
            maxsize=settings.task_queue_max_size
        )
        # 全局 LLM API 并发信号量
        self.global_llm_semaphore = asyncio.Semaphore(settings.model_api_max_concurrency)
        # task_id → asyncio.Event（取消信号）
        self._cancel_events: dict[str, asyncio.Event] = {}
        # task_id → asyncio.Event（任务完成信号，DELETE 等待用）
        self._done_events: dict[str, asyncio.Event] = {}
        self._running_count: int = 0
        # 用于快速检查 pending 队列（task_id set，近似）
        self._pending_ids: set[str] = set()

    async def enqueue(self, task_id: str) -> int:
        if self._queue.full():
            raise QueueFullError("Task queue is full")
        await self._queue.put(task_id)
        self._pending_ids.add(task_id)
        # 提前创建 done_event，DELETE handler 可提前等待
        if task_id not in self._done_events:
            self._done_events[task_id] = asyncio.Event()
        pos = self._queue.qsize()
        logger.debug(f"[队列] 任务入队: {task_id}，当前队列大小: {pos}")
        return pos

    def get_cancel_event(self, task_id: str) -> asyncio.Event:
        if task_id not in self._cancel_events:
            self._cancel_events[task_id] = asyncio.Event()
        return self._cancel_events[task_id]

    def get_done_event(self, task_id: str) -> asyncio.Event:
        """获取任务完成 Event（任务结束时 set）。"""
        if task_id not in self._done_events:
            self._done_events[task_id] = asyncio.Event()
        return self._done_events[task_id]

    def signal_done(self, task_id: str) -> None:
        """pipeline 结束时调用，通知等待中的 DELETE handler。"""
        event = self._done_events.get(task_id)
        if event:
            event.set()

    async def cancel(self, task_id: str) -> bool:
        # 如果任务正在运行，发送取消信号
        if task_id in self._cancel_events:
            self._cancel_events[task_id].set()
            logger.info(f"[队列] 发送取消信号: {task_id}")
            return True
        # 如果任务在队列中等待，标记取消（worker 取出时跳过）
        if task_id in self._pending_ids:
            self._pending_ids.discard(task_id)
            update_task(task_id, status=TaskStatus.CANCELLED)
            self.signal_done(task_id)
            logger.info(f"[队列] 队列中任务已取消: {task_id}")
            return True
        return False

    def queue_size(self) -> int:
        return self._queue.qsize()

    def running_count(self) -> int:
        return self._running_count

    def cleanup_task(self, task_id: str) -> None:
        self.signal_done(task_id)
        self._cancel_events.pop(task_id, None)
        self._done_events.pop(task_id, None)
        self._pending_ids.discard(task_id)
