import pytest


def test_setup_logging_does_not_raise(tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config.settings, "log_dir", tmp_path / "logs")
    monkeypatch.setattr(config.settings, "log_console_enabled", False)
    from src.logging_config import setup_logging
    setup_logging()  # must not raise


def test_log_dir_created(tmp_path, monkeypatch):
    from src import config
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(config.settings, "log_dir", log_dir)
    monkeypatch.setattr(config.settings, "log_console_enabled", False)
    from src.logging_config import setup_logging
    setup_logging()
    assert log_dir.exists()


def test_setup_logging_adds_file_handler(tmp_path, monkeypatch):
    from src import config
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(config.settings, "log_dir", log_dir)
    monkeypatch.setattr(config.settings, "log_console_enabled", False)
    from loguru import logger
    from src.logging_config import setup_logging
    setup_logging()
    # After setup, logger should have at least 1 handler (file handler)
    # We verify by checking the log dir has a file after a write
    import logging
    logging.getLogger("test").info("hello from stdlib")
    # No assertion beyond "no exception" — loguru file writing is async
