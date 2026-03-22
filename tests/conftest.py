# tests/conftest.py
import pytest


@pytest.fixture(autouse=True)
def clean_loguru_handlers():
    """每个测试后移除 loguru handlers，防止跨测试 handler 累积。"""
    from loguru import logger
    yield
    logger.remove()


@pytest.fixture
def disable_logging(tmp_path, monkeypatch):
    """测试时使用临时目录作为日志目录，避免写入 ./logs/"""
    from src import config
    import src.logging_config as lc
    monkeypatch.setattr(config.settings, "log_console_enabled", False)
    monkeypatch.setattr(config.settings, "log_dir", tmp_path / "logs")
    monkeypatch.setattr(lc, "_logging_configured", False)
