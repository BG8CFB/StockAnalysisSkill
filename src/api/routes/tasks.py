from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, field_validator

from src.core.models import TaskRecord, TaskStatus, StageStatus
from src.core.task_queue import QueueFullError
from src.core.task_store import (
    create_task, get_task, list_tasks, update_task,
    find_active_task, delete_task_folder, get_report_content,
)
from src.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

# 股票代码格式（A股/港股/美股）
_CODE_PATTERN = re.compile(
    r"^\d{6}\.(SZ|SH)$"           # A股 000001.SZ
    r"|^\d{5}\.HK$"               # 港股 00700.HK
    r"|^[A-Z]{1,5}$"              # 美股 AAPL
    r"|^[A-Z]{1,5}\.[A-Z]{1,2}$", # 美股带后缀 BRK.A
    re.IGNORECASE,
)

# DELETE 等待 RUNNING 任务完成的超时（秒）
_DELETE_WAIT_TIMEOUT = 5.0


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    stock_code: str
    note: Optional[str] = None

    @field_validator("stock_code")
    @classmethod
    def validate_stock_code(cls, v: str) -> str:
        v = v.strip().upper()
        if not _CODE_PATTERN.match(v):
            raise ValueError(
                f"Invalid stock code format: '{v}'. "
                "Expected formats: 000001.SZ, 00700.HK, AAPL"
            )
        return v


class CreateTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    stock_code: str
    queue_position: int
    created_at: datetime
    is_existing: bool = False


class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    stock_code: str
    current_stage: Optional[str]
    current_agent: Optional[str]
    stage_progress: dict[str, StageStatus]
    stages_completed: list[str]
    resume_count: int
    logs: list[str]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error: Optional[str]
    report_path: Optional[str]
    note: Optional[str]


class ReportResponse(BaseModel):
    task_id: str
    stock_code: str
    content: str
    report_path: Optional[str]
    completed_at: Optional[datetime]


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    total: int


# ── 路由处理器 ─────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_task_endpoint(request: Request, body: CreateTaskRequest):
    """创建股票分析任务（幂等：相同股票已有活跃任务则返回 200）。"""
    logger.info(f"[API] 收到分析请求，股票: {body.stock_code}")

    # 幂等检查：同一股票有 PENDING/RUNNING 任务时直接返回
    existing = find_active_task(body.stock_code)
    if existing:
        logger.info(
            f"[API] 发现重复任务，{body.stock_code} 已有进行中任务 "
            f"{existing.task_id}，直接返回"
        )
        response = CreateTaskResponse(
            task_id=existing.task_id,
            status=existing.status,
            stock_code=existing.stock_code,
            queue_position=0,
            created_at=existing.created_at,
            is_existing=True,
        )
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

    task_queue = request.app.state.task_queue
    task = create_task(body.stock_code, body.note)

    try:
        queue_pos = await task_queue.enqueue(task.task_id)
    except QueueFullError:
        update_task(task.task_id, task.stock_code, status=TaskStatus.FAILED, error="Queue is full")
        logger.warning(f"[API] 队列已满（max {settings.task_queue_max_size}），拒绝请求")
        raise HTTPException(
            status_code=429,
            detail=(
                f"Task queue is full (max {settings.task_queue_max_size}). "
                "Please try again later."
            ),
        )

    logger.info(f"[API] 任务创建成功，task_id: {task.task_id}，队列位置: {queue_pos}")
    response = CreateTaskResponse(
        task_id=task.task_id,
        status=task.status,
        stock_code=task.stock_code,
        queue_position=queue_pos,
        created_at=task.created_at,
        is_existing=False,
    )
    return JSONResponse(status_code=201, content=response.model_dump(mode="json"))


@router.get("", response_model=TaskListResponse)
async def list_tasks_endpoint(
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> TaskListResponse:
    """列出任务（可按状态过滤）。"""
    status_filter = TaskStatus(status) if status else None
    tasks = list_tasks(status=status_filter, limit=limit, offset=offset)
    return TaskListResponse(
        tasks=[_task_to_response(t) for t in tasks],
        total=len(tasks),
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task_endpoint(task_id: str) -> TaskResponse:
    """获取任务详情。"""
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    return _task_to_response(task)


@router.delete("/{task_id}", status_code=204)
async def delete_task_endpoint(request: Request, task_id: str) -> Response:
    """
    删除任务及其全部文件（task.json / report.md / agents/ / data/）。

    - PENDING/RUNNING：先发取消信号，等待最多 5s 完成，再删除文件夹
    - COMPLETED/FAILED/CANCELLED：直接删除文件夹
    - 404：任务不存在
    """
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    task_queue = request.app.state.task_queue
    stock_code = task.stock_code

    if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        logger.info(f"[API] 删除活跃任务 {task_id}（状态={task.status}），发送取消信号")
        done_event = task_queue.get_done_event(task_id)
        await task_queue.cancel(task_id)
        try:
            await asyncio.wait_for(done_event.wait(), timeout=_DELETE_WAIT_TIMEOUT)
            logger.info(f"[API] 任务 {task_id} 已停止，开始删除文件")
        except asyncio.TimeoutError:
            logger.warning(
                f"[API] 等待任务 {task_id} 停止超时（{_DELETE_WAIT_TIMEOUT}s），强制删除文件"
            )

    deleted = delete_task_folder(task_id, stock_code)
    if deleted:
        logger.info(f"[API] 任务 {task_id} 文件夹已删除")
    else:
        logger.warning(f"[API] 任务 {task_id} 文件夹不存在或已删除")

    return Response(status_code=204)


@router.get("/{task_id}/report", response_model=ReportResponse)
async def get_report_endpoint(task_id: str) -> ReportResponse:
    """获取已完成任务的 Markdown 报告内容。"""
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Task is not completed yet (current status: '{task.status}'). Please wait."
        )

    content = get_report_content(task_id, task.stock_code)
    if content is None:
        raise HTTPException(
            status_code=404,
            detail="Report file not found for this task"
        )

    return ReportResponse(
        task_id=task.task_id,
        stock_code=task.stock_code,
        content=content,
        report_path=task.report_path,
        completed_at=task.completed_at,
    )


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _task_to_response(task: TaskRecord) -> TaskResponse:
    return TaskResponse(
        task_id=task.task_id,
        status=task.status,
        stock_code=task.stock_code,
        current_stage=task.current_stage,
        current_agent=task.current_agent,
        stage_progress=task.stage_progress,
        stages_completed=task.stages_completed,
        resume_count=task.resume_count,
        logs=task.logs,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        error=task.error,
        report_path=task.report_path,
        note=task.note,
    )
