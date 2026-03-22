from __future__ import annotations

from src.agents.config_loader import get_market_rules as _loader_get_market_rules


def get_market_rules(stock_code: str) -> str:
    """按股票代码后缀返回对应市场规则（从 config/market_rules.yaml 加载）。"""
    return _loader_get_market_rules(stock_code)
