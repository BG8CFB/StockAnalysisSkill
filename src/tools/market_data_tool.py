"""
市场数据工具模块。

提供统一的市场数据工具，整合价格序列、技术指标和行情快照。
原 price_tool + indicator_tool + snapshot_tool → market_data_tool
"""

from __future__ import annotations

import pandas as pd

from src.data.calculator import CalculatedDataPacket
from src.tools.base import _na, _meta_header, _fmt_value


def market_data_tool(packet: CalculatedDataPacket, window: int = 250) -> str:
    """
    技术分析师：完整市场数据工具。

    整合以下原工具功能：
    - price_tool: 前复权OHLCV序列
    - indicator_tool: 技术指标（MACD/RSI/KDJ/布林带/均线）
    - snapshot_tool: 行情快照+关键技术位
    """
    if packet.price_series is None or packet.price_series.empty:
        return _na("market_data_tool", "行情数据不可用")

    sections = ["# 市场数据（整合）\n", _meta_header(packet), ""]

    # ========== Section 1: OHLCV 序列 ==========
    df = packet.price_series.tail(window)
    display = df.tail(20)
    sections.append("## 1. 价格数据（前复权，近20日）")
    sections.append(
        "| 日期 | 开盘(adj) | 最高(adj) | 最低(adj) | 收盘(adj) | 成交量 | 涨跌幅% |"
    )
    sections.append("|-----|---------|---------|---------|---------|------|-------|")

    for _, row in display.iterrows():
        date = str(row.get("trade_date", ""))
        o_val = f"{row.get('open_adj', 0):.2f}" if pd.notna(row.get("open_adj")) else "N/A"
        h_val = f"{row.get('high_adj', 0):.2f}" if pd.notna(row.get("high_adj")) else "N/A"
        l_val = f"{row.get('low_adj', 0):.2f}" if pd.notna(row.get("low_adj")) else "N/A"
        c_val = f"{row.get('close_adj', 0):.2f}" if pd.notna(row.get("close_adj")) else "N/A"
        v_val = f"{row.get('vol', 0):.0f}" if pd.notna(row.get("vol")) else "N/A"
        p_val = f"{row.get('pct_chg', 0):.2f}" if pd.notna(row.get("pct_chg")) else "N/A"
        sections.append(f"| {date} | {o_val} | {h_val} | {l_val} | {c_val} | {v_val} | {p_val} |")

    sections.append(
        f"\n*完整历史：共{len(df)}条记录（{df.iloc[0].get('trade_date', '?')} ~ {df.iloc[-1].get('trade_date', '?')}）*"
    )

    # 近20日异常事件
    anomalies = [a for a in (packet.anomalies or []) if a.get("date", "") >= str(df.iloc[-20].get("trade_date", ""))]
    if anomalies:
        sections.append("\n### 近20日异常事件")
        tag_map = {
            "limit_up": "涨停",
            "limit_down": "跌停",
            "one_word_limit_up": "一字涨停",
            "one_word_limit_down": "一字跌停",
            "volume_surge": "成交量异常",
            "gap_open": "跳空缺口",
        }
        for a in anomalies[-20:]:
            tag = tag_map.get(a.get("type", ""), a.get("type", ""))
            detail = ""
            if "pct_chg" in a:
                detail = f"涨跌幅：{a['pct_chg']:.2f}%"
            elif "vol_ratio" in a:
                detail = f"量比：{a['vol_ratio']}x"
            elif "gap_pct" in a:
                detail = f"跳空：{a['gap_pct']:.2f}%"
            sections.append(f"- {a.get('date', '')}: **{tag}** {detail}")

    # ========== Section 2: 技术指标 ==========
    sections.append("\n---\n")
    sections.append("## 2. 技术指标\n")

    # MACD
    m = packet.macd
    if m:
        sections.append("### MACD（12-26-9）")
        sections.append("| DIF | DEA | MACD柱 | 信号 | 最近金叉/死叉距今天数 |")
        sections.append("|-----|-----|-------|------|---------------------|")
        sections.append(
            f"| {_fmt_value(m.get('macd_dif'))} | {_fmt_value(m.get('macd_dea'))} "
            f"| {_fmt_value(m.get('macd_bar'))} | {m.get('macd_signal', 'N/A')} "
            f"| {_fmt_value(m.get('macd_cross_days'))} |"
        )

    # RSI
    r = packet.rsi
    if r:
        sections.append("\n### RSI（14）")
        sections.append("| RSI14 | 信号 | 背离 |")
        sections.append("|-------|------|------|")
        sections.append(
            f"| {_fmt_value(r.get('rsi_14'))} | {r.get('rsi_signal', 'N/A')} | {r.get('rsi_divergence', 'N/A')} |"
        )

    # KDJ
    k = packet.kdj
    if k:
        sections.append("\n### KDJ（9-3-3）")
        sections.append("| K | D | J | 信号 |")
        sections.append("|---|---|---|------|")
        sections.append(
            f"| {_fmt_value(k.get('kdj_k'))} | {_fmt_value(k.get('kdj_d'))} | {_fmt_value(k.get('kdj_j'))} | {k.get('kdj_signal', 'N/A')} |"
        )

    # 布林带
    b = packet.bollinger
    if b:
        sections.append("\n### 布林带（20-2）")
        sections.append("| 上轨 | 中轨 | 下轨 | 带宽 | 位置% | 信号 |")
        sections.append("|------|------|------|------|-------|------|")
        sections.append(
            f"| {_fmt_value(b.get('bb_upper'))} | {_fmt_value(b.get('bb_middle'))} "
            f"| {_fmt_value(b.get('bb_lower'))} | {_fmt_value(b.get('bb_bandwidth'))} "
            f"| {_fmt_value(b.get('bb_position'))} | {b.get('bb_signal', 'N/A')} |"
        )

    # 均线系统
    ma = packet.ma_system
    if ma:
        sections.append("\n### 均线系统")
        ma_vals = [(f"MA{p}", ma.get(f"ma_{p}")) for p in [5, 10, 20, 60, 120, 250]]
        sections.append("| " + " | ".join(n for n, _ in ma_vals) + " |")
        sections.append("| " + " | ".join("-----" for _ in ma_vals) + " |")
        sections.append("| " + " | ".join(_fmt_value(v) for _, v in ma_vals) + " |")
        sections.append(
            f"- 多头排列：{'是' if ma.get('ma_bullish_arrange') else '否'}  "
            f"空头排列：{'是' if ma.get('ma_bearish_arrange') else '否'}"
        )
        sections.append(
            f"- 价格偏离MA20：{_fmt_value(ma.get('price_vs_ma20'))}%  偏离MA60：{_fmt_value(ma.get('price_vs_ma60'))}%"
        )
        cross_info = []
        for key in ["ma_cross_5_20", "ma_cross_10_60", "ma_cross_20_60"]:
            if ma.get(key):
                cross_info.append(f"{key.replace('ma_cross_', '').replace('_', '/')}均线：{ma[key]}")
        if cross_info:
            sections.append(f"- 近期均线交叉：{', '.join(cross_info)}")

    # 成交量指标
    vi = packet.volume_indicators
    if vi:
        sections.append("\n### 成交量指标")
        sections.append(f"- 量比（当日成交量/5日均量）：{_fmt_value(vi.get('volume_ratio'))}")
        sections.append(f"- 振幅（今日）：{_fmt_value(vi.get('amplitude'))}%")

    # ========== Section 3: 行情快照 ==========
    sections.append("\n---\n")
    sections.append("## 3. 行情快照与关键技术位\n")

    last = df.iloc[-1]
    close = last.get("close_adj") or last.get("close", 0)
    pct = last.get("pct_chg", 0) or 0
    sign = "+" if float(pct) >= 0 else ""

    sections.append(f"- **最新收盘价**：{float(close):.2f} 元")
    sections.append(f"- **今日涨跌幅**：{sign}{float(pct):.2f}%")
    sections.append(f"- **日期**：{last.get('trade_date', 'N/A')}")

    # 近5日高低点
    recent_5 = df.tail(5)
    hi_5 = recent_5["high_adj"].max() if "high_adj" in recent_5.columns else recent_5.get("high", recent_5.get("close_adj", 0)).max()
    lo_5 = recent_5["low_adj"].min() if "low_adj" in recent_5.columns else recent_5.get("low", recent_5.get("close_adj", 0)).min()
    sections.append(f"- **近5日最高**：{float(hi_5):.2f} 元")
    sections.append(f"- **近5日最低**：{float(lo_5):.2f} 元")

    # 关键技术位
    sections.append("\n### 关键技术支撑阻力位")
    supports = []
    if ma.get("ma_20") and float(ma["ma_20"]) < float(close):
        supports.append(f"MA20={float(ma['ma_20']):.2f}")
    if ma.get("ma_60") and float(ma["ma_60"]) < float(close):
        supports.append(f"MA60={float(ma['ma_60']):.2f}")
    if b.get("bb_lower") and float(b["bb_lower"]) < float(close):
        supports.append(f"布林下轨={float(b['bb_lower']):.2f}")

    resistances = []
    if ma.get("ma_20") and float(ma["ma_20"]) > float(close):
        resistances.append(f"MA20={float(ma['ma_20']):.2f}")
    if ma.get("ma_60") and float(ma["ma_60"]) > float(close):
        resistances.append(f"MA60={float(ma['ma_60']):.2f}")
    if b.get("bb_upper") and float(b["bb_upper"]) > float(close):
        resistances.append(f"布林上轨={float(b['bb_upper']):.2f}")

    sections.append(f"- **支撑位**：{', '.join(supports) if supports else 'N/A'}")
    sections.append(f"- **阻力位**：{', '.join(resistances) if resistances else 'N/A'}")
    if b.get("bb_middle"):
        sections.append(f"- **布林带中轨**：{float(b['bb_middle']):.2f}")

    return "\n".join(sections)


# 保留向后兼容的别名（可选，用于平滑过渡）
def price_tool(packet: CalculatedDataPacket, window: int = 250) -> str:
    """【向后兼容】价格数据工具。"""
    return market_data_tool(packet, window)


def indicator_tool(packet: CalculatedDataPacket) -> str:
    """【向后兼容】技术指标工具。"""
    return market_data_tool(packet)


def snapshot_tool(packet: CalculatedDataPacket) -> str:
    """【向后兼容】行情快照工具。"""
    return market_data_tool(packet)
