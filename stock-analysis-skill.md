# Stock Analysis Skill

> **适用场景**：需要对A股/港股/美股进行多维度量化分析、生成投资研究报告时使用。
> **分析模式**：11个AI智能体组成4阶段流水线，覆盖技术/基本面/资金流/风控等维度。
> **输出格式**：结构化Markdown报告，含交易计划、风控裁决、投资建议。

---

## 一、服务概述

Stock Analysis Skill 是一个多智能体股票分析服务，通过 HTTP API 提供服务。

- **服务地址**：`http://127.0.0.1:8888`
- **分析标的**：A股（.SZ/.SH）、港股（.HK）、美股（AAPL等）
- **分析时长**：约 15-25 分钟（4阶段流水线，含11个LLM智能体）
- **并发支持**：最多5个任务同时分析，队列最大100个

---

## 二、启动服务（必须先执行）

**服务需要手动启动，每次使用前请先确保服务运行。**
启动脚本是幂等的——无论服务是否已在运行都可以安全调用。

```bash
# 确保服务运行（已运行则直接返回，未运行则自动启动）
bash /root/Code/StockAnalysisSkill/scripts/start.sh

# 停止服务
bash /root/Code/StockAnalysisSkill/scripts/stop.sh
```

**start.sh 输出（JSON）**：
```json
{"status": "running",  "port": 8888, "pid": 12345, "started": false}
{"status": "started",  "port": 8888, "pid": 12345, "started": true}
{"status": "error",    "port": 8888, "error": "描述..."}
```

> 启动成功后（status 为 running 或 started）才可调用以下 API。

---

## 三、调用步骤

```
1. bash scripts/start.sh          → 确保服务运行
2. POST /api/v1/tasks             → 创建分析任务（返回 task_id）
3. GET  /api/v1/tasks/{id}        → 轮询任务状态（每30秒一次）
4. GET  /api/v1/tasks/{id}/report → 任务完成后获取Markdown报告
5. DELETE /api/v1/tasks/{id}      → 如需中止，取消并删除任务
```

---

## 四、API详细说明

### 4.1 创建分析任务

**接口**：`POST http://127.0.0.1:8888/api/v1/tasks`

**请求体**：
```json
{"stock_code": "000001.SZ", "note": "可选备注"}
```

**stock_code 格式**：
| 市场 | 格式 | 示例 |
|-----|------|------|
| A股 | 6位数字.SZ或.SH | 000001.SZ、600519.SH |
| 港股 | 5位数字.HK | 00700.HK |
| 美股 | 1-5位字母 | AAPL、BRK.A |

**成功响应（201 — 新任务）**：
```json
{"task_id": "TASK_20260322_143000_A1B2C3", "status": "pending", "stock_code": "000001.SZ", "queue_position": 1, "is_existing": false}
```

**成功响应（200 — 同股票已有进行中任务）**：
```json
{"task_id": "TASK_20260322_143000_A1B2C3", "status": "running", "stock_code": "000001.SZ", "queue_position": 0, "is_existing": true}
```

**错误响应**：422（格式不正确）、429（队列已满）

---

### 4.2 查询任务状态

**接口**：`GET http://127.0.0.1:8888/api/v1/tasks/{task_id}`

**响应关键字段**：
```json
{
  "task_id": "TASK_20260322_143000_A1B2C3",
  "status": "running",
  "current_stage": "stage2",
  "current_agent": "research_director",
  "stage_progress": {"stage1": "completed", "stage2": "running", "stage3": "pending", "stage4": "pending"},
  "stages_completed": ["stage1"],
  "resume_count": 0,
  "logs": ["[14:30:05] [Pipeline] 任务启动...", "..."],
  "error": null,
  "report_path": null
}
```

---

### 4.3 获取分析报告

**接口**：`GET http://127.0.0.1:8888/api/v1/tasks/{task_id}/report`

仅当 status == "completed" 时可调用，否则返回 400。

**响应**：
```json
{
  "task_id": "TASK_20260322_143000_A1B2C3",
  "stock_code": "000001.SZ",
  "content": "# 平安银行(000001.SZ) 投资分析报告\n\n## 执行摘要\n...",
  "completed_at": "2026-03-22T14:52:30.000000"
}
```

---

### 4.4 删除任务

**接口**：`DELETE http://127.0.0.1:8888/api/v1/tasks/{task_id}`

删除任务及其全部文件（含 agents/ data/ report.md）。PENDING/RUNNING 状态会先发取消信号等待最多5s。

**成功响应**：204 No Content

---

### 4.5 列出任务

**接口**：`GET http://127.0.0.1:8888/api/v1/tasks?status=running&limit=20&offset=0`

status 可选：pending、running、completed、failed、cancelled

---

### 4.6 健康检查

**接口**：`GET http://127.0.0.1:8888/api/v1/health`

```json
{"status": "ok", "queue_size": 2, "running_tasks": 3, "max_concurrent_tasks": 5}
```

---

## 五、任务状态说明

| 状态 | 含义 |
|-----|------|
| pending | 已加入队列，等待 worker 处理 |
| running | 流水线正在运行（current_stage 显示当前阶段） |
| completed | 分析完成，报告可读取 |
| failed | 发生错误（error 字段含错误信息） |
| cancelled | 已取消并删除 |

---

## 六、分析阶段说明

| 阶段 | 内容 | 耗时估计 |
|-----|------|---------|
| Stage 1 | 数据获取 + 6位专项分析师并行（技术/基本面/微观结构/情绪/板块/资讯） | 3-5分钟 |
| Stage 2 | 多空辩论（2轮）+ 研究主管裁决 + 交易计划师 | 5-8分钟 |
| Stage 3 | VaR计算 + 3位风控师并行（激进/保守/量化）+ 首席风控官裁决 | 3-6分钟 |
| Stage 4 | 投资顾问综合报告生成 | 2-4分钟 |

---

## 七、任务文件结构

```
tasks/{日期}/{股票代码}/{task_id}/
├── task.json          # 任务状态、进度、日志
├── report.md          # 最终投资报告
├── agents/            # 各智能体 AI 输出（溯源）
│   ├── stage1_technical_analyst.md
│   ├── stage2_bull_r0.md
│   ├── stage2_research_director.md
│   └── stage3_chief_risk_officer.md ...
└── data/              # 注入智能体的原始工具数据（溯源）
    ├── stage1_technical_analyst_data.md
    └── stage4_investment_advisor_data.md ...
```

---

## 八、断点续跑

服务崩溃或重启后，已完成部分阶段的任务自动恢复：
- 已完成的 Stage 直接从磁盘加载，不重新调用 LLM
- Stage 2 / Stage 3 支持智能体级断点
- resume_count 字段记录续跑次数

---

## 九、推荐调用流程

```python
import subprocess, requests, time, json

# 0. 确保服务运行
r = subprocess.run(["bash", "/root/Code/StockAnalysisSkill/scripts/start.sh"],
                   capture_output=True, text=True)
result = json.loads(r.stdout)
if result["status"] == "error":
    raise RuntimeError("服务启动失败: " + result["error"])

BASE = "http://127.0.0.1:8888"

# 1. 创建任务
resp = requests.post(f"{BASE}/api/v1/tasks", json={"stock_code": "000001.SZ"})
task_id = resp.json()["task_id"]

# 2. 轮询（每30秒，超时30分钟）
for _ in range(60):
    task = requests.get(f"{BASE}/api/v1/tasks/{task_id}").json()
    if task["status"] == "completed":
        break
    elif task["status"] in ("failed", "cancelled"):
        raise RuntimeError("任务失败: " + task.get("error", ""))
    time.sleep(30)

# 3. 获取报告
report = requests.get(f"{BASE}/api/v1/tasks/{task_id}/report").json()
print(report["content"])
```

---

## 十、注意事项

- 分析结果基于历史数据和模型推断，**仅供参考，不构成投资建议**
- 数据来源：Tushare Pro（主）/ AkShare（备）
- 每次分析消耗约 15+ 次 LLM API 调用
- 港股/美股不执行A股特有风险评分（T+1/涨跌停等规则不适用）
- 过期任务自动清理：失败任务保留7天，完成任务默认永久保留（可在 .env 配置）
