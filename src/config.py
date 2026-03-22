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
    llm_timeout_seconds: int = 120
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
    server_port: int = 8080

    # 存储
    tasks_dir: Path = Path("./tasks")
    reports_dir: Path = Path("./reports")
    task_file_retention_days: int = 30

    # 分析
    analysis_capital_base: int = 100000
    debate_rounds: int = 2

    # 日志
    log_dir: Path = Path("./logs")
    log_level: str = "INFO"
    log_console_enabled: bool = True
    log_retention_days: int = 30

    @computed_field  # type: ignore[misc]
    @property
    def max_concurrent_tasks(self) -> int:
        return self.model_api_max_concurrency // self.task_max_model_concurrency


settings = Settings()
