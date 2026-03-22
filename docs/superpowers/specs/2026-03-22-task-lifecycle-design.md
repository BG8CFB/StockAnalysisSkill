# 任务全生命周期管理 — 设计规范

**日期**: 2026-03-22
**状态**: 待实施
**背景**: 原设计存在中间结果不落盘、僵尸任务阻塞、删除无效、日志无法追踪等缺陷。本文档描述完整重设计方案。

---

## 一、存储结构

### 1.1 任务文件夹层级

```
tasks/
└── {YYYYMMDD}/                          ← 按日期分组
    └── {stock_code}/                    ← 按股票代码分组（如 000001.SZ）
        └── {task_id}/                   ← 每个任务独立文件夹
            ├── task.json                ← 任务状态、日志、元数据
            ├── report.md                ← 最终投资报告（Stage4 输出，用户阅读）
            ├── agents/                  ← 各智能体的 AI 输出（中间过程）
            │   ├── stage1_technical_analyst.md       ← 文件名 = stage{N}_{agent_id}.md
            │   ├── stage1_fundamental_analyst.md
            │   ├── stage1_microstructure_analyst.md
            │   ├── stage1_sentiment_analyst.md
            │   ├── stage1_sector_analyst.md
            │   ├── stage1_news_analyst.md
            │   ├── stage2_bull_r0.md    ← 第0轮看涨初始报告
            │   ├── stage2_bear_r0.md
            │   ├── stage2_bull_r1.md    ← 第N轮反驳（N由 DEBATE_ROUNDS 决定）
            │   ├── stage2_bear_r1.md
            │   ├── stage2_research_director.md
            │   ├── stage2_trading_planner.md
            │   ├── stage3_aggressive_risk_manager.md
            │   ├── stage3_conservative_risk_manager.md
            │   ├── stage3_quant_risk_manager.md
            │   └── stage3_chief_risk_officer.md
            └── data/                    ← 注入各智能体的原始工具数据（溯源依据）
                ├── stage1_technical_analyst_data.md
                ├── stage1_fundamental_analyst_data.md
                ├── stage1_microstructure_analyst_data.md
                ├── stage1_sentiment_analyst_data.md
                ├── stage1_sector_analyst_data.md
                ├── stage1_news_analyst_data.md
                ├── stage2_trading_planner_data.md
                └── stage4_investment_advisor_data.md
```

**文件命名规则（必须严格遵守）**：
- `agents/` 下的文件：`{stage_prefix}_{agent_id}.md`，其中 `agent_id` 直接取自 `AGENT_CONFIGS` 的 key（如 `technical_analyst`），不做任何变换
- `data/` 下的文件：`{stage_prefix}_{agent_id}_data.md`，同样直接使用 `agent_id`
- Stage2 辩论轮次文件例外：`stage2_bull_r{N}.md` / `stage2_bear_r{N}.md`（N 从 0 开始）

**为什么使用完整 agent_id（不截断）**：agent_id 由 `config/agents/stage1.yaml` 动态配置，截断会与动态配置产生隐式耦合，断点续跑时无法可靠地推导文件名。

**三种文件的语义边界（不可混淆）**：

| 文件 | 语义 | 谁写 | 谁读 |
|------|------|------|------|
| `report.md` | 最终投资报告 | Stage4 投资顾问 AI | 用户日常阅读 |
| `agents/*.md` | 各智能体的 AI 输出 | 各阶段 AI 智能体 | 溯源：AI 说了什么 |
| `data/*_data.md` | 工具注入的原始数据 | tool_injector 在调用 AI 前写入 | 溯源：AI 依据什么数据 |

### 1.2 data 文件格式

每个 `data/*_data.md` 文件带固定头部，说明是哪个智能体调用了哪些工具：

```markdown
# 数据依据 · 技术分析师 (technical_analyst)

**调用智能体**: 技术分析师 (technical_analyst)
**所属阶段**: Stage 1
**使用工具**: price_tool, indicator_tool
**记录时间**: 2026-03-22 09:30:15

---

## price_tool 输出

[实际 K线/均线/布林带数据 Markdown]

---

## indicator_tool 输出

[实际 MACD/RSI/KDJ 数据 Markdown]
```

### 1.3 路径推导规则

`task_id` 格式固定为 `TASK_{YYYYMMDD}_{HHMMSS}_{HEX6}`。

**已知 stock_code 时（创建、更新任务）**：

```python
def task_folder(task_id: str, stock_code: str) -> Path:
    date = task_id.split("_")[1]          # 提取 "20260322"
    return settings.tasks_dir / date / stock_code / task_id

def task_file(task_id: str, stock_code: str) -> Path:
    return task_folder(task_id, stock_code) / "task.json"
```

**未知 stock_code 时（通过 task_id 查找）**：

```python
def find_task_folder(task_id: str) -> Path | None:
    date = task_id.split("_")[1]
    # pathlib.glob 对 . 无特殊处理，stock_code 中的 . 是安全的
    matches = list(settings.tasks_dir.glob(f"{date}/*/{task_id}"))
    return matches[0] if matches else None
```

**列举所有任务**：`settings.tasks_dir.glob("*/*/*/task.json")`（三级通配）

**性能说明**：任务总量在数千以内时 glob 性能完全可接受；若未来需要处理数万任务，可引入 SQLite 索引，但当前不在范围内。

---

## 二、task.json 新字段

在原有字段基础上新增三个字段：

```json
{
  "task_id": "TASK_20260322_135954_E544B3",
  "stock_code": "000001.SZ",
  "status": "running",
  "current_stage": "stage2",
  "current_agent": "bear_researcher",
  "stage_progress": {
    "stage1": "completed",
    "stage2": "running",
    "stage3": "pending",
    "stage4": "pending"
  },
  "stages_completed": ["stage1"],
  "resume_count": 0,
  "logs": ["..."],
  "created_at": "2026-03-22T09:25:52",
  "started_at": "2026-03-22T09:25:53",
  "completed_at": null,
  "report_path": null,
  "error": null,
  "note": null
}
```

| 字段 | 类型 | 用途 |
|------|------|------|
| `current_agent` | `str \| null` | 当前正在执行的智能体 ID，供前端实时展示 |
| `stages_completed` | `list[str]` | 已完整完成的阶段列表，**断点续跑的唯一依据** |
| `resume_count` | `int` | 累计断点续跑次数（用于日志追踪） |

**`stages_completed` 与 `stage_progress` 的分工**：
- `stage_progress`：前端实时展示用，粒度到"running/completed/pending"
- `stages_completed`：断点续跑专用，只有一个阶段全部智能体输出都已落盘后才追加该阶段

两者由 **`task_store.update_task`** 内部负责同步（当传入的 `stage_progress[X] = StageStatus.COMPLETED` 时，自动追加 `X` 到 `stages_completed`）。同步逻辑写在 `task_store.py` 的 `update_task` 函数体内（与现有合并逻辑并列），调用方不需要手动维护 `stages_completed`，`models.py` 的 `TaskRecord` 只做字段声明。

**所有时间戳统一使用 naive datetime（无时区）**，与现有序列化行为一致，避免 `offset-naive` 与 `offset-aware` 相减报错。

---

## 三、断点续跑机制

### 3.1 核心原则

**文件系统即检查点**：以 `agents/` 目录下已存在的文件为唯一真相来源，不维护额外的 checkpoint 状态。`stages_completed` 是这个真相的 JSON 层镜像（启动时二者互相验证）。

**Stage1 是原子单元**：Stage1 的分析师并行运行，任意中断意味着 "有的跑了有的没跑"，基础数据不完整不应进入 Stage2。若 Stage1 未完整完成 → 任务 FAILED，用户重新提交。

**Stage2/3/4 可在智能体粒度续跑**：逐一检查各智能体对应的 `.md` 输出文件，确定续跑起点。

### 3.2 服务重启恢复流程

```
服务启动
  → _recover_orphaned_tasks()
      对每个 status=RUNNING 的任务：

        Case A: "stage1" not in stages_completed
          → 追加日志："Stage1未完成，服务重启，任务标记为失败"
          → update_task(status=FAILED, error="服务重启，Stage1未完成，请重新提交")

        Case B: "stage1" in stages_completed
          → 追加日志："服务重启，任务将从断点续跑（第N次）"
          → task = get_task(task_id)
          → update_task(task_id,
                        status=PENDING,
                        resume_count=task.resume_count + 1)  ← 先读后写，不能传固定值
          → task_queue.enqueue(task_id)  ← 正常入队，不插队
```

### 3.3 续跑时的 orchestrator 执行逻辑

```
orchestrator 收到续跑任务（resume_count > 0）后：

1. 重新拉取市场数据（始终获取最新行情，不缓存历史数据）
2. 重新计算指标（calculator 是纯函数，耗时可忽略）
3. 加载市场规则和技能列表
4. 检测续跑起点（按顺序，以文件是否存在为判断依据）：

   ─ Stage1：检查所有配置的 Stage1 agent_id 对应文件是否全部存在
       全部存在 → 从磁盘加载 Stage1Results，跳过 Stage1 执行
       否       → 正常执行完整 Stage1（全部重跑）

   ─ Stage2：检查 stage2_research_director.md 是否存在
       存在  → 从磁盘加载整个 Stage2Results，跳过 Stage2 执行
       不存在 → 检测 Stage2 内部续跑点（见 3.4）

   ─ Stage3：检查 stage3_chief_risk_officer.md 是否存在
       存在  → 从磁盘加载整个 Stage3Results，跳过 Stage3 执行
       不存在 → 检测 Stage3 内部续跑点（见 3.4）

   ─ Stage4：检查 report.md 是否存在
       存在  → 任务实际已完成，直接更新状态为 COMPLETED（异常情况，理论上不会到这里）
       不存在 → 正常执行 Stage4
```

### 3.4 Stage 内部续跑点检测

**Stage2**（Round0 是并行整体，Rounds1..N 和后续智能体是串行）：

```python
def detect_stage2_resume_point(agents_dir: Path, debate_rounds: int) -> str:
    """
    返回值含义：
      "start"           → 从 Round 0 开始（并行）
      "bull_r{N}"       → 从第 N 轮看涨方反驳开始（N >= 1）
      "bear_r{N}"       → 从第 N 轮看跌方反驳开始
      "director"        → 从研究主管开始
      "trading_planner" → 从交易计划师开始
      "complete"        → Stage2 完整完成
    """
    # Round 0 是并行整体：两个文件要么都在，要么都不在
    # 若任意一个不存在，从头重跑整个 Round 0（并行 gather 是原子的）
    r0_bull = agents_dir / "stage2_bull_r0.md"
    r0_bear = agents_dir / "stage2_bear_r0.md"
    if not (r0_bull.exists() and r0_bear.exists()):
        return "start"

    # Rounds 1..N 是串行的，逐个检查
    for i in range(1, debate_rounds + 1):
        if not (agents_dir / f"stage2_bull_r{i}.md").exists():
            return f"bull_r{i}"
        if not (agents_dir / f"stage2_bear_r{i}.md").exists():
            return f"bear_r{i}"

    if not (agents_dir / "stage2_research_director.md").exists():
        return "director"
    if not (agents_dir / "stage2_trading_planner.md").exists():
        return "trading_planner"

    return "complete"
```

**Stage3**（风控师是并行整体，CRO 是串行）：

```python
def detect_stage3_resume_point(agents_dir: Path) -> str:
    """
    返回值：
      "risk_managers" → 三位风控师并行组整体重跑（含 VaR 计算，纯代码耗时可忽略）
      "cro"           → 三位风控师报告已在磁盘，仅需运行首席风控官
      "complete"      → Stage3 完整完成

    重要：续跑点为 "cro" 时，VaR 计算仍然必须重新执行（纯代码，无副作用），
    因为 CRO 的上下文（risk_ctx）由 format_risk_results(var_result, ...) 生成，
    risk_ctx 本身不落盘，不能从磁盘恢复。实现者不得跳过 VaR 计算步骤。
    """
    rm_files = [
        "stage3_aggressive_risk_manager.md",
        "stage3_conservative_risk_manager.md",
        "stage3_quant_risk_manager.md",
    ]
    # 并行组要么全在要么全不在，有缺失则整体重跑
    if not all((agents_dir / f).exists() for f in rm_files):
        return "risk_managers"
    if not (agents_dir / "stage3_chief_risk_officer.md").exists():
        return "cro"
    return "complete"
```

### 3.5 从磁盘重建 Stage 结果对象

**Stage1Results 加载**（文件名使用完整 agent_id，不做任何截断或变换）：

```python
def load_stage1_results_from_disk(task_folder: Path) -> Stage1Results:
    """
    从 agents/ 目录加载所有 Stage1 智能体的输出，重建 Stage1Results。
    文件名规则：agents/stage1_{agent_id}.md
    """
    from src.agents.config_loader import get_stage1_agents
    agents = get_stage1_agents()  # [(agent_id, display_name), ...]
    results = Stage1Results(display_names={aid: dname for aid, dname in agents})
    agents_dir = task_folder / "agents"

    for agent_id, _ in agents:
        file = agents_dir / f"stage1_{agent_id}.md"
        if file.exists():
            results.reports[agent_id] = file.read_text(encoding="utf-8")
        else:
            results.reports[agent_id] = f"[{agent_id} 报告文件缺失，断点续跑时无法加载]"

    return results
```

**Stage2Results 加载**：

> **前置条件**：此函数只在 `stage2_research_director.md` 已确认存在后调用（即 orchestrator 检测到 Stage2 完整完成时）。调用方负责保证前置条件成立，函数内部不做额外检查。

> **运维警告**：`debate_rounds` 参数来自运行时配置 `settings.debate_rounds`。若某个任务中断后，`DEBATE_ROUNDS` 配置被修改（如从 2 改为 1），续跑时传入的 `debate_rounds` 与磁盘文件数量不一致，可能导致部分辩论轮次被跳过。建议将 `DEBATE_ROUNDS` 视为任务级别的固定参数，服务运行期间不更改；若必须更改，应等待所有进行中任务完成后再重启服务。

```python
def load_stage2_results_from_disk(task_folder: Path, debate_rounds: int) -> Stage2Results:
    """
    debate_rounds 必须与任务创建时的配置一致，见上方运维警告。
    仅在 stage2_research_director.md 已存在时调用（前置条件由调用方保证）。
    """
    agents_dir = task_folder / "agents"
    bull_rounds, bear_rounds = [], []
    for i in range(debate_rounds + 1):
        f_bull = agents_dir / f"stage2_bull_r{i}.md"
        f_bear = agents_dir / f"stage2_bear_r{i}.md"
        bull_rounds.append(f_bull.read_text("utf-8") if f_bull.exists() else "")
        bear_rounds.append(f_bear.read_text("utf-8") if f_bear.exists() else "")
    director = (agents_dir / "stage2_research_director.md").read_text("utf-8")
    plan = (agents_dir / "stage2_trading_planner.md").read_text("utf-8")
    return Stage2Results(
        bull_rounds=bull_rounds, bear_rounds=bear_rounds,
        director_report=director, trading_plan=plan,
    )
```

---

## 四、智能体失败处理策略

### 4.1 Stage1（并行分析师）

```
每个分析师的 LLM 调用已有 3 次 per-call 重试（llm_client 层）
超出重试 →
  写入错误占位文件：agents/stage1_{agent_id}.md（内容为错误说明）
  继续等待其他分析师完成（return_exceptions=True 行为不变）

全部配置的分析师都失败 →
  Stage1 FAILED → 任务 FAILED（无意义继续）
```

Stage1 某个维度失败不阻塞整体：5 个正常维度仍可支撑 Stage2 辩论，报告中标注"XXX维度不可用"。

### 4.2 Stage2（关键串行智能体）

| 智能体 | 失败后行为 |
|--------|-----------|
| bull / bear（辩论） | Stage 级重试 ×2（30s 间隔）；仍失败 → 写入错误占位文件，继续后续步骤 |
| research_director | Stage 级重试 ×2；仍失败 → 写入"研究主管分析不可用"占位文件，继续 Stage3 |
| trading_planner | Stage 级重试 ×2；仍失败 → 写入错误占位文件，继续 Stage3 |

### 4.3 Stage3（风控层）

| 智能体 | 失败后行为 |
|--------|-----------|
| 三位风控师（并行） | `return_exceptions=True`；单个失败 → 错误占位文件；三个全失败 → CRO 仍运行，报告标注数据质量差 |
| chief_risk_officer | Stage 级重试 ×2；仍失败 → 写入错误占位文件，继续 Stage4，最终报告中标注"风控裁决不可用" |

### 4.4 Stage4（投资顾问）

Stage 级重试 ×2；仍失败 → 任务 FAILED。
前三阶段所有报告已落盘，用户可查阅 `agents/` 下的中间报告。

### 4.5 Stage 级重试实现

```python
async def run_with_stage_retry(
    coro_factory: Callable[[], Coroutine],
    max_retries: int = 2,
    delay: float = 30.0,
) -> Any:
    """
    对关键智能体调用提供 stage 级重试保障。
    coro_factory 是返回 coroutine 的可调用对象（每次重试需要新的 coroutine）。
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except asyncio.CancelledError:
            raise  # 取消信号不重试，立即传播
        except Exception as e:
            last_exc = e
            if attempt < max_retries:
                logger.warning(
                    f"Stage 级重试 {attempt + 1}/{max_retries}，"
                    f"将在 {delay}s 后重试，错误: {e}"
                )
                await asyncio.sleep(delay)
    raise last_exc  # 超出重试次数，向上抛出
```

---

## 五、删除任务 API

`DELETE /api/v1/tasks/{task_id}` 的新语义：**永久删除整个任务文件夹**，不可恢复。

```
任何状态均可删除：

PENDING   →
  1. task_queue.cancel(task_id)（从内存队列移除）
  2. shutil.rmtree(task_folder)

RUNNING   →
  1. task_queue.cancel(task_id)（发送取消信号）
  2. await asyncio.wait_for(cancel_done_event, timeout=5.0)
     （在路由 handler 中使用 asyncio.wait_for，不能用 time.sleep，避免阻塞事件循环）
  3. shutil.rmtree(task_folder)（超时后强制删除）

其他终态  →
  shutil.rmtree(task_folder)

删除内容：tasks/{date}/{stock_code}/{task_id}/
  └─ task.json, report.md, agents/*, data/*

返回：204 No Content
```

**RUNNING 任务删除时等待取消的实现**：在 `task_queue` 中为每个 running 任务维护一个 `done_event`，pipeline 完成（正常结束或 CancelledError 捕获后）时 set 该 event，DELETE handler `await wait_for(done_event, 5)` 即可。

---

## 六、自动清理机制

### 6.1 清理规则

| 任务状态 | 默认保留期 | 配置项 |
|---------|-----------|--------|
| `completed` | 永久（0 = 永不删除） | `COMPLETED_TASK_RETENTION_DAYS=0` |
| `failed` / `cancelled` | 7 天 | `FAILED_TASK_RETENTION_DAYS=7` |

`COMPLETED_TASK_RETENTION_DAYS=30` 表示 30 天后删除；`=0` 表示永久保留。

### 6.2 清理时机

服务每次启动时，在后台异步执行一次清理扫描（不阻塞启动流程）。

### 6.3 清理逻辑

```python
async def cleanup_expired_tasks() -> None:
    now = datetime.now()  # naive datetime，与 task.json 序列化保持一致

    # 使用 "*/*/*/task.json" 精确匹配任务文件，避免误匹配子目录
    for task_json in settings.tasks_dir.glob("*/*/*/task.json"):
        task_folder = task_json.parent
        task = get_task_from_file(task_json)

        if task.status == TaskStatus.COMPLETED:
            days = settings.completed_task_retention_days
            if days == 0:
                continue  # 永久保留
            ref_time = task.completed_at or task.created_at

        elif task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            days = settings.failed_task_retention_days
            ref_time = task.completed_at or task.created_at

        else:
            continue  # PENDING/RUNNING 不清理（由 _recover_orphaned_tasks 处理）

        if (now - ref_time).days >= days:
            shutil.rmtree(task_folder, ignore_errors=True)
            logger.info(
                f"[清理] 已删除过期任务 {task.task_id}"
                f"（状态={task.status}，超过 {days} 天）"
            )
```

---

## 七、应用日志管理

### 7.1 日志策略

保留现有 loguru 的 `rotation="00:00"` 按天轮转方案，**不引入额外的启动时手动清理**（loguru 内置 retention 已经可靠处理，重写会增加复杂度）。

调整内容：将现有的 `log_retention_days`（30天）配置项直接复用，不新增 `LOG_MAX_FILES`。

**唯一调整**：将日志文件名格式改为包含启动时间戳，便于区分不同运行实例：

```python
# 在 setup_logging() 中，将文件 handler 改为：
startup_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = settings.log_dir / f"app_{startup_ts}.log"

logger.add(
    log_file,
    level=settings.log_level,
    rotation="00:00",                          # 每天自动轮转（跨天时创建新文件）
    retention=f"{settings.log_retention_days} days",  # loguru 管理清理
    encoding="utf-8",
    enqueue=True,
)
```

这样每次启动会产生 `app_20260322_134100.log`，跨天后 loguru 自动创建新文件，无需额外管理。

---

## 八、新增 / 修改的配置项

```env
# 任务保留（新增）
COMPLETED_TASK_RETENTION_DAYS=0    # 0=永久保留已完成任务，>0=保留天数
FAILED_TASK_RETENTION_DAYS=7       # 失败/取消任务保留天数

# 日志（已有，不变）
LOG_RETENTION_DAYS=7               # 保留天数，直接沿用现有配置项名称
# 删除：LOG_MAX_FILES（不引入，loguru retention 已足够）
```

`config.py` 对应新增：

```python
completed_task_retention_days: int = 0   # 0 = 永久
failed_task_retention_days: int = 7
```

---

## 九、影响的文件清单

| 文件 | 变更类型 | 核心说明 |
|------|---------|---------|
| `src/core/models.py` | 修改 | 新增 `current_agent`、`stages_completed`、`resume_count` 字段；`update_task` 内部自动同步 `stages_completed` |
| `src/core/task_store.py` | 重写 | 路径改为三级文件夹；新增 `task_folder()`、`find_task_folder()`、`save_agent_output()`、`save_data_evidence()`、`load_agent_output()` 等 |
| `src/pipeline/orchestrator.py` | 修改 | 新增断点续跑检测逻辑；调用 `save_agent_output` / `save_data_evidence` |
| `src/pipeline/stage1.py` | 修改 | 每个分析师完成后立即落盘；保存 data evidence |
| `src/pipeline/stage2.py` | 修改 | 每个智能体完成后立即落盘；关键智能体加 `run_with_stage_retry` |
| `src/pipeline/stage3.py` | 修改 | 同上 |
| `src/pipeline/stage4.py` | 修改 | 报告写入任务文件夹（而非 `reports/YYYYMMDD/`）；加 stage 级重试 |
| `src/tools/tool_injector.py` | 修改 | `inject_tools` 支持将注入数据写入 data evidence 文件（可选参数） |
| `src/main.py` | 修改 | 启动时依次执行：清理过期任务、恢复僵尸任务；不再手动清理日志 |
| `src/api/routes/tasks.py` | 修改 | DELETE 改为删除整个任务文件夹；支持 RUNNING 任务等待取消 |
| `src/logging_config.py` | 修改 | 文件名加启动时间戳；rotation/retention 由 loguru 管理 |
| `src/config.py` / `.env.example` | 修改 | 新增 `completed_task_retention_days` / `failed_task_retention_days` |

---

## 十、实施补充说明

**`run_with_stage_retry` 模块归属**：新建 `src/pipeline/utils.py`，在此定义 `run_with_stage_retry`，各 Stage 文件均可导入使用。

**`settings.reports_dir` 配置项**：新设计中报告写入任务文件夹（`tasks/{date}/{stock_code}/{task_id}/report.md`），`reports/` 独立目录不再使用。`settings.reports_dir` 配置项从 `config.py` 和 `.env.example` 中移除，`mkdir` 调用从 `main.py` 的 lifespan 中删除。

**`run_pipeline` 签名**：续跑检测逻辑由 `orchestrator.run_pipeline` 内部从 `task_store.get_task(task_id)` 读取 `resume_count` 和 `stages_completed`，不需要在函数签名中新增参数（保持 `scheduler` 调用侧不变）。

## 十一、不在本次范围内

- 任务优先级队列（续跑任务按正常顺序排队，不插队）
- 历史报告浏览 API（现有 `GET /tasks/{id}/report` 已足够）
- SQLite 索引（任务量达数万级别时再引入）
