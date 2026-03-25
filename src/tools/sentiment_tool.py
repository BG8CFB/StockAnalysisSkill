"""
市场情绪与资讯工具模块。
提供市场情绪、板块轮动和新闻公告相关工具。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from src.data.calculator import CalculatedDataPacket
from src.tools.base import _na, _meta_header


def sentiment_tool(packet: CalculatedDataPacket) -> str:
    """情绪分析师 + 微观结构分析师：全市场涨跌停统计 + 北向资金（近10日）。"""
    sentiment_df = packet.market_sentiment_raw

    if sentiment_df is None or sentiment_df.empty:
        return _na("sentiment_tool", "市场情绪数据不可用")

    lines = ["## 市场情绪数据\n", _meta_header(packet), ""]

    recent = sentiment_df.tail(10)
    lines.append("### 近10日市场涨跌停统计")
    lines.append("| 日期 | 涨停家数 | 跌停家数 | 情绪信号 |")
    lines.append("|-----|---------|---------|---------|")
    for _, row in recent.iterrows():
        d = str(row.get("trade_date", ""))
        up = int(row.get("limit_up_count", 0))
        down = int(row.get("limit_down_count", 0))
        ratio = up / max(down, 1)
        if ratio >= 5:
            signal = "[极度乐观]"
        elif ratio >= 2:
            signal = "[偏多]"
        elif ratio <= 0.5:
            signal = "[偏空]"
        elif ratio <= 0.2:
            signal = "[极度悲观]"
        else:
            signal = "[中性]"
        lines.append(f"| {d} | {up} | {down} | {signal} |")

    # 换手率信息（从 daily_basic 提取）
    if (
        packet.daily_basic is not None
        and not packet.daily_basic.empty
        and "turnover_rate" in packet.daily_basic.columns
    ):
        recent_basic = packet.daily_basic.tail(20)
        tr_avg = recent_basic["turnover_rate"].mean()
        tr_last = recent_basic.iloc[-1].get("turnover_rate")
        if pd.notna(tr_last) and pd.notna(tr_avg):
            vs = "高于" if float(tr_last) > float(tr_avg) else "低于"
            lines.append(
                f"\n**个股换手率（今日）**：{float(tr_last):.2f}%（20日均值：{float(tr_avg):.2f}%，{vs}均值）"
            )

    # RSI 情绪参考
    if packet.rsi:
        rsi_val = packet.rsi.get("rsi_14")
        if rsi_val is not None:
            lines.append(
                f"**个股RSI14**：{float(rsi_val):.1f}（信号：{packet.rsi.get('rsi_signal', 'N/A')}）"
            )

    return "\n".join(lines)


def sector_tool(packet: CalculatedDataPacket) -> str:
    """板块轮动分析师：概念/行业分类 + 动量因子。"""
    sector = packet.sector_raw
    mom = packet.momentum

    if not sector and not mom:
        return _na("sector_tool", "板块分类数据和动量数据均不可用")

    lines = ["## 板块轮动数据\n", _meta_header(packet), ""]

    # 概念/行业分类
    if sector:
        concepts = sector.get("concepts", [])
        if concepts:
            lines.append(f"**所属概念板块（{len(concepts)}个）**：")
            lines.append(", ".join(concepts[:20]))  # 最多显示20个
            if len(concepts) > 20:
                lines.append(f"*（另有{len(concepts) - 20}个概念，已截断）*")
        else:
            lines.append("*板块分类数据为空*")

    # 动量因子
    if mom:
        lines.append("\n### 动量因子")
        lines.append("| 1个月动量 | 3个月动量 | 6个月动量 | 3M跳过1M | 动量评分 |")
        lines.append("|---------|---------|---------|---------|---------|")

        def _m(k) -> str:
            v = mom.get(k)
            return f"{v:.2f}%" if v is not None and pd.notna(v) else "N/A"

        lines.append(
            f"| {_m('mom_1m')} | {_m('mom_3m')} | {_m('mom_6m')} "
            f"| {_m('mom_3m_skip1m')} | {mom.get('momentum_score', 'N/A')}/100 |"
        )

    return "\n".join(lines)


def news_tool(packet: CalculatedDataPacket) -> str:
    """资讯事件分析师：近7日新闻 + 近30日公告标题 + 停牌信息。"""
    news = packet.news_raw
    if not news:
        return _na("news_tool", "公告/新闻数据不可用（数据源不支持或近期无公告）")

    lines = ["## 资讯事件数据\n", _meta_header(packet), ""]

    # 按日期分组：近7日 / 近30日
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    recent_week = [n for n in news if str(n.get("ann_date", "")) >= week_ago]
    recent_month = [
        n for n in news if month_ago <= str(n.get("ann_date", "")) < week_ago
    ]

    if recent_week:
        lines.append("### 近7日重要公告")
        for item in recent_week[:10]:
            lines.append(f"- **{item.get('ann_date', '')}** — {item.get('title', '')}")
    else:
        lines.append("### 近7日公告\n*近7日无公告*")

    if recent_month:
        lines.append("\n### 近30日公告（7日前）")
        for item in recent_month[:20]:
            lines.append(f"- {item.get('ann_date', '')} — {item.get('title', '')}")

    # 停牌记录提示
    if packet.is_suspended:
        lines.append("\n[!] **当前状态：停牌中**")

    return "\n".join(lines)
