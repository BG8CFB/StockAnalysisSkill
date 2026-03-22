from __future__ import annotations

from pathlib import Path

_RULES_DIR = Path(__file__).parent.parent.parent / "docs" / "market_rules"
_cache: dict[str, str] = {}


def get_market_rules(stock_code: str) -> str:
    """按股票代码后缀加载对应市场规则文档。"""
    code_upper = stock_code.upper()
    if code_upper.endswith((".SZ", ".SH")):
        key = "a"
        filename = "A股交易规则.md"
    elif code_upper.endswith(".HK"):
        key = "hk"
        filename = "港股交易规则.md"
    else:
        key = "us"
        filename = "美股交易规则.md"

    if key in _cache:
        return _cache[key]

    path = _RULES_DIR / filename
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        content = f"[{filename} 未找到，请检查 docs/market_rules/ 目录]"

    _cache[key] = content
    return content
