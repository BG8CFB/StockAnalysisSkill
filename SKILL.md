---
name: stock-analysis
description: 对A股、港股或美股进行多维度量化分析并生成专业的投资研究报告。包含技术面、基本面、资金流和风控分析。
license: MIT
compatibility: Requires local HTTP service running.
---

# Stock Analysis Skill

## When to use this skill
当用户请求对某只股票（A股、港股、美股）进行深入分析、诊断、评估或要求生成投资研究报告时使用此技能。
**Trigger phrases**: "帮我分析一下平安银行", "评估一下 AAPL", "00700.HK 现在的基本面如何", "生成股票研报".

## Prerequisites
- 服务必须已经在本地运行（默认端口 `http://127.0.0.1:8888`，如果用户在安装时更改了端口，请使用实际端口）。
- 如果 API 拒绝连接，请提示用户先检查服务状态或运行 `bash scripts/start.sh`。

## Steps
按照以下步骤调用 API 完成股票分析：

1. **创建分析任务 (Create Task)**
   - **Method**: `POST`
   - **URL**: `http://127.0.0.1:8888/api/v1/tasks`
   - **Payload**: `{"stock_code": "<格式化的股票代码>", "note": "<可选用户备注>"}`
     - *格式化规则*：A股为 `000001.SZ` 或 `600519.SH`；港股为 `00700.HK`；美股为大写字母如 `AAPL`。
   - **Action**: 记录响应中的 `task_id`。如果返回 429 说明队列已满，请告知用户稍后再试。

2. **轮询任务状态 (Poll Status)**
   - **Method**: `GET`
   - **URL**: `http://127.0.0.1:8888/api/v1/tasks/{task_id}`
   - **Action**: 每隔 30 秒轮询一次，直到 `status` 变为 `completed`。
   - *提示*：在轮询期间，你可以通过解析 `current_stage` 向用户同步进度（例如：“正在进行 Stage 2 多空辩论...”）。若状态变为 `failed`，请提取 `error` 报错并终止。

3. **获取最终报告 (Fetch Report)**
   - **Method**: `GET`
   - **URL**: `http://127.0.0.1:8888/api/v1/tasks/{task_id}/report`
   - **Action**: 当任务 `completed` 后，请求该接口获取最终的 Markdown 研报。

4. **呈现结果 (Present Results)**
   - 将研报的核心摘要或全文呈现给用户。
   - **安全合规要求**：必须在最终回答末尾附加免责声明：“*注：以上分析结果基于历史数据和 AI 模型推断，仅供参考，不构成任何实质性投资建议。*”

## Advanced / Cancellation
- 若用户中途要求取消分析：`DELETE http://127.0.0.1:8888/api/v1/tasks/{task_id}`。
- 若需排查特定数据指标异常，可读取本地文件 `tasks/{日期}/{股票代码}/{task_id}/data/` 获取原始溯源数据。
