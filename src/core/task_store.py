from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import settings
from src.core.models import StageStatus, TaskRecord, TaskStatus, generate_task_id


def _task_file(task_id: str) -> Path:
    return settings.tasks_dir / f"{task_id}.json"


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def create_task(stock_code: str, note: Optional[str] = None) -> TaskRecord:
    task_id = generate_task_id()
    record = TaskRecord(
        task_id=task_id,
        stock_code=stock_code,
        status=TaskStatus.PENDING,
        stage_progress={
            "stage1": StageStatus.PENDING,
            "stage2": StageStatus.PENDING,
            "stage3": StageStatus.PENDING,
            "stage4": StageStatus.PENDING,
        },
        created_at=datetime.now(),
        note=note,
    )
    _write_atomic(_task_file(task_id), record.model_dump())
    return record


def get_task(task_id: str) -> Optional[TaskRecord]:
    path = _task_file(task_id)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return TaskRecord.model_validate(data)


def update_task(task_id: str, **fields) -> TaskRecord:
    record = get_task(task_id)
    if record is None:
        raise ValueError(f"Task {task_id} not found")
    # 自动设置 started_at：首次转为 RUNNING 时
    new_status = fields.get("status")
    if new_status == TaskStatus.RUNNING and record.started_at is None:
        fields.setdefault("started_at", datetime.now())
    # 自动设置 completed_at：首次转为终态时
    if new_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        if record.completed_at is None:
            fields.setdefault("completed_at", datetime.now())
    # stage_progress 合并（不替换，防止丢失其他阶段状态）
    if "stage_progress" in fields:
        merged = dict(record.stage_progress)
        merged.update(fields["stage_progress"])
        fields["stage_progress"] = merged
    updated = record.model_copy(update=fields)
    _write_atomic(_task_file(task_id), updated.model_dump())
    return updated


def list_tasks(
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> list[TaskRecord]:
    tasks_dir = settings.tasks_dir
    if not tasks_dir.exists():
        return []

    records: list[TaskRecord] = []
    for path in sorted(tasks_dir.glob("TASK_*.json"), reverse=True):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            record = TaskRecord.model_validate(data)
            if status is None or record.status.value == status:
                records.append(record)
        except Exception:
            continue

    return records[offset : offset + limit]
