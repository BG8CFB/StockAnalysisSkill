# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (requires uv)
uv sync

# Run the service
uv run python -m src.main
# or
uv run uvicorn src.main:app --host 127.0.0.1 --port 8888

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_foo.py

# Run tests with async support
uv run pytest --asyncio-mode=auto
```

Environment: copy `.env.example` to `.env` and fill in `LLM_API_KEY` and `TUSHARE_TOKEN` before running.

## Architecture

This is a **multi-agent stock analysis pipeline** served as a FastAPI HTTP service. A task accepts a stock code (A-share/HK/US), runs a 4-stage LLM pipeline, and returns a Markdown investment report.

### Three-Layer Design

**Layer 1 — Data** (`src/data/`):
- `tushare_adapter.py` + `akshare_adapter.py`: Fetch raw market data from Tushare Pro (primary) and AkShare (fallback/supplement). Return a raw data dict and a set of `available_tools` (which tools actually have data).
- `cleaner.py`: Applies forward-adjustment (前复权), detects suspension (停牌), marks anomalies.
- `calculator.py`: Computes all technical/fundamental indicators (MACD, RSI, KDJ, Bollinger, MA, VaR etc.), returning a `CalculatedDataPacket`.

**Layer 2 — Tools** (`src/tools/`):
- `data_tools.py`: 11 formatting functions (`price_tool`, `indicator_tool`, `fundamental_tool`, `capital_flow_tool`, `margin_tool`, `dragon_tiger_tool`, `sentiment_tool`, `sector_tool`, `news_tool`, `snapshot_tool`, `risk_metric_tool`). Each takes a `CalculatedDataPacket` and returns formatted Markdown for injection into LLM context.
- `tool_injector.py`: For a given agent, intersects `AGENT_CONFIGS[agent]["tools"]` with `available_tools` to build the agent's full data context string. Missing tools get a standard N/A placeholder.
- `risk_calculator.py`: Pure-code VaR calculation and A-share-specific risk scoring (涨跌停/T+1/融资/停牌). Runs before Stage 3 AI agents.
- `skills_loader.py`: Loads `*.md` files from `skills/` directory as domain knowledge snippets injected into all agents' user messages.

**Layer 3 — Agents** (`src/agents/`):
- `registry.py`: `AGENT_CONFIGS` dict mapping 15 agent names to their `prompt_file` and required `tools`.
- `base_agent.py`: `BaseAgent` — loads prompt from `docs/prompts/`, assembles system prompt (`global_rules.md` + role prompt + market rules) and user message (skills list + data context), calls LLM.
- `llm_client.py`: OpenAI-compatible async client with semaphore-based concurrency control.

### Pipeline Stages (`src/pipeline/`)

| Stage | Agents | Mode |
|-------|--------|------|
| Stage 1 | 6 analysts: technical, fundamental, microstructure, sentiment, sector, news | Parallel |
| Stage 2 | bull/bear debate (configurable rounds) → research director → trading planner | Serial |
| Stage 3 | Pure-code VaR + risk scoring, then 3 risk managers → chief risk officer | Mixed |
| Stage 4 | Investment advisor → final Markdown report | Serial |

`orchestrator.py` drives all stages with cancellation support via `asyncio.Event`.

### Task Management (`src/core/`)

- `task_store.py`: File-based task persistence. Each task is a JSON file in `./tasks/`. Atomic writes via `tempfile` + `os.replace`.
- `task_queue.py`: Asyncio queue for task dispatch.
- `scheduler.py`: Worker coroutines that pull tasks from the queue and call `run_pipeline()`.
- `models.py`: `TaskRecord`, `TaskStatus`, `StageStatus` Pydantic models.

### API (`src/api/`)

- `routes/tasks.py`: CRUD for tasks — `POST /api/v1/tasks`, `GET /api/v1/tasks/{id}`, `GET /api/v1/tasks/{id}/report`, `DELETE /api/v1/tasks/{id}`, `GET /api/v1/tasks`
- `routes/health.py`: `GET /api/v1/health`

### Key Configuration (`src/config.py`)

All config via environment variables / `.env`. Key settings:
- `MODEL_API_MAX_CONCURRENCY` / `TASK_MAX_MODEL_CONCURRENCY` → `max_concurrent_tasks = floor(A/B)`
- `DEBATE_ROUNDS`: number of bull/bear debate rounds in Stage 2 (0–3)
- `ANALYSIS_CAPITAL_BASE`: base position size for VaR calculation (doesn't affect actual trading advice)

### Prompt Files (`docs/prompts/`)

All agent system prompts are Markdown files loaded at runtime (cached in memory). `global_rules.md` is prepended to every agent's system prompt. Market rules (`docs/market_rules/`) are injected dynamically based on the stock's market (A/HK/US).

### Skills Extension (`skills/`)

Drop `.md` files in `skills/` to add domain knowledge available to all agents. Files are loaded at service start and injected as a list into every LLM call's user message header. No code changes needed — restart service to pick up new files.

### Suspension Handling

If `cleaner.py` detects a suspended stock, the pipeline short-circuits and generates a suspension report without calling any LLM agents. Stage 3 can also trigger a suspension report via `SuspendedResult`.
