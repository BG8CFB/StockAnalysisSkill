#!/usr/bin/env python3
"""
幂等服务启动脚本 — StockAnalysisSkill

供 AI Skill 调用，无论服务是否已在运行都可安全执行。

使用方式：
    python scripts/start_service.py          # 确保服务运行
    python scripts/start_service.py --wait   # 同上，但等待时限 120s（适合慢机器）

退出码：
    0  服务已就绪（已在运行 或 成功启动）
    1  启动失败（端口被其他进程占用 / 超时 / 进程意外退出）

标准输出（JSON）：
    {"status": "running",  "port": 8080, "pid": 12345, "started": false}
    {"status": "started",  "port": 8080, "pid": 12345, "started": true}
    {"status": "error",    "port": 8080, "error": "描述"}  → stderr
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PID_FILE     = PROJECT_ROOT / ".service.pid"
LOG_FILE     = PROJECT_ROOT / "logs" / "service.log"
STARTUP_TIMEOUT = 120   # 等待服务就绪最大秒数
WAIT_INTERVAL   = 1     # 健康检查轮询间隔（秒）


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #

def _read_env() -> dict[str, str]:
    """从 .env 文件读取 KEY=VALUE 对（不依赖第三方库）。"""
    env: dict[str, str] = {}
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        env_path = PROJECT_ROOT / ".env.example"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def _get_server_config() -> tuple[str, int]:
    """返回 (host, port)，优先读 .env，回退到默认值。"""
    env = _read_env()
    host = env.get("SERVER_HOST", "127.0.0.1")
    port = int(env.get("SERVER_PORT", "8080"))
    return host, port


def _check_health(host: str, port: int, timeout: float = 2.0) -> bool:
    """向 /api/v1/health 发请求，成功返回 True。"""
    import urllib.request
    import urllib.error
    try:
        url = f"http://{host}:{port}/api/v1/health"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _is_port_in_use(host: str, port: int) -> bool:
    """检查端口是否被占用（TCP 连接探测）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def _read_pid() -> int | None:
    """读取 PID 文件，返回 int 或 None。"""
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def _is_pid_alive(pid: int) -> bool:
    """检查进程是否存活（发送信号 0）。"""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _out(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False))
    sys.stdout.flush()


def _err(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False), file=sys.stderr)
    sys.stderr.flush()


# --------------------------------------------------------------------------- #
# 主逻辑
# --------------------------------------------------------------------------- #

def main() -> None:
    host, port = _get_server_config()

    # ── Step 1：健康检查（最快路径）──────────────────────────────────────────
    if _check_health(host, port):
        pid = _read_pid()
        _out({"status": "running", "port": port, "pid": pid, "started": False})
        sys.exit(0)

    # ── Step 2：PID 文件检查（服务可能正在启动中）────────────────────────────
    existing_pid = _read_pid()
    if existing_pid and _is_pid_alive(existing_pid):
        # 进程存在但健康检查未通过 → 可能正在启动，等待
        print(f"[start_service] 检测到进程 {existing_pid} 正在运行，等待就绪...",
              file=sys.stderr)
        for _ in range(30):
            time.sleep(WAIT_INTERVAL)
            if _check_health(host, port):
                _out({"status": "running", "port": port, "pid": existing_pid, "started": False})
                sys.exit(0)

        # 等待 30s 仍未就绪 → 进程可能卡死，尝试终止
        print(f"[start_service] 进程 {existing_pid} 长时间未就绪，尝试终止...",
              file=sys.stderr)
        try:
            os.kill(existing_pid, signal.SIGTERM)
            time.sleep(3)
            if _is_pid_alive(existing_pid):
                os.kill(existing_pid, signal.SIGKILL)
                time.sleep(1)
        except Exception:
            pass
        PID_FILE.unlink(missing_ok=True)

    elif existing_pid:
        # PID 文件存在但进程已死 → 清理
        PID_FILE.unlink(missing_ok=True)

    # ── Step 3：端口占用检查（是否被其他进程占用）────────────────────────────
    if _is_port_in_use(host, port):
        # 端口被占用但不是我们的进程（健康检查失败），可能是其他应用
        msg = (f"端口 {port} 已被其他进程占用，无法启动。"
               f"请检查：lsof -i :{port}")
        _err({"status": "error", "port": port, "error": msg})
        sys.exit(1)

    # ── Step 4：启动服务 ──────────────────────────────────────────────────────
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    print(f"[start_service] 正在启动服务（host={host} port={port}）...", file=sys.stderr)

    with open(LOG_FILE, "a", encoding="utf-8") as log_fp:
        proc = subprocess.Popen(
            ["uv", "run", "python", "-m", "src.main"],
            cwd=str(PROJECT_ROOT),
            stdout=log_fp,
            stderr=log_fp,
            start_new_session=True,   # 脱离当前终端会话，AI 进程退出后服务继续运行
        )

    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    print(f"[start_service] 服务进程已启动（PID={proc.pid}），等待就绪...",
          file=sys.stderr)

    # ── Step 5：轮询健康检查，直到就绪或超时 ─────────────────────────────────
    for elapsed in range(STARTUP_TIMEOUT):
        time.sleep(WAIT_INTERVAL)

        # 服务进程意外退出
        if proc.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            msg = (f"服务进程（PID={proc.pid}）意外退出（返回码={proc.returncode}）。"
                   f"详情见日志：{LOG_FILE}")
            _err({"status": "error", "port": port, "error": msg})
            sys.exit(1)

        if _check_health(host, port):
            print(f"[start_service] 服务就绪（{elapsed + 1}s）", file=sys.stderr)
            _out({"status": "started", "port": port, "pid": proc.pid, "started": True})
            sys.exit(0)

        if (elapsed + 1) % 10 == 0:
            print(f"[start_service] 已等待 {elapsed + 1}s...", file=sys.stderr)

    # 超时
    PID_FILE.unlink(missing_ok=True)
    msg = f"服务在 {STARTUP_TIMEOUT}s 内未就绪，请检查日志：{LOG_FILE}"
    _err({"status": "error", "port": port, "error": msg})
    sys.exit(1)


if __name__ == "__main__":
    main()
