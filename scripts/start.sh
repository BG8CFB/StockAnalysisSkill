#!/bin/bash
# 幂等启动 StockAnalysisSkill 服务（AI Skill 入口）
# 用法：bash scripts/start.sh
# 输出：JSON {"status": "running"|"started"|"error", "port": 8080, "pid": 12345, ...}

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"
exec uv run python scripts/start_service.py "$@"
