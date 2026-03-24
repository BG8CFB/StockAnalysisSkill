from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, field_validator

from src.core.models import TaskRecord, TaskStatus, StageStatus, PipelineConfig
from src.core.task_queue import QueueFullError
from src.core.task_store import (
    create_task, get_task, list_tasks, update_task,
    find_active_task, delete_task_folder, get_report_content,
    list_agent_outputs,
)
from src.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

# 股票代码格式（A股/港股/美股）
_CODE_PATTERN = re.compile(
    r"^\d{6}\.(SZ|SH)$"           # A股 000001.SZ
    r"|^\d{5}\.HK$"               # 港股 00700.HK
    r"|^[A-Z]{1,5}$"              # 美股 AAPL
    r"|^[A-Z]{1,5}\.[A-Z]{1,2}$", # 美股带后缀 BRK.A
    re.IGNORECASE,
)

# DELETE 等待 RUNNING 任务完成的超时（秒）
_DELETE_WAIT_TIMEOUT = 5.0


# ── 配置合并（优先级：API 参数 > 环境变量 > 默认值）─────────────────────────────

def _merge_pipeline_config(api_config: Optional[PipelineConfig]) -> PipelineConfig:
    """
    合并流水线配置：API 参数 > 环境变量 > 配置默认值。
    """
    # 从环境变量/配置读取默认值
    env_stage1 = settings.stage1_agents_list
    env_stage2_enabled = settings.pipeline_stage2_enabled
    env_stage2_rounds = settings.pipeline_stage2_debate_rounds
    env_stage3_enabled = settings.pipeline_stage3_enabled

    if api_config is None:
        # 无 API 参数，使用环境变量/配置
        return PipelineConfig(
            stage1_agents=env_stage1,
            stage2_enabled=env_stage2_enabled,
            stage2_debate_rounds=env_stage2_rounds,
            stage3_enabled=env_stage3_enabled,
        )

    # API 参数覆盖环境变量（None 表示未传入，使用环境变量）
    merged = PipelineConfig(
        stage1_agents=api_config.stage1_agents if api_config.stage1_agents else env_stage1,
        stage2_enabled=api_config.stage2_enabled if api_config.stage2_enabled is not None else env_stage2_enabled,
        stage2_debate_rounds=api_config.stage2_debate_rounds if api_config.stage2_debate_rounds > 0 else env_stage2_rounds,
        stage3_enabled=api_config.stage3_enabled if api_config.stage3_enabled is not None else env_stage3_enabled,
    )

    return merged


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    stock_code: str
    note: Optional[str] = None
    pipeline_config: Optional[PipelineConfig] = None

    @field_validator("stock_code")
    @classmethod
    def validate_stock_code(cls, v: str) -> str:
        v = v.strip().upper()
        if not _CODE_PATTERN.match(v):
            raise ValueError(
                f"Invalid stock code format: '{v}'. "
                "Expected formats: 000001.SZ, 00700.HK, AAPL"
            )
        return v


class CreateTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    stock_code: str
    queue_position: int
    created_at: datetime
    is_existing: bool = False
    effective_config: PipelineConfig
    message: str = ""


class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    stock_code: str
    current_stage: Optional[str]
    current_agent: Optional[str]
    stage_progress: dict[str, StageStatus]
    stages_completed: list[str]
    resume_count: int
    logs: list[str]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error: Optional[str]
    report_path: Optional[str]
    note: Optional[str]


class ReportResponse(BaseModel):
    task_id: str
    stock_code: str
    content: str
    report_path: Optional[str]
    completed_at: Optional[datetime]


class AgentOutputItem(BaseModel):
    filename: str          # 原始文件名，如 stage1_technical_analyst.md
    stage: str             # stage1 / stage2 / stage3
    agent_id: str          # technical_analyst / bull_researcher 等
    display_name: str      # 技术分析师 / 看涨分析师 等
    round: Optional[int]   # 仅多空辩论轮次有值（0-based）
    content: str           # 报告全文


class TaskAgentsResponse(BaseModel):
    task_id: str
    stock_code: str
    task_status: TaskStatus
    current_stage: Optional[str]
    current_agent: Optional[str]
    agents: list[AgentOutputItem]   # 已完成的智能体报告列表
    final_report: Optional[str]     # report.md 内容（仅 COMPLETED 时有值）


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    total: int


# ── 路由处理器 ─────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_task_endpoint(request: Request, body: CreateTaskRequest):
    """创建股票分析任务（幂等：相同股票已有活跃任务则返回 200）。"""
    logger.info(f"[API] 收到分析请求，股票: {body.stock_code}")

    # 幂等检查：同一股票有 PENDING/RUNNING 任务时直接返回
    existing = find_active_task(body.stock_code)
    if existing:
        logger.info(
            f"[API] 发现重复任务，{body.stock_code} 已有进行中任务 "
            f"{existing.task_id}，直接返回"
        )
        # 构建生效配置说明
        cfg = existing.pipeline_config
        effective_cfg_desc = (
            f"Stage1: {cfg.stage1_agents if cfg.stage1_agents else '全部'}, "
            f"Stage2: {'开启' if cfg.stage2_enabled else '关闭'}, "
            f"辩论轮数: {cfg.get_effective_debate_rounds(settings.debate_rounds)}, "
            f"Stage3: {'开启' if cfg.get_effective_stage3_enabled() else '关闭'}"
        )
        response = CreateTaskResponse(
            task_id=existing.task_id,
            status=existing.status,
            stock_code=existing.stock_code,
            queue_position=0,
            created_at=existing.created_at,
            is_existing=True,
            effective_config=existing.pipeline_config,
            message=f"任务已存在，{effective_cfg_desc}",
        )
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

    # 合并配置（API 参数 > 环境变量 > 默认值）
    effective_config = _merge_pipeline_config(body.pipeline_config)

    task_queue = request.app.state.task_queue
    task = create_task(body.stock_code, body.note, effective_config)

    try:
        queue_pos = await task_queue.enqueue(task.task_id)
    except QueueFullError:
        update_task(task.task_id, task.stock_code, status=TaskStatus.FAILED, error="Queue is full")
        logger.warning(f"[API] 队列已满（max {settings.task_queue_max_size}），拒绝请求")
        raise HTTPException(
            status_code=429,
            detail=(
                f"Task queue is full (max {settings.task_queue_max_size}). "
                "Please try again later."
            ),
        )

    # 构建配置说明
    cfg = effective_config
    effective_cfg_desc = (
        f"Stage1: {cfg.stage1_agents if cfg.stage1_agents else '全部'}, "
        f"Stage2: {'开启' if cfg.stage2_enabled else '关闭'}, "
        f"辩论轮数: {cfg.get_effective_debate_rounds(settings.debate_rounds)}, "
        f"Stage3: {'开启' if cfg.get_effective_stage3_enabled() else '关闭'}"
    )
    logger.info(f"[API] 任务创建成功，task_id: {task.task_id}，队列位置: {queue_pos}，配置: {effective_cfg_desc}")
    response = CreateTaskResponse(
        task_id=task.task_id,
        status=task.status,
        stock_code=task.stock_code,
        queue_position=queue_pos,
        created_at=task.created_at,
        is_existing=False,
        effective_config=effective_config,
        message=f"任务创建成功，{effective_cfg_desc}",
    )
    return JSONResponse(status_code=201, content=response.model_dump(mode="json"))


@router.get("", response_model=TaskListResponse)
async def list_tasks_endpoint(
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> TaskListResponse:
    """列出任务（可按状态过滤）。"""
    status_filter = TaskStatus(status) if status else None
    tasks = list_tasks(status=status_filter, limit=limit, offset=offset)
    return TaskListResponse(
        tasks=[_task_to_response(t) for t in tasks],
        total=len(tasks),
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task_endpoint(task_id: str) -> TaskResponse:
    """获取任务详情。"""
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    return _task_to_response(task)


@router.delete("/{task_id}", status_code=204)
async def delete_task_endpoint(request: Request, task_id: str) -> Response:
    """
    删除任务及其全部文件（task.json / report.md / agents/ / data/）。

    - PENDING/RUNNING：先发取消信号，等待最多 5s 完成，再删除文件夹
    - COMPLETED/FAILED/CANCELLED：直接删除文件夹
    - 404：任务不存在
    """
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    task_queue = request.app.state.task_queue
    stock_code = task.stock_code

    if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        logger.info(f"[API] 删除活跃任务 {task_id}（状态={task.status}），发送取消信号")
        done_event = task_queue.get_done_event(task_id)
        await task_queue.cancel(task_id)
        try:
            await asyncio.wait_for(done_event.wait(), timeout=_DELETE_WAIT_TIMEOUT)
            logger.info(f"[API] 任务 {task_id} 已停止，开始删除文件")
        except asyncio.TimeoutError:
            logger.warning(
                f"[API] 等待任务 {task_id} 停止超时（{_DELETE_WAIT_TIMEOUT}s），强制删除文件"
            )

    deleted = delete_task_folder(task_id, stock_code)
    if deleted:
        logger.info(f"[API] 任务 {task_id} 文件夹已删除")
    else:
        logger.warning(f"[API] 任务 {task_id} 文件夹不存在或已删除")

    return Response(status_code=204)


@router.get("/{task_id}/report", response_model=ReportResponse)
async def get_report_endpoint(task_id: str) -> ReportResponse:
    """获取已完成任务的 Markdown 报告内容。"""
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Task is not completed yet (current status: '{task.status}'). Please wait."
        )

    content = get_report_content(task_id, task.stock_code)
    if content is None:
        raise HTTPException(
            status_code=404,
            detail="Report file not found for this task"
        )

    return ReportResponse(
        task_id=task.task_id,
        stock_code=task.stock_code,
        content=content,
        report_path=task.report_path,
        completed_at=task.completed_at,
    )


# ── 路由：智能体报告列表 ────────────────────────────────────────────────────────

@router.get("/{task_id}/agents", response_model=TaskAgentsResponse)
async def list_task_agents_endpoint(task_id: str) -> TaskAgentsResponse:
    """
    返回任务所有已完成的智能体报告（含全文内容）及最终报告。

    适合 AI 客户端（如 OpenClaw Skill）定期轮询：
    - 每 2 分钟调用一次，取出 agents 列表与上次对比，将新增报告推送给用户
    - 任务完成时 final_report 有值，即为最终投资报告
    - 任务未开始/不存在时返回 404
    """
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    raw_outputs = list_agent_outputs(task_id, task.stock_code)
    agents = [_enrich_agent_output(fname, content) for fname, content in raw_outputs]

    final_report: Optional[str] = None
    if task.status == TaskStatus.COMPLETED:
        final_report = get_report_content(task_id, task.stock_code)

    return TaskAgentsResponse(
        task_id=task.task_id,
        stock_code=task.stock_code,
        task_status=task.status,
        current_stage=task.current_stage,
        current_agent=task.current_agent,
        agents=agents,
        final_report=final_report,
    )


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

# 静态文件名 → (display_name) 映射，用于无法从 config_loader 推导的情况
_STATIC_DISPLAY_NAMES: dict[str, str] = {
    "technical_analyst": "技术分析师",
    "fundamental_analyst": "基本面分析师",
    "microstructure_analyst": "市场微观结构分析师",
    "sentiment_analyst": "市场情绪分析师",
    "sector_analyst": "板块轮动分析师",
    "news_analyst": "资讯事件分析师",
    "bull_researcher": "看涨分析师",
    "bear_researcher": "看跌分析师",
    "research_director": "研究主管",
    "trading_planner": "交易计划师",
    "aggressive_risk_manager": "激进风控师",
    "conservative_risk_manager": "保守风控师",
    "quant_risk_manager": "量化风控师",
    "chief_risk_officer": "首席风控官",
    "investment_advisor": "投资顾问（最终报告）",
}

# 匹配 stage2 多空辩论轮次文件名：stage2_bull_r0, stage2_bear_r2 …
_DEBATE_RE = re.compile(r"^stage2_(bull|bear)_r(\d+)$")


def _enrich_agent_output(filename: str, content: str) -> AgentOutputItem:
    """将文件名解析为结构化 AgentOutputItem，用于前端/AI 客户端展示。"""
    stem = filename.removesuffix(".md")

    # 提取阶段前缀
    m = re.match(r"^(stage[123])_(.+)$", stem)
    if not m:
        return AgentOutputItem(
            filename=filename, stage="unknown", agent_id=stem,
            display_name=stem, round=None, content=content,
        )

    stage = m.group(1)
    rest = m.group(2)

    # 多空辩论轮次
    debate_m = _DEBATE_RE.match(stem)
    if debate_m:
        side = debate_m.group(1)          # bull / bear
        rnd = int(debate_m.group(2))
        agent_id = f"{side}_researcher"
        base_name = _STATIC_DISPLAY_NAMES.get(agent_id, agent_id)
        display = f"{base_name} 第{rnd + 1}轮"
        return AgentOutputItem(
            filename=filename, stage=stage, agent_id=agent_id,
            display_name=display, round=rnd, content=content,
        )

    display_name = _STATIC_DISPLAY_NAMES.get(rest, rest)
    return AgentOutputItem(
        filename=filename, stage=stage, agent_id=rest,
        display_name=display_name, round=None, content=content,
    )


def _task_to_response(task: TaskRecord) -> TaskResponse:
    return TaskResponse(
        task_id=task.task_id,
        status=task.status,
        stock_code=task.stock_code,
        current_stage=task.current_stage,
        current_agent=task.current_agent,
        stage_progress=task.stage_progress,
        stages_completed=task.stages_completed,
        resume_count=task.resume_count,
        logs=task.logs,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        error=task.error,
        report_path=task.report_path,
        note=task.note,
    )
