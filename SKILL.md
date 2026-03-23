---
name: stock-analysis
version: 2.0.0
description: "对A股/港股/美股个股发起多智能体深度分析，使用OpenClaw定时任务链实现分阶段报告推送"
author: your-username
license: MIT

capabilities:
  - id: start-analysis
    description: "对指定股票代码发起深度AI分析。创建后端任务后，自动设置OpenClaw定时任务链，每2分钟检查进度并推送已完成的专家报告"
  - id: check-progress
    description: "检查指定任务ID的分析进度，推送新完成的专家报告。如任务未完成，自动创建下一个2分钟后检查的定时任务"

permissions:
  network: true
  filesystem: true
  shell: true
  env:
    - STOCK_ANALYSIS_API

inputs:
  - name: stock_code
    type: string
    required: true
    description: "股票代码。A股格式：000001.SZ 或 600519.SH；港股格式：00700.HK；美股格式：AAPL"
  - name: note
    type: string
    required: false
    description: "分析备注（可选），如'关注Q3业绩'、'评估建仓时机'"
  - name: task_id
    type: string
    required: false
    description: "用于check-progress能力，指定要查询的任务ID"

outputs:
  - name: task_id
    type: string
    description: "分析任务ID"
  - name: status
    type: string
    description: "任务状态：pending/running/completed/failed"
  - name: new_reports
    type: array
    description: "本次检查新发现的专家报告列表"

tags:
  - finance
  - stock-analysis
  - investment
  - async
  - cron

minOpenClawVersion: "2.1.0"
---

## OpenClaw 定时任务链配置说明

本 Skill 采用**链式定时任务**模式实现异步报告推送，无需后台轮询进程。

### 工作原理

```
用户: "分析000001.SZ"
    ↓
[start-analysis] 创建后端任务 (TASK_20260323_143012_A1B2C3)
    ↓
创建 OpenClaw 定时任务: 2分钟后执行 check-progress (task_id=xxx)
    ↓
2分钟后 ──────────────────────────────────────────────
    ↓
[check-progress] 查询 /api/v1/tasks/{id}/agents
    ↓
├─ 发现新报告 → 推送给用户
├─ 任务未完成 → 创建下一个定时任务（2分钟后）
└─ 任务完成   → 推送最终报告，停止
```

### 配置步骤（必须）

**Step 1: 安装 Skill**

将本目录复制到 OpenClaw skills 目录：
```bash
cp -r stock-analysis ~/.openclaw/skills/
```

**Step 2: 配置环境变量**

在 `~/.openclaw/config/config.json` 或 shell profile 中设置：
```bash
export STOCK_ANALYSIS_API="http://127.0.0.1:8888"
```

**Step 3: 确保后端服务运行**

```bash
cd /path/to/StockAnalysisSkill
uv run python -m src.main
```

### 状态存储

本 Skill 使用本地文件存储每个任务的"已推送报告"状态：
- 路径：`~/.openclaw/skills/stock-analysis/state/TASK_xxx.json`
- 内容：已推送的 agent 文件名列表
- 用途：避免重复推送同一份报告

### 分析流程与报告推送时机

| 阶段 | 智能体 | 预计时间 | 推送内容 |
|------|--------|----------|----------|
| Stage 1 | 技术分析师、基本面分析师、微观结构分析师、情绪分析师、板块分析师、资讯分析师 | 0-5分钟 | 每完成一个即推送 |
| Stage 2 | 多空辩论(多轮)、研究主管、交易计划师 | 5-12分钟 | 每轮辩论完成推送 |
| Stage 3 | 激进/保守/量化风控师、首席风控官 | 12-18分钟 | 每位风控师完成推送 |
| Stage 4 | 投资顾问（综合报告） | 18-22分钟 | 最终完整报告 |

### 手动管理定时任务

如果需要查看或管理本 Skill 创建的定时任务：

```bash
# 列出所有定时任务
openclaw cron list

# 查看特定任务的历史运行记录
openclaw cron runs --id <job-id>

# 手动触发检查（调试用）
openclaw cron run --id <job-id>

# 停止某个股票的分析监控
openclaw cron rm <job-id>
```

### 避免重复报告机制

1. **文件级去重**：每次 `check-progress` 执行时，读取 `state/{task_id}.json`
2. **对比差异**：将当前 `/agents` 接口返回的报告列表与已推送列表对比
3. **仅推送新增**：只推送文件名不在已推送列表中的报告
4. **更新状态**：推送成功后，将新报告文件名写入状态文件

### 故障恢复

如果 OpenClaw 重启或 Skill 进程中断：

1. 已创建的定时任务仍然有效（由 OpenClaw Cron 调度器管理）
2. 下次 `check-progress` 执行时会自动恢复状态
3. 如果任务已完成但未推送最终报告，下次检查时会补推

## 使用示例

- "帮我分析一下宁德时代"
- "分析 000001.SZ，关注近期资金流向"
- "AAPL 现在值得买入吗"

## 注意事项

- 分析总耗时约 15-25 分钟，期间会分阶段收到专家报告
- 同一股票有进行中的任务时会复用现有任务
- 定时任务链会自动清理，无需手动管理
- 如需要立即停止某股票的分析监控，使用 `openclaw cron rm` 删除对应定时任务
