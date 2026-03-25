"""
src.tools 包 — 数据工具模块。

提供股票分析所需的各类数据格式化工具。
所有工具函数接收 CalculatedDataPacket，返回 Markdown 格式字符串。

工具列表（按业务域整合）：
- market_data_tool: 行情数据整合（价格 + 技术指标 + 行情快照）
- fundamental_tool: 基本面分析（财务数据 + 股东结构）
- microstructure_tool: 市场微观结构（资金流 + 融资融券 + 龙虎榜）
- sentiment_tool: 市场情绪
- sector_tool: 板块轮动
- news_tool: 资讯事件
- risk_metric_tool: 风险指标
- macro_tool: 宏观数据整合

使用方式：
    from src.tools import market_data_tool, fundamental_tool
    report = market_data_tool(packet)
"""

# 从统一导出模块导入所有工具
from src.tools.data_tools import *  # noqa: F401, F403
from src.tools.data_tools import __all__
