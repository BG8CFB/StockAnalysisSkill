# Stock Analysis Skill

> **适用场景**：需要对A股/港股/美股进行多维度量化分析、生成投资研究报告时使用。
> **分析模式**：11个AI智能体组成4阶段流水线，覆盖技术/基本面/资金流/风控等维度。
> **输出格式**：结构化Markdown报告，含交易计划、风控裁决、投资建议。

---

## 一、服务概述

Stock Analysis Skill 是一个多智能体股票分析服务，通过 HTTP API 提供服务。

- **服务地址**：`http://127.0.0.1:8080`
- **分析标的**：A股（.SZ/.SH）、港股（.HK）、美股（AAPL等）
- **分析时长**：约 15-25 分钟（4阶段流水线，含11个LLM智能体）
- **并发支持**：最多5个任务同时分析，队列最大100个

---

## 二、调用步骤

```
1. POST /api/v1/tasks          → 创建分析任务（返回 task_id）
2. GET  /api/v1/tasks/{id}     → 轮询任务状态（每30秒轮询一次）
3. GET  /api/v1/tasks/{id}/report → 任务完成后获取Markdown报告
4. DELETE /api/v1/tasks/{id}   → 如需中止，取消任务
```

---

## 三、API详细说明

### 3.1 创建分析任务

**接口**：`POST /api/v1/tasks`

**请求体**：
```json
{
  "stock_code": "000001.SZ",
  "note": "可选备注"
}
```

**stock_code 格式**：
| 市场 | 格式 | 示例 |
|-----|------|------|
| A股 | 6位数字.SZ或.SH | `000001.SZ`、`600519.SH` |
| 港股 | 5位数字.HK | `00700.HK` |
| 美股 | 1-5位字母 | `AAPL`、`BRK.A` |

**成功响应（201）**：
```json
{
  "task_id": "TASK_20260322_143000_A1B2C3",
  "status": "pending",
  "stock_code": "000001.SZ",
  "queue_position": 1,
  "created_at": "2026-03-22T14:30:00.000000"
}
```

**错误响应**：
- `422`：stock_code 格式不正确
- `429`：任务队列已满，稍后重试

---

### 3.2 查询任务状态

**接口**：`GET /api/v1/tasks/{task_id}`

**响应**：
```json
{
  "task_id": "TASK_20260322_143000_A1B2C3",
  "status": "running",
  "stock_code": "000001.SZ",
  "current_stage": "stage2",
  "stage_progress": {
    "stage1": "completed",
    "stage2": "running",
    "stage3": "pending",
    "stage4": "pending"
  },
  "created_at": "2026-03-22T14:30:00.000000",
  "started_at": "2026-03-22T14:30:05.000000",
  "completed_at": null,
  "error": null,
  "report_path": null,
  "note": "可选备注"
}
```

---

### 3.3 获取分析报告

**接口**：`GET /api/v1/tasks/{task_id}/report`

> 仅当 `status == "completed"` 时可调用，否则返回 400。

**响应**：
```json
{
  "task_id": "TASK_20260322_143000_A1B2C3",
  "stock_code": "000001.SZ",
  "content": "# 平安银行(000001.SZ) 投资分析报告\n\n## 执行摘要\n...",
  "report_path": "reports/20260322/000001.SZ_TASK_20260322_143000_A1B2C3.md",
  "completed_at": "2026-03-22T14:52:30.000000"
}
```

报告内容为完整 Markdown 格式，包含：技术分析、基本面、资金流、风控计算、交易计划、投资建议。

---

### 3.4 取消任务

**接口**：`DELETE /api/v1/tasks/{task_id}`

仅可取消 `pending` 或 `running` 状态的任务。`completed`/`failed` 状态不可取消。

---

### 3.5 列出任务

**接口**：`GET /api/v1/tasks?status=running&limit=20&offset=0`

`status` 可选值：`pending`、`running`、`completed`、`failed`、`cancelled`

---

### 3.6 健康检查

**接口**：`GET /api/v1/health`

```json
{
  "status": "ok",
  "queue_size": 2,
  "running_tasks": 3,
  "max_concurrent_tasks": 5,
  "model_api_max_concurrency": 10
}
```

---

## 四、任务状态说明

| 状态 | 含义 |
|-----|------|
| `pending` | 已加入队列，等待 worker 处理 |
| `running` | 流水线正在运行（current_stage 显示当前阶段） |
| `completed` | 分析完成，报告可读取 |
| `failed` | 发生错误（error 字段含错误信息） |
| `cancelled` | 已取消 |

---

## 五、分析阶段说明

| 阶段 | 内容 | 耗时估计 |
|-----|------|---------|
| Stage 1（data_fetch → stage1） | 数据获取 + 6位专项分析师并行 | 3-5分钟 |
| Stage 2（stage2） | 多空辩论（2轮）+ 研究主管 + 交易计划师 | 5-8分钟 |
| Stage 3（stage3） | VaR计算 + 3位风控师并行 + 首席风控官 | 3-6分钟 |
| Stage 4（stage4） | 投资顾问综合报告生成 | 2-4分钟 |

---

## 六、推荐调用流程（伪代码）

```
# 1. 创建任务
response = POST /api/v1/tasks {"stock_code": "000001.SZ"}
task_id = response.task_id

# 2. 轮询（每30秒一次，超时30分钟）
for _ in range(60):
    status = GET /api/v1/tasks/{task_id}
    if status.status == "completed":
        break
    elif status.status in ["failed", "cancelled"]:
        handle_error()
        break
    sleep(30)

# 3. 获取报告
report = GET /api/v1/tasks/{task_id}/report
print(report.content)
```

---

## 七、停牌处理

若股票当日停牌，系统将直接生成停牌报告（跳过所有AI分析阶段），报告内容注明"禁止交易"，任务状态仍为 `completed`。

---

## 八、注意事项

- 分析结果基于历史数据和模型推断，**仅供参考，不构成投资建议**
- 数据来源：Tushare Pro（主）/ AkShare（备）
- 每次分析消耗约 11 次 LLM API 调用（加上 2 轮辩论共 15+ 次）
- 港股/美股不执行A股特有风险评分（T+1/涨跌停等规则不适用）
