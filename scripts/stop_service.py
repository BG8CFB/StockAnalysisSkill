#!/usr/bin/env python3
"""
优雅停止 StockAnalysisSkill 服务。

使用方式：
    python scripts/stop_service.py            # 发 SIGTERM，等待最多 30s
    python scripts/stop_service.py --force    # 等待 5s 后强制 SIGKILL

退出码：
    0  服务已停止（含本来就未运行的情况）
    1  停止失败

标准输出（JSON）：
    {"status": "stopped",      "pid": 12345}
    {"status": "not_running"}
    {"status": "error",        "error": "描述"}
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PID_FILE = PROJECT_ROOT / ".service.pid"

GRACEFUL_TIMEOUT = 30   # 等待优雅关闭最大秒数
FORCE_TIMEOUT    = 5    # --force 时等待秒数


def _out(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False))
    sys.stdout.flush()


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def main() -> None:
    force = "--force" in sys.argv

    pid = _read_pid()
    if pid is None:
        # 没有 PID 文件，检查是否真的没在跑
        _out({"status": "not_running"})
        sys.exit(0)

    if not _is_pid_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        _out({"status": "not_running"})
        sys.exit(0)

    # 发送 SIGTERM（优雅关闭）
    print(f"[stop_service] 向进程 {pid} 发送 SIGTERM...", file=sys.stderr)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        _out({"status": "stopped", "pid": pid})
        sys.exit(0)

    timeout = FORCE_TIMEOUT if force else GRACEFUL_TIMEOUT
    for elapsed in range(timeout):
        time.sleep(1)
        if not _is_pid_alive(pid):
            PID_FILE.unlink(missing_ok=True)
            print(f"[stop_service] 进程 {pid} 已停止（{elapsed + 1}s）", file=sys.stderr)
            _out({"status": "stopped", "pid": pid})
            sys.exit(0)

    if force:
        # 强制终止
        print(f"[stop_service] 超时，强制终止进程 {pid}（SIGKILL）...", file=sys.stderr)
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(1)
        except Exception:
            pass
        PID_FILE.unlink(missing_ok=True)
        _out({"status": "stopped", "pid": pid, "forced": True})
        sys.exit(0)
    else:
        msg = f"进程 {pid} 在 {timeout}s 内未停止，请使用 --force 强制终止"
        print(f"[stop_service] {msg}", file=sys.stderr)
        print(json.dumps({"status": "error", "error": msg}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
