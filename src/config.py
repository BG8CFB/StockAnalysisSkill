from __future__ import annotations

from pathlib import Path

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM API
    llm_api_base: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model_id: str = "gpt-4o"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096
    llm_timeout_seconds: int = 180
    llm_max_retries: int = 3

    # 数据源
    tushare_token: str = ""
    akshare_enabled: bool = True

    # 并发
    model_api_max_concurrency: int = 10
    task_max_model_concurrency: int = 2
    task_queue_max_size: int = 100

    # 服务
    server_host: str = "127.0.0.1"
    server_port: int = 8081

    # 存储
    tasks_dir: Path = Path("./tasks")
    completed_task_retention_days: int = 0   # 0 = 永久保留
    failed_task_retention_days: int = 7

    # 分析
    analysis_capital_base: int = 100000
    debate_rounds: int = 2

    # 流水线阶段配置（可被 API 参数覆盖）
    # Stage 1: 启用的分析师列表，逗号分隔，空表示使用配置文件中所有
    pipeline_stage1_agents: str = ""
    # Stage 2: 多空辩论启用开关
    pipeline_stage2_enabled: bool = True
    # Stage 2: 辩论轮数（0-3，0表示使用 debate_rounds）
    pipeline_stage2_debate_rounds: int = 0
    # Stage 3: 风控流程启用开关（依赖 Stage 2，若 Stage 2 关闭则强制关闭）
    pipeline_stage3_enabled: bool = True

    # 日志
    log_dir: Path = Path("./logs")
    log_level: str = "INFO"
    log_console_enabled: bool = True
    log_retention_days: int = 30

    @computed_field  # type: ignore[misc]
    @property
    def max_concurrent_tasks(self) -> int:
        return self.model_api_max_concurrency // self.task_max_model_concurrency

    @computed_field  # type: ignore[misc]
    @property
    def stage1_agents_list(self) -> list[str]:
        """将逗号分隔的字符串解析为智能体ID列表，空字符串返回空列表。"""
        if not self.pipeline_stage1_agents:
            return []
        return [aid.strip() for aid in self.pipeline_stage1_agents.split(",") if aid.strip()]

    def get_effective_debate_rounds(self) -> int:
        """获取实际生效的辩论轮数。"""
        if self.pipeline_stage2_debate_rounds > 0:
            return self.pipeline_stage2_debate_rounds
        return self.debate_rounds


settings = Settings()
