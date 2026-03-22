from __future__ import annotations

import secrets
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"


def generate_task_id() -> str:
    return f"TASK_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3).upper()}"


class TaskRecord(BaseModel):
    task_id: str
    stock_code: str
    status: TaskStatus
    current_stage: Optional[str] = None
    stage_progress: dict[str, StageStatus] = Field(
        default_factory=lambda: {
            "stage1": StageStatus.PENDING,
            "stage2": StageStatus.PENDING,
            "stage3": StageStatus.PENDING,
            "stage4": StageStatus.PENDING,
        }
    )
    logs: list[str] = Field(default_factory=list)
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    report_path: Optional[str] = None
    error: Optional[str] = None
    note: Optional[str] = None
