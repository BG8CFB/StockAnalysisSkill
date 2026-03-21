# 日志系统设计文档

**项目**：Stock Analysis Skill
**日期**：2026-03-22
**状态**：待实现

---

## 一、背景与问题

### 1.1 现状

Stock Analysis Skill 是一个多智能体股票分析 HTTP 服务，作为 AI 技能在后台运行。当前日志系统存在以下问题：

1. **日志不可见**：`src/main.py` 中的 `logging.basicConfig()` 在 uvicorn 启动后才执行，root logger 已有 handler，basicConfig 是 no-op，自定义格式完全不生效。
2. **关键步骤无日志**：API 层（任务创建/取消）、数据清洗、指标计算、各 Agent 启动均无日志。
3. **日志语言**：现有日志全为英文，不符合项目要求。
4. **无文件持久化**：日志只输出到终端（若可见），无文件记录，服务重启后历史丢失。
5. **幂等性缺失**：`POST /api/v1/tasks` 无重复检测，AI 上下文重置后会对同一股票重复提交任务，浪费 LLM 调用资源。

### 1.2 存储目录现状

```
./tasks/      任务记录 JSON（已有）
./reports/    分析报告 Markdown（已有）
./logs/       ❌ 不存在
```

---

## 二、技术选型：loguru

选用 [loguru](https://github.com/Delgan/loguru) 替代标准 `logging` 模块。

### 选型理由

| 能力 | 标准 logging | loguru |
|------|-------------|--------|
| 初始化代码量 | 30-50 行 | 5 行 |
| 彩色终端输出 | 需第三方库 | 内置 |
| 异步安全写文件 | 手动配置 Queue | `enqueue=True` |
| 按日轮转 + 自动清理 | RotatingFileHandler | 一行参数 |
| 任务上下文绑定 | 自定义 Filter 类 | `logger.bind(task_id=...)` |
| 拦截标准库 logging | 不需要 | InterceptHandler（5行） |
| 异常堆栈美化 | 标准输出 | 彩色变量值展示 |

项目处于开发阶段，loguru 提供最佳开发体验，同时具备生产级文件管理能力。

### 兼容性

现有代码全部使用 `logging.getLogger(__name__)` 模式，通过 `InterceptHandler` 拦截后统一转发给 loguru，**现有代码无需修改 import**。

---

## 三、架构设计

### 3.1 日志初始化模块

新建 `src/logging_config.py`，提供 `setup_logging()` 函数：

```
setup_logging()
├── 移除 loguru 默认 handler
├── 添加终端 handler（彩色，可通过 LOG_CONSOLE_ENABLED=false 关闭）
├── 添加文件 handler（按日轮转，保留 30 天，UTF-8，异步写入）
└── 添加 InterceptHandler 拦截所有 logging.getLogger() 输出
```

**调用时机**：在 `src/main.py` 最顶部，任何其他 import 之前调用，确保早于 uvicorn 初始化。

### 3.2 日志格式

**终端格式（开发友好，彩色）：**
```
2026-03-22 14:30:01 | INFO     | pipeline.orchestrator | [TASK_xxx][000001.SZ] 流水线启动
```

**文件格式（调试详细，含行号）：**
```
2026-03-22 14:30:01.234 | INFO     | pipeline/orchestrator.py:run_pipeline:38 | [TASK_xxx][000001.SZ] 流水线启动
```

### 3.3 文件存储

```
./logs/
  app-20260322.log    每日 00:00 轮转
  app-20260323.log    保留 30 天后自动删除
  ...
```

`log_dir` 通过 `config.py` 可配置（`LOG_DIR` 环境变量）。

### 3.4 任务上下文绑定

在 `orchestrator.py` 及各 Stage 中，通过 `logger.bind()` 绑定任务上下文，使同一任务的所有日志可被追踪：

```python
# orchestrator.py
task_logger = logger.bind(task_id=task_id, stock_code=stock_code)
task_logger.info("流水线启动")
# 输出: [TASK_xxx][000001.SZ] 流水线启动
```

bind 后的 logger 向下传递给各 Stage 函数（通过参数），确保全链路日志携带任务标识。

---

## 四、配置项扩展

在 `src/config.py` 新增：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `log_dir` | `Path` | `./logs` | 日志文件目录 |
| `log_level` | `str` | `"INFO"` | 日志级别 |
| `log_console_enabled` | `bool` | `True` | 是否输出到终端（后台运行时设 false） |
| `log_retention_days` | `int` | `30` | 日志文件保留天数 |

---

## 五、日志覆盖规范

### 5.1 服务生命周期（`main.py` / `scheduler.py`）

```
[服务] 日志系统初始化完成 → 文件: ./logs/app-20260322.log
[服务] Stock Analysis Skill 启动 → 监听 127.0.0.1:8080，Worker 数: 5
[服务] Worker-0 就绪，等待任务
[服务] 正在关闭 → 等待 Worker 完成...
[服务] 全部 Worker 已停止，服务退出
```

### 5.2 API 层（`api/routes/tasks.py`）

```
[API] 收到分析请求 → 股票: 000001.SZ
[API] 任务创建成功 → task_id: TASK_xxx，队列位置: 1
[API] 发现重复任务 → 000001.SZ 已有进行中任务 TASK_xxx，直接返回
[API] 队列已满（100/100），拒绝请求
[API] 任务取消请求 → task_id: TASK_xxx
```

### 5.3 数据获取层（`data/tushare_adapter.py` / `akshare_adapter.py`）

```
[Tushare] 开始拉取 000001.SZ（20250116 → 20260322）
[Tushare] ✓ 日线行情: 120 条
[Tushare] ✓ 每日基本面指标: PE=12.3，总市值=1234亿
[Tushare] ✗ 资金流向拉取失败，已跳过（原因: 权限不足）
[AkShare] 开始补充缺失字段: 板块、资讯、财务
[AkShare] ✓ 已补充 3 个字段
```

### 5.4 数据处理层（`data/cleaner.py` / `data/calculator.py`）

```
[清洗] 000001.SZ 开始数据清洗
[清洗] ✓ 前复权完成，有效记录: 120 条
[清洗] ⚠ 检测到停牌，跳过后续分析
[指标] 000001.SZ 开始计算技术指标
[指标] ✓ 全部指标计算完成（MACD/RSI/KDJ/布林带/均线/VaR）
```

### 5.5 流水线各阶段（`pipeline/`）

```
[Pipeline][TASK_xxx][000001.SZ] 任务启动
[Stage1][TASK_xxx] 启动 6 个分析师并行 → 技术/基本面/微观结构/情绪/板块/资讯
[Stage1][TASK_xxx] ▶ 技术分析师 开始
[Stage1][TASK_xxx] ✓ 技术分析师 完成（3.2s，1842字）
[Stage1][TASK_xxx] ✓ 全部 6 位分析师完成
[Stage2][TASK_xxx] 多空辩论 第1轮 → 多头 2341字 / 空头 2156字
[Stage2][TASK_xxx] ✓ 研究主管综合报告完成（3120字）
[Stage2][TASK_xxx] ✓ 交易计划书完成（2890字）
[Stage3][TASK_xxx] VaR 计算 → 资本基数 ¥100,000
[Stage3][TASK_xxx] ✓ VaR(95%, 1日) = 2.31% / ¥2,310
[Stage3][TASK_xxx] ✓ 3 位风控经理并行完成
[Stage3][TASK_xxx] ✓ 首席风控官裁决完成（2345字）
[Stage4][TASK_xxx] 投资顾问开始生成最终报告
[Stage4][TASK_xxx] ✓ 报告已保存 → reports/20260322/000001.SZ_TASK_xxx.md
[Pipeline][TASK_xxx] 任务完成，总耗时 18m32s
```

### 5.6 LLM 调用层（`agents/llm_client.py` / `agents/base_agent.py`）

```
[LLM][TASK_xxx] 调用 gpt-4o → 角色: 技术分析师（系统 2341字 + 用户 4521字）
[LLM][TASK_xxx] ✓ 技术分析师 响应（3.2s，1842字）
[LLM][TASK_xxx] ⚠ 限流，1.5s 后重试（第 2/3 次）
[LLM][TASK_xxx] ✗ 技术分析师 调用失败（不可重试: 401 Unauthorized）
```

---

## 六、幂等性设计

### 6.1 问题描述

AI 上下文重置后重新读取技能文档，可能对同一股票重复提交分析任务。当前 `create_task()` 无任何重复检测，导致资源浪费（每次任务消耗 15+ 次 LLM 调用，耗时 15-25 分钟）。

### 6.2 方案

在 `src/core/task_store.py` 新增 `find_active_task(stock_code)` 函数，查找该股票的 PENDING 或 RUNNING 任务。

在 `src/api/routes/tasks.py` 的 `create_task_endpoint` 中：

```
1. 接收 POST /api/v1/tasks 请求
2. 验证 stock_code 格式
3. 调用 find_active_task(stock_code)
   - 若找到 PENDING/RUNNING 任务 → 返回 HTTP 200，附 is_existing: true
   - 若无活跃任务 → 正常创建，返回 HTTP 201，附 is_existing: false
```

**状态判断规则：**
- `PENDING` / `RUNNING` → 返回现有任务（任务进行中，无需重复）
- `COMPLETED` / `FAILED` / `CANCELLED` → 允许新建（可重新分析）

### 6.3 响应模型变更

`CreateTaskResponse` 新增字段：
```python
is_existing: bool = False  # True 表示返回的是已有任务
```

---

## 七、改动文件清单

| 文件 | 改动类型 | 主要内容 |
|------|---------|---------|
| `pyproject.toml` | 修改 | 新增 loguru 依赖 |
| `src/config.py` | 修改 | 新增 log_dir / log_level / log_console_enabled / log_retention_days |
| `src/logging_config.py` | **新建** | loguru 初始化，InterceptHandler，setup_logging() |
| `src/main.py` | 修改 | 最顶部调用 setup_logging()，移除旧 basicConfig |
| `src/api/routes/tasks.py` | 修改 | 补全 API 日志 + 幂等检查 |
| `src/core/task_store.py` | 修改 | 新增 find_active_task() |
| `src/data/tushare_adapter.py` | 修改 | 中文日志全覆盖，补充成功日志 |
| `src/data/akshare_adapter.py` | 修改 | 中文日志全覆盖，补充成功日志 |
| `src/data/cleaner.py` | 修改 | 新增清洗阶段日志 |
| `src/data/calculator.py` | 修改 | 新增指标计算日志 |
| `src/pipeline/orchestrator.py` | 修改 | 总耗时计算，中文化，传递 bound logger |
| `src/pipeline/stage1.py` | 修改 | 逐 Agent 启动/完成日志 |
| `src/pipeline/stage2.py` | 修改 | 辩论轮次日志中文化 |
| `src/pipeline/stage3.py` | 修改 | 日志中文化 |
| `src/pipeline/stage4.py` | 修改 | 日志中文化 |
| `src/agents/base_agent.py` | 修改 | 中文化 |
| `src/agents/llm_client.py` | 修改 | 补 INFO 调用日志，中文化 |
| `src/core/scheduler.py` | 修改 | 中文化 |

共 **17 个文件**，其中 1 个新建。

---

## 八、不在本次范围内

- 日志聚合平台（ELK/Loki）集成：当前开发阶段不需要
- 结构化 JSON 日志输出：开发阶段可读性优先，如后续有运维需求可扩展
- 访问日志（HTTP 请求/响应详细记录）：uvicorn 已提供基础访问日志
