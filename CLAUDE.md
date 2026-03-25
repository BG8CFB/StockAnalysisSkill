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
- `data_tools.py`: Unified export module for all formatting tools (re-exports from submodules).
- Submodules (`market.py`, `fundamental.py`, `microstructure.py`, `sentiment.py`, `macro.py`): Formatting functions that take a `CalculatedDataPacket` and return Markdown. Tools are organized by data domain:
  - `market_data_tool`: Integrated tool (price + indicators + snapshot)
  - `fundamental_tool`: Financial data + shareholder structure
  - `microstructure_tool`: Capital flow + margin + dragon tiger
  - `macro_tool`: Macro economic data
  - `sentiment_tool`, `sector_tool`, `news_tool`, `risk_metric_tool`: Standalone tools
- `tool_injector.py`: For a given agent, intersects `AGENT_CONFIGS[agent]["tools"]` with `available_tools` to build the agent's full data context string. Missing tools get a standard N/A placeholder.
- `risk_calculator.py`: Pure-code VaR calculation and A-share-specific risk scoring (涨跌停/T+1/融资/停牌). Runs before Stage 3 AI agents.
- `skills_loader.py`: Loads skills from `skills/` directory following Agent Skills standard (agentskills.io). Supports progressive disclosure — AI only sees skill name/description, full content loaded on-demand.

**Layer 3 — Agents** (`src/agents/`):
- `config_loader.py`: Loads agent configurations from `config/agents/*.yaml`. Each agent has `prompt` (embedded in YAML), `tools`, `use_skills` fields.
- `base_agent.py`: `BaseAgent` — assembles system prompt (`global_rules` from `config/global.yaml` + role prompt from YAML + market rules from `config/market_rules.yaml`) and user message (skills list + data context), calls LLM.
- `llm_client.py`: OpenAI-compatible async client with semaphore-based concurrency control. Supports function calling for skills.

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

### Agent Configuration (`config/`)

All agent configurations are YAML files:
- `config/agents/stage1.yaml`: Stage 1 analysts (list format, user can add/remove)
- `config/agents/stage2.yaml`, `stage3.yaml`, `stage4.yaml`: Fixed agents for later stages
- `config/global.yaml`: Global rules prepended to every agent's system prompt
- `config/market_rules.yaml`: Market-specific rules (A/HK/US) injected dynamically based on stock code suffix

Each agent config contains: `agent_id`, `display_name`, `prompt` (embedded in YAML), `tools`, `use_skills`.

### Skills Extension (`skills/`)

Skills follow the Agent Skills open standard (agentskills.io/specification). Each skill is a directory containing:
- `SKILL.md`: Required. YAML frontmatter (name + description) + instruction content
- `references/`, `scripts/`, `assets/`: Optional supporting files

Skills use progressive disclosure:
1. Service start: Scan `skills/*/SKILL.md` to extract name + description (~100 tokens/skill)
2. LLM call: Skills are presented as function calling tools (AI only sees name/description)
3. Skill activation: When AI calls a skill, full `SKILL.md` content is loaded (<5000 tokens)

No code changes needed to add skills — just drop a directory in `skills/` and restart.

### Suspension Handling

If `cleaner.py` detects a suspended stock, the pipeline short-circuits and generates a suspension report without calling any LLM agents. Stage 3 can also trigger a suspension report via `SuspendedResult`.
