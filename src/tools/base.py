"""
工具模块基础定义。
提供公共类型、常量和辅助函数。
"""

from __future__ import annotations

import pandas as pd

from src.data.calculator import CalculatedDataPacket

_NA_PLACEHOLDER = "[{tool}未激活：{reason}，本维度数据不可用，分析时请标注N/A]"


def _na(tool: str, reason: str) -> str:
    """返回标准 N/A 占位符字符串。"""
    return _NA_PLACEHOLDER.format(tool=tool, reason=reason)


def _meta_header(packet: CalculatedDataPacket) -> str:
    """生成元信息头部字符串（来源/时间/质量等级）。"""
    meta = packet.metadata
    return (
        f"**数据来源**：{meta.get('source', 'unknown')} | "
        f"**截止日期**：{meta.get('date', 'N/A')} | "
        f"**质量等级**：{meta.get('quality_level', 'N/A')} | "
        f"**标的代码**：{meta.get('stock_code', 'N/A')}"
    )


def _fmt_value(v) -> str:
    """通用数值格式化函数。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "N/A"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _fmt_float(val, fmt="{:.2f}", suffix="") -> str:
    """安全格式化数值，None/NaN 返回 N/A。"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "N/A"
    try:
        return fmt.format(float(val)) + suffix
    except (TypeError, ValueError):
        return "N/A"
