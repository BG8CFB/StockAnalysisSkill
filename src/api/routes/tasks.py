from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from src.core.models import TaskRecord, TaskStatus, StageStatus
from src.core.task_queue import QueueFullError
from src.core.task_store import create_task, get_task, list_tasks, update_task
from src.config import settings

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

# 股票代码格式（A股/港股/美股）
_CODE_PATTERN = re.compile(
    r"^\d{6}\.(SZ|SH)$"           # A股 000001.SZ
    r"|^\d{5}\.HK$"               # 港股 00700.HK
    r"|^[A-Z]{1,5}$"              # 美股 AAPL
    r"|^[A-Z]{1,5}\.[A-Z]{1,2}$", # 美股带后缀 BRK.A
    re.IGNORECASE,
)


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


class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    stock_code: str
    current_stage: Optional[str]
    stage_progress: dict[str, StageStatus]
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
    report_path: str
    completed_at: Optional[datetime]


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    total: int


# ── 路由处理器 ─────────────────────────────────────────────────────────────────

@router.post("", status_code=201, response_model=CreateTaskResponse)
async def create_task_endpoint(request: Request, body: CreateTaskRequest) -> CreateTaskResponse:
    """创建新的股票分析任务。"""
    task_queue = request.app.state.task_queue

    # 创建任务记录
    task = create_task(body.stock_code, body.note)

    # 加入队列
    try:
        queue_pos = await task_queue.enqueue(task.task_id)
    except QueueFullError:
        # 队列已满，删除刚创建的任务记录
        update_task(task.task_id, status=TaskStatus.FAILED, error="Queue is full")
        raise HTTPException(
            status_code=429,
            detail=f"Task queue is full (max {settings.task_queue_max_size} pending + {task_queue.running_count()} running). Please try again later."
        )

    return CreateTaskResponse(
        task_id=task.task_id,
        status=task.status,
        stock_code=task.stock_code,
        queue_position=queue_pos,
        created_at=task.created_at,
    )


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


@router.delete("/{task_id}")
async def cancel_task_endpoint(request: Request, task_id: str) -> dict:
    """取消任务（PENDING 或 RUNNING 状态）。"""
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel task in status '{task.status}'. Only PENDING/RUNNING tasks can be cancelled."
        )

    task_queue = request.app.state.task_queue
    cancelled = await task_queue.cancel(task_id)

    if cancelled:
        return {"message": f"Task {task_id} has been cancelled", "task_id": task_id}
    else:
        return {"message": f"Task {task_id} cancellation signal sent (may still be running)", "task_id": task_id}


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

    if not task.report_path:
        raise HTTPException(
            status_code=404,
            detail="Report file path not recorded for this task"
        )

    from pathlib import Path
    report_path = Path(task.report_path)
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Report file not found at: {task.report_path}"
        )

    content = report_path.read_text(encoding="utf-8")
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
        stage_progress=task.stage_progress,
        logs=task.logs,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        error=task.error,
        report_path=task.report_path,
        note=task.note,
    )
