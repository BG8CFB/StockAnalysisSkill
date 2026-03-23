"""
数据工具统一导出模块。

此模块作为向后兼容层，将所有业务域工具统一导出。
新代码建议直接从子模块导入特定工具。

工具分类：
- market: price_tool, indicator_tool, snapshot_tool
- fundamental: fundamental_tool, shareholder_tool
- microstructure: capital_flow_tool, margin_tool, dragon_tiger_tool
- sentiment: sentiment_tool, sector_tool, news_tool
- risk: risk_metric_tool
"""

from __future__ import annotations

# 行情数据工具
from src.tools.market import price_tool, indicator_tool, snapshot_tool

# 基本面工具
from src.tools.fundamental import fundamental_tool, shareholder_tool

# 市场微观结构工具
from src.tools.microstructure import capital_flow_tool, margin_tool, dragon_tiger_tool

# 情绪与资讯工具
from src.tools.sentiment import sentiment_tool, sector_tool, news_tool

# 风险工具
from src.tools.risk import risk_metric_tool

# 基础工具函数（供外部使用）
from src.tools.base import _na, _meta_header

__all__ = [
    # 行情
    "price_tool",
    "indicator_tool",
    "snapshot_tool",
    # 基本面
    "fundamental_tool",
    "shareholder_tool",
    # 微观结构
    "capital_flow_tool",
    "margin_tool",
    "dragon_tiger_tool",
    # 情绪资讯
    "sentiment_tool",
    "sector_tool",
    "news_tool",
    # 风险
    "risk_metric_tool",
    # 基础函数
    "_na",
    "_meta_header",
]
