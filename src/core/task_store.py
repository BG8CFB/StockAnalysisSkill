from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import settings
from src.core.models import StageStatus, TaskRecord, TaskStatus, generate_task_id


# --------------------------------------------------------------------------- #
# 路径工具                                                                      #
# --------------------------------------------------------------------------- #

def task_folder(task_id: str, stock_code: str) -> Path:
    """
    构造任务文件夹路径：tasks/{date}/{stock_code}/{task_id}/
    task_id 格式：TASK_{YYYYMMDD}_{HHMMSS}_{HEX6}
    """
    date = task_id.split("_")[1]   # "20260322"
    return settings.tasks_dir / date / stock_code / task_id


def find_task_folder(task_id: str) -> Optional[Path]:
    """
    在不知道 stock_code 时，通过 glob 定位任务文件夹。
    pathlib.glob 对 stock_code 中的 '.' 无特殊处理，安全。
    """
    date = task_id.split("_")[1]
    matches = list(settings.tasks_dir.glob(f"{date}/*/{task_id}"))
    return matches[0] if matches else None


def _task_file(task_id: str, stock_code: Optional[str] = None) -> Optional[Path]:
    if stock_code:
        return task_folder(task_id, stock_code) / "task.json"
    folder = find_task_folder(task_id)
    return (folder / "task.json") if folder else None


# --------------------------------------------------------------------------- #
# 原子写入                                                                      #
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# 任务 CRUD                                                                     #
# --------------------------------------------------------------------------- #

def create_task(stock_code: str, note: Optional[str] = None) -> TaskRecord:
    task_id = generate_task_id()
    folder = task_folder(task_id, stock_code)
    folder.mkdir(parents=True, exist_ok=True)
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
    _write_atomic(folder / "task.json", record.model_dump())
    return record


def get_task(task_id: str, stock_code: Optional[str] = None) -> Optional[TaskRecord]:
    path = _task_file(task_id, stock_code)
    if path is None or not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return TaskRecord.model_validate(data)


def update_task(task_id: str, stock_code: Optional[str] = None, **fields) -> TaskRecord:
    record = get_task(task_id, stock_code)
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

    # stage_progress 合并（不替换）
    if "stage_progress" in fields:
        merged = dict(record.stage_progress)
        merged.update(fields["stage_progress"])
        fields["stage_progress"] = merged

        # 自动同步 stages_completed：stage_progress[X]=COMPLETED 时追加 X
        current_completed = list(record.stages_completed)
        for stage, status in merged.items():
            if status == StageStatus.COMPLETED and stage not in current_completed:
                current_completed.append(stage)
        fields["stages_completed"] = current_completed

    updated = record.model_copy(update=fields)
    path = _task_file(task_id, updated.stock_code)
    _write_atomic(path, updated.model_dump())
    return updated


def delete_task_folder(task_id: str, stock_code: Optional[str] = None) -> bool:
    """删除整个任务文件夹（含 task.json / report.md / agents/ / data/）。"""
    if stock_code:
        folder = task_folder(task_id, stock_code)
    else:
        folder = find_task_folder(task_id)
    if folder is None or not folder.exists():
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True


def list_tasks(
    status: Optional[TaskStatus] = None,
    limit: int = 20,
    offset: int = 0,
) -> list[TaskRecord]:
    if not settings.tasks_dir.exists():
        return []

    records: list[TaskRecord] = []
    # 扫描三级目录：{date}/{stock_code}/{task_id}/task.json
    for path in sorted(
        settings.tasks_dir.glob("*/*/TASK_*/task.json"),
        reverse=True,
    ):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            record = TaskRecord.model_validate(data)
            if status is None or record.status == status:
                records.append(record)
        except Exception:
            continue

    return records[offset: offset + limit]


def append_task_log(task_id: str, message: str, stock_code: Optional[str] = None) -> None:
    """向任务追加操作日志条目（时间戳前缀）。"""
    record = get_task(task_id, stock_code)
    if record is None:
        return
    entry = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    updated_logs = record.logs + [entry]
    updated = record.model_copy(update={"logs": updated_logs})
    path = _task_file(task_id, updated.stock_code)
    _write_atomic(path, updated.model_dump())


def find_active_task(stock_code: str) -> Optional[TaskRecord]:
    """查找 stock_code 的活跃任务（PENDING 或 RUNNING），用于幂等检查。"""
    if not settings.tasks_dir.exists():
        return None
    for path in sorted(
        settings.tasks_dir.glob("*/*/TASK_*/task.json"),
        reverse=True,
    ):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            record = TaskRecord.model_validate(data)
            if record.stock_code == stock_code and record.status in (
                TaskStatus.PENDING, TaskStatus.RUNNING
            ):
                return record
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------- #
# 智能体输出 / 数据依据 落盘                                                    #
# --------------------------------------------------------------------------- #

def save_agent_output(
    task_id: str,
    stock_code: str,
    filename: str,
    content: str,
) -> None:
    """
    保存智能体 AI 输出到 agents/ 目录。
    filename 示例：stage1_technical_analyst.md / stage2_bull_r0.md
    """
    folder = task_folder(task_id, stock_code) / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text(content, encoding="utf-8")


def load_agent_output(
    task_id: str,
    stock_code: str,
    filename: str,
) -> Optional[str]:
    """从 agents/ 目录读取智能体输出，文件不存在返回 None。"""
    file = task_folder(task_id, stock_code) / "agents" / filename
    return file.read_text("utf-8") if file.exists() else None


def save_data_evidence(
    task_id: str,
    stock_code: str,
    filename: str,
    display_name: str,
    agent_id: str,
    stage: str,
    tools: list[str],
    raw_content: str,
) -> None:
    """
    保存注入智能体的原始工具数据到 data/ 目录（溯源依据）。
    filename 示例：stage1_technical_analyst_data.md

    文件格式包含头部元信息，便于用户理解数据来源。
    """
    folder = task_folder(task_id, stock_code) / "data"
    folder.mkdir(parents=True, exist_ok=True)
    tools_str = ", ".join(tools) if tools else "（无工具，上游报告直接传入）"
    header = (
        f"# 数据依据 · {display_name} ({agent_id})\n\n"
        f"**调用智能体**: {display_name} ({agent_id})  \n"
        f"**所属阶段**: {stage}  \n"
        f"**使用工具**: {tools_str}  \n"
        f"**记录时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"---\n\n"
    )
    (folder / filename).write_text(header + raw_content, encoding="utf-8")


def save_report(task_id: str, stock_code: str, content: str) -> str:
    """
    将最终投资报告写入任务文件夹根目录的 report.md。
    返回相对路径字符串（用于 task.json 的 report_path 字段）。
    """
    folder = task_folder(task_id, stock_code)
    report_path = folder / "report.md"
    report_path.write_text(content, encoding="utf-8")
    return str(report_path)


def get_report_content(task_id: str, stock_code: Optional[str] = None) -> Optional[str]:
    """读取任务的最终报告内容，不存在返回 None。"""
    if stock_code:
        folder = task_folder(task_id, stock_code)
    else:
        folder = find_task_folder(task_id)
    if folder is None:
        return None
    report_path = folder / "report.md"
    return report_path.read_text("utf-8") if report_path.exists() else None


def list_agent_outputs(
    task_id: str,
    stock_code: Optional[str] = None,
) -> list[tuple[str, str]]:
    """
    列出任务已生成的智能体输出文件，返回 [(filename, content), ...] 列表。
    按文件名升序排列（stage1 < stage2 < stage3 的自然顺序）。
    文件夹不存在或为空时返回空列表。
    """
    if stock_code:
        agents_dir = task_folder(task_id, stock_code) / "agents"
    else:
        task_fdr = find_task_folder(task_id)
        agents_dir = (task_fdr / "agents") if task_fdr else None

    if agents_dir is None or not agents_dir.exists():
        return []

    results: list[tuple[str, str]] = []
    for f in sorted(agents_dir.glob("*.md")):
        try:
            results.append((f.name, f.read_text("utf-8")))
        except Exception:
            continue
    return results


# --------------------------------------------------------------------------- #
# 自动清理                                                                      #
# --------------------------------------------------------------------------- #

async def cleanup_expired_tasks() -> None:
    """清理过期任务文件夹（服务启动时后台执行）。"""
    import asyncio
    from loguru import logger as _logger

    now = datetime.now()
    # 精确匹配任务文件，避免误匹配子目录
    for task_json in settings.tasks_dir.glob("*/*/TASK_*/task.json"):
        task_folder_path = task_json.parent
        try:
            with open(task_json, encoding="utf-8") as f:
                data = json.load(f)
            task = TaskRecord.model_validate(data)
        except Exception:
            continue

        if task.status == TaskStatus.COMPLETED:
            days = settings.completed_task_retention_days
            if days == 0:
                continue  # 永久保留
            ref_time = task.completed_at or task.created_at

        elif task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            days = settings.failed_task_retention_days
            ref_time = task.completed_at or task.created_at

        else:
            continue  # PENDING/RUNNING 不清理

        if (now - ref_time).days >= days:
            shutil.rmtree(task_folder_path, ignore_errors=True)
            _logger.info(
                f"[清理] 已删除过期任务 {task.task_id}"
                f"（状态={task.status.value}，超过 {days} 天）"
            )
        await asyncio.sleep(0)  # 让出事件循环，避免阻塞启动
