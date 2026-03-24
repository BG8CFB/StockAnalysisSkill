"""
数据工具统一导出模块。

此模块作为向后兼容层，将所有业务域工具统一导出。
新代码建议直接从子模块导入特定工具。

工具分类（重构后 - 11个工具）：
- market: market_data_tool (新整合工具) = price_tool + indicator_tool + snapshot_tool
- fundamental: fundamental_tool (已整合 shareholder 数据)
- microstructure: microstructure_tool (新整合工具) = capital_flow_tool + margin_tool + dragon_tiger_tool
- sentiment: sentiment_tool, sector_tool, news_tool
- macro: macro_tool (新整合工具) = macro_china + macro_interest + macro_fx + macro_market
- risk: risk_metric_tool

向后兼容：所有旧工具名仍可作为别名使用。
"""

from __future__ import annotations

# 行情数据工具（整合后 + 向后兼容）
from src.tools.market import market_data_tool, price_tool, indicator_tool, snapshot_tool

# 基本面工具（fundamental_tool 已整合 shareholder 数据）
from src.tools.fundamental import fundamental_tool, shareholder_tool

# 市场微观结构工具（整合后 + 向后兼容）
from src.tools.microstructure import microstructure_tool, capital_flow_tool, margin_tool, dragon_tiger_tool

# 情绪与资讯工具
from src.tools.sentiment import sentiment_tool, sector_tool, news_tool

# 风险工具
from src.tools.risk import risk_metric_tool

# 宏观工具（整合后 + 向后兼容，macro_global_tool 已删除）
from src.tools.macro import (
    macro_tool,
    macro_china_tool,
    macro_interest_tool,
    macro_fx_tool,
    macro_market_tool,
)

# 基础工具函数（供外部使用）
from src.tools.base import _na, _meta_header

__all__ = [
    # ========== 整合后的新工具（推荐）==========
    "market_data_tool",      # 行情数据整合工具
    "microstructure_tool",   # 市场微观结构整合工具
    "macro_tool",            # 宏观数据整合工具

    # ========== 现有工具 ==========
    "fundamental_tool",      # 基本面（已整合股东结构）
    "sentiment_tool",        # 市场情绪
    "sector_tool",           # 板块轮动
    "news_tool",             # 资讯事件
    "risk_metric_tool",      # 风险指标

    # ========== 向后兼容的旧工具别名 ==========
    # 行情数据（旧）
    "price_tool",
    "indicator_tool",
    "snapshot_tool",
    # 基本面（旧）
    "shareholder_tool",
    # 微观结构（旧）
    "capital_flow_tool",
    "margin_tool",
    "dragon_tiger_tool",
    # 宏观（旧，macro_global_tool 已删除）
    "macro_china_tool",
    "macro_interest_tool",
    "macro_fx_tool",
    "macro_market_tool",

    # ========== 基础函数 ==========
    "_na",
    "_meta_header",
]
