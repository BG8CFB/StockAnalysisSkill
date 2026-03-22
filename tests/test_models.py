# tests/test_models.py
import pytest
from datetime import datetime
from src.core.models import TaskRecord, TaskStatus, StageStatus, generate_task_id


def test_generate_task_id_starts_with_task():
    task_id = generate_task_id()
    assert task_id.startswith("TASK_")


def test_generate_task_id_is_unique():
    ids = {generate_task_id() for _ in range(100)}
    assert len(ids) == 100


def test_task_record_default_stage_progress():
    record = TaskRecord(
        task_id="TASK_TEST",
        stock_code="000001.SZ",
        status=TaskStatus.PENDING,
        created_at=datetime.now(),
    )
    assert record.stage_progress["stage1"] == StageStatus.PENDING
    assert record.stage_progress["stage2"] == StageStatus.PENDING
    assert record.stage_progress["stage3"] == StageStatus.PENDING
    assert record.stage_progress["stage4"] == StageStatus.PENDING


def test_task_record_default_logs_empty():
    record = TaskRecord(
        task_id="TASK_TEST",
        stock_code="000001.SZ",
        status=TaskStatus.PENDING,
        created_at=datetime.now(),
    )
    assert record.logs == []


def test_task_record_stage_progress_independent():
    """Two records have independent stage_progress dicts (no shared default)."""
    r1 = TaskRecord(task_id="T1", stock_code="000001.SZ", status=TaskStatus.PENDING, created_at=datetime.now())
    r2 = TaskRecord(task_id="T2", stock_code="000001.SZ", status=TaskStatus.PENDING, created_at=datetime.now())
    r1.stage_progress["stage1"] = StageStatus.COMPLETED
    assert r2.stage_progress["stage1"] == StageStatus.PENDING


def test_task_record_logs_independent():
    """Two records have independent logs lists (no shared default)."""
    r1 = TaskRecord(task_id="T1", stock_code="000001.SZ", status=TaskStatus.PENDING, created_at=datetime.now())
    r2 = TaskRecord(task_id="T2", stock_code="000001.SZ", status=TaskStatus.PENDING, created_at=datetime.now())
    r1.logs.append("test")
    assert r2.logs == []


def test_task_status_values():
    assert TaskStatus.PENDING == "pending"
    assert TaskStatus.RUNNING == "running"
    assert TaskStatus.COMPLETED == "completed"
    assert TaskStatus.FAILED == "failed"
    assert TaskStatus.CANCELLED == "cancelled"


def test_stage_status_values():
    assert StageStatus.PENDING == "pending"
    assert StageStatus.RUNNING == "running"
    assert StageStatus.COMPLETED == "completed"
    assert StageStatus.SKIPPED == "skipped"


def test_task_record_optional_fields_default_none():
    record = TaskRecord(
        task_id="TASK_TEST",
        stock_code="AAPL",
        status=TaskStatus.PENDING,
        created_at=datetime.now(),
    )
    assert record.current_stage is None
    assert record.started_at is None
    assert record.completed_at is None
    assert record.report_path is None
    assert record.error is None
    assert record.note is None


def test_task_record_serializes_to_dict():
    record = TaskRecord(
        task_id="TASK_TEST",
        stock_code="000001.SZ",
        status=TaskStatus.PENDING,
        created_at=datetime.now(),
    )
    d = record.model_dump()
    assert d["task_id"] == "TASK_TEST"
    assert d["status"] == "pending"
    assert isinstance(d["stage_progress"], dict)
    assert isinstance(d["logs"], list)


def test_task_record_roundtrip_json():
    """model_dump + model_validate preserves all fields."""
    original = TaskRecord(
        task_id="TASK_TEST",
        stock_code="00700.HK",
        status=TaskStatus.RUNNING,
        created_at=datetime.now(),
        logs=["[10:00:00] started"],
        note="test note",
    )
    data = original.model_dump()
    restored = TaskRecord.model_validate(data)
    assert restored.task_id == original.task_id
    assert restored.stock_code == original.stock_code
    assert restored.status == original.status
    assert restored.logs == original.logs
    assert restored.note == original.note
