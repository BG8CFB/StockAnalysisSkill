"""
风险指标工具模块。
提供风控计算结果格式化工具（Stage 3 专用）。
"""

from __future__ import annotations

from src.tools.base import _na


def risk_metric_tool(risk_results: dict) -> str:
    """
    Stage 3 风控智能体专用工具。
    入参为 format_risk_results() 已生成的 Markdown 字符串（直接传入）。
    或传入 {"formatted": str} dict。
    """
    if not risk_results:
        return _na("risk_metric_tool", "风控计算结果不可用")

    if isinstance(risk_results, str):
        return risk_results

    formatted = risk_results.get("formatted", "")
    if not formatted:
        return _na("risk_metric_tool", "风控计算结果格式错误")

    return formatted
