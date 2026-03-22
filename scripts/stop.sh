#!/bin/bash
# 停止 StockAnalysisSkill 服务
# 用法：bash scripts/stop.sh [--force]
# 输出：JSON {"status": "stopped"|"not_running"|"error", "pid": 12345}

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"
exec uv run python scripts/stop_service.py "$@"
