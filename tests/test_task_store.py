from datetime import datetime

import pytest
from src.core.task_store import create_task, update_task, get_task, list_tasks, append_task_log, find_active_task
from src.core.models import StageStatus, TaskStatus


@pytest.fixture(autouse=True)
def tmp_tasks_dir(tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config.settings, "tasks_dir", tmp_path / "tasks")
    (tmp_path / "tasks").mkdir()


def test_update_task_merges_stage_progress():
    task = create_task("000001.SZ")
    update_task(task.task_id, stage_progress={"stage1": StageStatus.RUNNING})
    result = get_task(task.task_id)
    assert result is not None
    assert result.stage_progress["stage1"] == StageStatus.RUNNING
    assert result.stage_progress["stage2"] == StageStatus.PENDING
    assert result.stage_progress["stage3"] == StageStatus.PENDING
    assert result.stage_progress["stage4"] == StageStatus.PENDING


def test_update_task_merges_multiple_stage_updates():
    """Simulate orchestrator's stage-by-stage updates."""
    task = create_task("000001.SZ")
    update_task(task.task_id, stage_progress={"stage1": StageStatus.RUNNING})
    update_task(task.task_id, stage_progress={"stage1": StageStatus.COMPLETED, "stage2": StageStatus.RUNNING})
    result = get_task(task.task_id)
    assert result is not None
    assert result.stage_progress["stage1"] == StageStatus.COMPLETED
    assert result.stage_progress["stage2"] == StageStatus.RUNNING
    assert result.stage_progress["stage3"] == StageStatus.PENDING
    assert result.stage_progress["stage4"] == StageStatus.PENDING


def test_update_task_sets_completed_at_on_completion():
    task = create_task("000001.SZ")
    update_task(task.task_id, status=TaskStatus.RUNNING)
    update_task(task.task_id, status=TaskStatus.COMPLETED)
    result = get_task(task.task_id)
    assert result is not None
    assert result.completed_at is not None


def test_update_task_sets_completed_at_on_failure():
    task = create_task("000001.SZ")
    update_task(task.task_id, status=TaskStatus.FAILED, error="test error")
    result = get_task(task.task_id)
    assert result.completed_at is not None


def test_update_task_sets_completed_at_on_cancellation():
    task = create_task("000001.SZ")
    update_task(task.task_id, status=TaskStatus.RUNNING)
    update_task(task.task_id, status=TaskStatus.CANCELLED)
    result = get_task(task.task_id)
    assert result is not None
    assert result.completed_at is not None


def test_update_task_does_not_overwrite_completed_at():
    task = create_task("000001.SZ")
    fixed_time = datetime(2026, 1, 1, 12, 0, 0)
    update_task(task.task_id, status=TaskStatus.COMPLETED, completed_at=fixed_time)
    # calling update again should not change completed_at
    update_task(task.task_id, status=TaskStatus.COMPLETED)
    result = get_task(task.task_id)
    assert result is not None
    assert result.completed_at == fixed_time


def test_update_task_sets_started_at_on_running():
    task = create_task("000001.SZ")
    assert task.started_at is None
    update_task(task.task_id, status=TaskStatus.RUNNING)
    result = get_task(task.task_id)
    assert result is not None
    assert result.started_at is not None


def test_create_and_get_task():
    task = create_task("000001.SZ", note="test note")
    fetched = get_task(task.task_id)
    assert fetched is not None
    assert fetched.stock_code == "000001.SZ"
    assert fetched.note == "test note"
    assert fetched.status == TaskStatus.PENDING


def test_get_task_returns_none_for_missing():
    result = get_task("TASK_NONEXISTENT")
    assert result is None


def test_list_tasks_returns_all():
    create_task("000001.SZ")
    create_task("600000.SH")
    tasks = list_tasks()
    assert len(tasks) == 2


def test_list_tasks_filters_by_status():
    t1 = create_task("000001.SZ")
    t2 = create_task("600000.SH")
    update_task(t2.task_id, status=TaskStatus.COMPLETED)
    pending = list_tasks(status=TaskStatus.PENDING)
    assert len(pending) == 1
    assert pending[0].task_id == t1.task_id


def test_update_task_raises_for_missing_task():
    with pytest.raises(ValueError, match="not found"):
        update_task("TASK_NONEXISTENT_XYZ", status=TaskStatus.RUNNING)


def test_append_task_log():
    task = create_task("000001.SZ")
    append_task_log(task.task_id, "Stage1 技术分析师 开始")
    append_task_log(task.task_id, "Stage1 技术分析师 完成（1234字）")
    result = get_task(task.task_id)
    assert result is not None
    assert len(result.logs) == 2
    assert "技术分析师 开始" in result.logs[0]
    assert "技术分析师 完成" in result.logs[1]
    assert result.logs[0].startswith("[")  # has timestamp prefix


def test_append_task_log_nonexistent_task():
    # should silently do nothing, not raise
    append_task_log("TASK_NONEXISTENT_XYZ", "test message")


def test_find_active_task_finds_pending():
    task = create_task("000001.SZ")
    found = find_active_task("000001.SZ")
    assert found is not None
    assert found.task_id == task.task_id


def test_find_active_task_finds_running():
    task = create_task("000001.SZ")
    update_task(task.task_id, status=TaskStatus.RUNNING)
    found = find_active_task("000001.SZ")
    assert found is not None


def test_find_active_task_returns_none_for_completed():
    task = create_task("000001.SZ")
    update_task(task.task_id, status=TaskStatus.COMPLETED)
    found = find_active_task("000001.SZ")
    assert found is None


def test_find_active_task_returns_none_for_different_stock():
    create_task("000001.SZ")
    found = find_active_task("600000.SH")
    assert found is None
