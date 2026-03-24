from __future__ import annotations

import secrets
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class PipelineConfig(BaseModel):
    """
    流水线配置模型。

    支持通过 API 参数动态控制各阶段的智能体启用情况。
    优先级：API 参数 > 环境变量 > 配置文件默认值
    """
    stage1_agents: list[str] = Field(
        default_factory=list,
        description="Stage 1 启用的智能体ID列表，为空则使用环境变量配置"
    )
    stage2_enabled: Optional[bool] = Field(
        default=None,
        description="Stage 2 多空辩论是否启用，None表示使用环境变量配置"
    )
    stage2_debate_rounds: int = Field(
        default=0,
        ge=0,
        le=3,
        description="Stage 2 辩论轮数，0表示使用环境变量配置"
    )
    stage3_enabled: Optional[bool] = Field(
        default=None,
        description="Stage 3 风控流程是否启用（依赖 Stage 2，若 Stage 2 关闭则强制关闭），None表示使用环境变量配置"
    )

    def get_effective_stage3_enabled(self, env_stage3_enabled: bool = True) -> bool:
        """
        获取实际生效的 Stage 3 状态（考虑 Stage 2 依赖关系）。

        优先级：
        1. 若 Stage 2 显式关闭，则 Stage 3 强制关闭
        2. 否则使用 Stage 3 自身配置（None 时使用环境变量默认值）
        """
        # Stage 2 显式关闭时，Stage 3 必须关闭
        if self.stage2_enabled is False:
            return False
        # Stage 3 未设置时使用环境变量默认值
        if self.stage3_enabled is None:
            return env_stage3_enabled
        return self.stage3_enabled

    def get_effective_debate_rounds(self, default_rounds: int = 2) -> int:
        """获取实际生效的辩论轮数"""
        if self.stage2_debate_rounds > 0:
            return self.stage2_debate_rounds
        return default_rounds


def generate_task_id() -> str:
    return f"TASK_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3).upper()}"


class TaskRecord(BaseModel):
    task_id: str
    stock_code: str
    status: TaskStatus
    current_stage: Optional[str] = None
    current_agent: Optional[str] = None          # 当前正在执行的智能体 ID
    stage_progress: dict[str, StageStatus] = Field(
        default_factory=lambda: {
            "stage1": StageStatus.PENDING,
            "stage2": StageStatus.PENDING,
            "stage3": StageStatus.PENDING,
            "stage4": StageStatus.PENDING,
        }
    )
    stages_completed: list[str] = Field(default_factory=list)  # 断点续跑依据
    resume_count: int = 0                        # 累计续跑次数
    logs: list[str] = Field(default_factory=list)
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    report_path: Optional[str] = None
    error: Optional[str] = None
    note: Optional[str] = None
    pipeline_config: PipelineConfig = Field(
        default_factory=PipelineConfig,
        description="该任务的流水线配置（各阶段智能体启用情况）"
    )
