#!/usr/bin/env node
/**
 * stock-analysis OpenClaw Skill
 *
 * 采用链式定时任务模式：
 *   1. start-analysis: 创建后端任务，然后创建第一个 2 分钟后的定时任务
 *   2. check-progress: 查询进度，推送新报告，如未完成则创建下一个定时任务
 *
 * 依赖 OpenClaw 的 Cron 系统调度，Skill 本身不长期运行。
 */

import { execSync } from "child_process";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";
import { dirname } from "path";
import { createInterface } from "readline";

// ── 配置 ──────────────────────────────────────────────────────────────────────
const API_BASE = process.env.STOCK_ANALYSIS_API ?? "http://127.0.0.1:8888";
const SKILL_STATE_DIR = `${process.env.HOME}/.openclaw/skills/stock-analysis/state`;
const CHECK_INTERVAL_MS = 2 * 60 * 1000; // 2 分钟

// 确保状态目录存在
try {
  mkdirSync(SKILL_STATE_DIR, { recursive: true });
} catch { /* 忽略 */ }

// ── 入口：从 stdin 读取 OpenClaw 传入的参数 ────────────────────────────────────
const rl = createInterface({ input: process.stdin });
let inputData = "";
rl.on("line", (line) => (inputData += line));
rl.on("close", () => {
  const params = JSON.parse(inputData || "{}");

  // 根据调用参数判断执行哪个能力
  if (params.task_id && !params.stock_code) {
    // 有 task_id 无 stock_code → check-progress 模式
    checkProgress(params.task_id);
  } else if (params.stock_code) {
    // 有 stock_code → start-analysis 模式
    startAnalysis(params.stock_code, params.note);
  } else {
    console.error("[stock-analysis] 错误：必须提供 stock_code 或 task_id 参数");
    process.exit(1);
  }
});

// ═══════════════════════════════════════════════════════════════════════════════
// 能力 1: start-analysis
// ═══════════════════════════════════════════════════════════════════════════════

async function startAnalysis(stockCode, note) {
  // 1. 立即给用户反馈
  pushMessage(`📊 正在提交 ${stockCode} 分析任务...`);

  // 2. 提交任务到后端
  let taskId;
  let isExisting = false;
  try {
    const payload = JSON.stringify({ stock_code: stockCode, ...(note ? { note } : {}) });
    const resp = await apiPost("/api/v1/tasks", payload);
    taskId = resp.task_id;
    isExisting = resp.is_existing ?? false;
  } catch (err) {
    pushMessage(`❌ 提交分析任务失败：${err.message}\n请确认服务已启动：${API_BASE}`);
    process.exit(1);
  }

  // 3. 初始化状态文件
  const state = {
    taskId,
    stockCode,
    sentAgents: [],
    createdAt: Date.now(),
  };
  saveState(taskId, state);

  // 4. 发送初始反馈
  if (isExisting) {
    pushMessage(
      `⚠️ ${stockCode} 已有进行中的分析任务\n` +
      `📋 任务ID：${taskId}\n` +
      `将继续监控进度并推送报告`
    );
  } else {
    pushMessage(
      `✅ 分析任务已提交\n` +
      `📋 任务ID：${taskId}\n` +
      `📈 股票：${stockCode}\n` +
      `⏱ 预计耗时：15-25 分钟\n` +
      `🔄 将每2分钟检查一次进度，新完成的专家报告会立即推送`
    );
  }

  // 5. 创建第一个定时任务（2分钟后执行 check-progress）
  scheduleNextCheck(taskId, CHECK_INTERVAL_MS);
  console.log(`[stock-analysis] 已创建定时任务，2分钟后首次检查 (${taskId})`);
}

// ═══════════════════════════════════════════════════════════════════════════════
// 能力 2: check-progress
// ═══════════════════════════════════════════════════════════════════════════════

async function checkProgress(taskId) {
  const state = loadState(taskId);
  if (!state) {
    pushMessage(`❌ 找不到任务状态：${taskId}\n可能任务已过期或被清理。`);
    process.exit(1);
  }

  // 1. 查询任务进度
  let data;
  try {
    data = await apiGet(`/api/v1/tasks/${taskId}/agents`);
  } catch (err) {
    pushMessage(`⚠️ 查询进度失败（${err.message}），将在下个周期重试`);
    scheduleNextCheck(taskId, CHECK_INTERVAL_MS);
    return;
  }

  // 2. 找出新完成的报告
  const sentSet = new Set(state.sentAgents);
  const newAgents = (data.agents ?? []).filter((a) => !sentSet.has(a.filename));

  // 3. 推送新报告
  if (newAgents.length > 0) {
    for (const agent of newAgents) {
      const header = formatAgentHeader(agent);
      pushMessage(`${header}\n\n${agent.content}`);
    }
    // 更新状态
    state.sentAgents = [...state.sentAgents, ...newAgents.map((a) => a.filename)];
    saveState(taskId, state);
  }

  // 4. 任务完成 → 推送最终报告，不再创建定时任务
  if (data.task_status === "completed") {
    if (data.final_report) {
      pushMessage(
        `🎉 **${state.stockCode} 分析完成！**\n\n` +
        `以下是投资顾问综合报告：\n\n${data.final_report}`
      );
    } else {
      pushMessage(`✅ ${state.stockCode} 分析完成，所有专家报告已推送完毕。`);
    }
    cleanupState(taskId);
    console.log(`[stock-analysis] 任务完成，停止定时任务链 (${taskId})`);
    return;
  }

  // 5. 任务失败/取消 → 停止
  if (data.task_status === "failed" || data.task_status === "cancelled") {
    pushMessage(
      `❌ ${state.stockCode} 分析${data.task_status === "failed" ? "失败" : "已取消"}\n` +
      `任务ID：${taskId}`
    );
    cleanupState(taskId);
    return;
  }

  // 6. 任务进行中 → 创建下一个定时任务
  if (newAgents.length === 0) {
    // 没有新报告，发送进度提示
    const progressMsg = buildProgressMessage(data, state.stockCode);
    pushMessage(progressMsg);
  }

  scheduleNextCheck(taskId, CHECK_INTERVAL_MS);
  console.log(`[stock-analysis] 任务进行中，已创建下次检查 (${taskId})`);
}

// ═══════════════════════════════════════════════════════════════════════════════
// OpenClaw 定时任务调度
// ═══════════════════════════════════════════════════════════════════════════════

function scheduleNextCheck(taskId, delayMs) {
  // 计算绝对时间（ISO 8601）
  const nextRunAt = new Date(Date.now() + delayMs);
  const atIso = nextRunAt.toISOString();

  // 构建 CLI 命令
  // 注意：通过 --context 传递 task_id，下次执行时 Skill 从 stdin 读取
  const jobName = `stock-check-${taskId}`;
  const message = `检查股票分析任务进度 (task_id=${taskId})`;

  const cmd = [
    "openclaw", "cron", "add",
    "--name", shellQuote(jobName),
    "--at", shellQuote(atIso),
    "--session", "isolated",
    "--message", shellQuote(message),
    "--delete-after-run",
  ].join(" ");

  try {
    execSync(cmd, { stdio: "pipe" });
    console.log(`[stock-analysis] 定时任务已创建: ${jobName} @ ${atIso}`);
  } catch (err) {
    console.error(`[stock-analysis] 创建定时任务失败: ${err.message}`);
    console.error(`[stock-analysis] 命令: ${cmd}`);
    pushMessage(`⚠️ 无法创建下次检查任务，请手动运行：\nstock-analysis check ${taskId}`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// 辅助函数
// ═══════════════════════════════════════════════════════════════════════════════

function pushMessage(msg) {
  // 使用 OpenClaw 的 async-task push 或直接输出到 stdout（OpenClaw 会捕获）
  // 这里假设通过 async-task 或直接输出都能被 OpenClaw 推送给用户
  console.log(msg);
}

function formatAgentHeader(agent) {
  const stageLabel = {
    stage1: "📊 第一阶段·基础分析",
    stage2: "🔬 第二阶段·多空辩论与裁决",
    stage3: "⚠️ 第三阶段·风险评估",
  }[agent.stage] ?? agent.stage;

  const roundLabel = agent.round !== null ? ` 第${agent.round + 1}轮` : "";
  return `---\n## ${stageLabel} — ${agent.display_name}${roundLabel}`;
}

function buildProgressMessage(data, stockCode) {
  const stageNames = {
    data_fetch: "数据拉取",
    stage1: "Stage 1·基础分析",
    stage2: "Stage 2·多空辩论",
    stage3: "Stage 3·风险评估",
    stage4: "Stage 4·综合报告",
  };
  const stage = stageNames[data.current_stage] ?? data.current_stage ?? "处理中";
  const agent = data.current_agent ? ` · ${data.current_agent}` : "";
  const completed = data.agents?.length ?? 0;
  return (
    `⏳ **${stockCode} 分析进行中**\n` +
    `当前阶段：${stage}${agent}\n` +
    `已完成报告：${completed} 份`
  );
}

// ── HTTP 工具 ──────────────────────────────────────────────────────────────────
async function apiGet(path) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, { signal: AbortSignal.timeout(30_000) });
  if (!res.ok) throw new Error(`HTTP ${res.status} GET ${path}`);
  return res.json();
}

async function apiPost(path, body) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    signal: AbortSignal.timeout(30_000),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} POST ${path}: ${text}`);
  }
  return res.json();
}

// ── 状态文件管理 ─────────────────────────────────────────────────────────────
function getStatePath(taskId) {
  return `${SKILL_STATE_DIR}/${taskId}.json`;
}

function saveState(taskId, state) {
  try {
    const path = getStatePath(taskId);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify(state, null, 2), "utf-8");
  } catch (err) {
    console.error(`[stock-analysis] 保存状态失败: ${err.message}`);
  }
}

function loadState(taskId) {
  try {
    const path = getStatePath(taskId);
    if (existsSync(path)) {
      return JSON.parse(readFileSync(path, "utf-8"));
    }
  } catch (err) {
    console.error(`[stock-analysis] 读取状态失败: ${err.message}`);
  }
  return null;
}

function cleanupState(taskId) {
  try {
    const path = getStatePath(taskId);
    if (existsSync(path)) {
      writeFileSync(path, "", "utf-8"); // 清空内容保留文件，或 unlinkSync 删除
    }
  } catch { /* 忽略 */ }
}

// ── 工具函数 ─────────────────────────────────────────────────────────────────
function shellQuote(str) {
  // 使用双引号包裹，内部双引号转义
  return `"${str.replace(/"/g, '\\"')}"`;
}
