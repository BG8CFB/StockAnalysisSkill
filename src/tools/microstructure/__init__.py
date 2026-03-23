"""
市场微观结构工具模块。
提供资金流向、融资融券和龙虎榜相关工具。
"""

from __future__ import annotations

import pandas as pd

from src.data.calculator import CalculatedDataPacket
from src.tools.base import _na, _meta_header


def capital_flow_tool(packet: CalculatedDataPacket) -> str:
    """市场微观结构分析师：资金流向指标（近10日）。"""
    cf_df = packet.capital_flow_raw
    cf_calc = packet.capital_flow

    if (cf_df is None or cf_df.empty) and not cf_calc:
        return _na("capital_flow_tool", "资金流向数据不可用（数据源积分不足或非A股）")

    lines = ["## 资金流向数据\n", _meta_header(packet), ""]

    # 近10日明细
    if cf_df is not None and not cf_df.empty:
        recent = cf_df.tail(10)
        lines.append("### 近10日主力资金流向（万元）")
        lines.append(
            "| 日期 | 主力净流入(今日) | 3日累计 | 5日累计 | 10日累计 | 散户净流入 | 北向资金 |"
        )
        lines.append(
            "|-----|--------------|--------|--------|---------|---------|---------|"
        )

        def _f(v):
            if pd.isna(v) or v == "":
                return "N/A"
            try:
                return f"{float(v):.0f}"
            except (ValueError, TypeError):
                return "N/A"

        for _, row in recent.iterrows():
            d = str(row.get("trade_date", ""))
            mn = _f(row.get("main_net_today", 0))
            m3 = _f(row.get("main_net_3d", 0))
            m5 = _f(row.get("main_net_5d", 0))
            m10 = _f(row.get("main_net_10d", 0))
            rn = _f(row.get("retail_net_today", 0))
            nb = _f(row.get("northbound_flow", 0))
            lines.append(f"| {d} | {mn} | {m3} | {m5} | {m10} | {rn} | {nb} |")

    # 资金评分
    if cf_calc:
        lines.append(f"\n**资金流向评分**：{cf_calc.get('capital_score', 'N/A')}/100")
        lines.append(f"**资金信号**：{cf_calc.get('capital_signal', 'N/A')}")
        mr = cf_calc.get("main_ratio")
        if mr is not None:
            lines.append(f"**主力净流入占成交额比**：{mr:.2f}%")

    return "\n".join(lines)


def margin_tool(packet: CalculatedDataPacket) -> str:
    """市场微观结构分析师：融资融券数据（近10日）。"""
    if packet.margin_raw is None or packet.margin_raw.empty:
        return _na("margin_tool", "融资融券数据不可用（无融资资格或数据源不支持）")

    df = packet.margin_raw.tail(10)
    lines = ["## 融资融券数据\n", _meta_header(packet), ""]

    lines.append("### 近10日融资融券（注：数据延迟T+1）")
    lines.append("| 日期 | 融资余额(万元) | 融资占流通市值% | 5日变化率% | 融券余量 |")
    lines.append("|-----|------------|--------------|---------|---------|")
    for _, row in df.iterrows():
        d = str(row.get("trade_date", ""))
        mb = (
            f"{row.get('margin_balance', 0):.0f}"
            if pd.notna(row.get("margin_balance"))
            else "N/A"
        )
        mr = (
            f"{row.get('margin_ratio', 0):.2f}"
            if pd.notna(row.get("margin_ratio"))
            else "N/A"
        )
        mc = (
            f"{row.get('margin_change_5d', 0):.2f}"
            if pd.notna(row.get("margin_change_5d"))
            else "N/A"
        )
        sb = (
            f"{row.get('short_balance', 0):.0f}"
            if pd.notna(row.get("short_balance"))
            else "N/A"
        )
        lines.append(f"| {d} | {mb} | {mr} | {mc} | {sb} |")

    return "\n".join(lines)


def dragon_tiger_tool(packet: CalculatedDataPacket) -> str:
    """市场微观结构分析师：最近3次龙虎榜记录。"""
    records = packet.dragon_tiger_raw
    if not records:
        return _na("dragon_tiger_tool", "龙虎榜数据不可用或近期未上榜")

    lines = ["## 龙虎榜数据\n", _meta_header(packet), ""]
    lines.append("### 最近龙虎榜记录（最多30条）")

    for rec in records[:30]:
        lines.append(
            f"- **{rec.get('trade_date', '?')}** | "
            f"原因：{rec.get('reason', 'N/A')} | "
            f"买入额：{rec.get('buy_amount', 0):.0f}万 | "
            f"卖出额：{rec.get('sell_amount', 0):.0f}万 | "
            f"净额：{rec.get('net_amount', 0):.0f}万"
        )

    return "\n".join(lines)
