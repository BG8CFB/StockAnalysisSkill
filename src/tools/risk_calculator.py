"""
风控计算模块。
纯代码逻辑，无 LLM 调用。
按 docs/tools/03_风控计算模块.md 规范实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class SuspensionCheckResult:
    is_suspended: bool
    suspend_type: Optional[str] = None


@dataclass
class VaRResult:
    var_daily_pct: float  # 日VaR百分比（绝对值）
    var_holding_pct: float  # 持有期VaR百分比（绝对值）
    var_amount: float  # 持有期VaR金额（元）
    confidence_level: float  # 置信水平
    holding_days: int  # 持有期天数
    exceeds_threshold: Optional[bool]  # 是否超过10%阈值（数据不足时为None）
    data_points: int  # 历史数据点数
    error: Optional[str] = None


@dataclass
class AShareRiskResult:
    # T+1 制度风险
    t1_risk_score: float
    t1_risk_level: str  # low / medium / high / extreme
    max_overnight_gap: Optional[float]  # 近60日最大隔夜低开幅度（%，负值）
    upcoming_events: list[str] = field(default_factory=list)

    # 涨跌停流动性风险
    limit_up_risk_score: float = 0.0
    is_limit_up: bool = False
    is_limit_down: bool = False
    limit_up_amount: float = 0.0  # 封单金额（万元）

    # 停牌风险
    suspend_risk_score: float = 0.0
    is_suspended_today: bool = False
    in_earnings_window: bool = False
    recent_suspend_count: int = 0

    # 融资融券风险
    margin_risk_score: float = 0.0
    margin_ratio: Optional[float] = None  # 融资占流通市值比（%）
    margin_change_5d: Optional[float] = None  # 5日融资变化率（%）
    data_available: bool = False

    # 综合
    composite_score: float = 0.0
    recommendation: str = "normal"  # normal / cautious / reduce_position / veto


def check_suspension(packet) -> SuspensionCheckResult:
    """检查当日停牌状态。"""
    if packet.is_suspended:
        return SuspensionCheckResult(is_suspended=True, suspend_type="suspended")
    return SuspensionCheckResult(is_suspended=False)


def calculate_var(
    packet,
    position_value: float,
    holding_days: int = 10,
    confidence: float = 0.95,
) -> VaRResult:
    """
    历史模拟法 VaR 计算。
    公式：VaR = position_value × |第5百分位日收益率| × √持有期
    至少需要 120 个交易日历史数据。
    """
    MIN_DATA_POINTS = 120

    price_df = packet.price_series
    if price_df is None or price_df.empty:
        return VaRResult(
            var_daily_pct=0.0,
            var_holding_pct=0.0,
            var_amount=0.0,
            confidence_level=confidence,
            holding_days=holding_days,
            exceeds_threshold=None,
            data_points=0,
            error="price_series unavailable",
        )

    close_col = "close_adj" if "close_adj" in price_df.columns else "close"
    if close_col not in price_df.columns:
        return VaRResult(
            var_daily_pct=0.0,
            var_holding_pct=0.0,
            var_amount=0.0,
            confidence_level=confidence,
            holding_days=holding_days,
            exceeds_threshold=None,
            data_points=0,
            error="close price column unavailable",
        )

    prices = price_df[close_col].dropna()
    n = len(prices)

    if n < MIN_DATA_POINTS:
        return VaRResult(
            var_daily_pct=0.0,
            var_holding_pct=0.0,
            var_amount=0.0,
            confidence_level=confidence,
            holding_days=holding_days,
            exceeds_threshold=None,
            data_points=n,
            error=f"insufficient data: {n} < {MIN_DATA_POINTS} required",
        )

    # 计算日收益率
    daily_returns = prices.pct_change().dropna()

    # 第5百分位（95%置信水平 → 最差5%）
    var_pct_daily = float(abs(np.percentile(daily_returns, (1 - confidence) * 100)))

    # 持有期缩放：VaR × √holding_days（假设收益率独立）
    var_pct_holding = var_pct_daily * np.sqrt(holding_days)

    # VaR 金额
    var_amount = position_value * var_pct_holding

    # 是否超过阈值（10%）
    exceeds_threshold = var_pct_holding > 0.10

    return VaRResult(
        var_daily_pct=round(var_pct_daily * 100, 2),  # 转为百分比
        var_holding_pct=round(var_pct_holding * 100, 2),
        var_amount=round(var_amount, 2),
        confidence_level=confidence,
        holding_days=holding_days,
        exceeds_threshold=exceeds_threshold,
        data_points=n,
    )


def calculate_a_share_risk(packet) -> AShareRiskResult:
    """
    A股特有风险评分（仅 .SZ/.SH 调用）。
    按 docs/tools/03_风控计算模块.md 的加分规则逐项计算。
    """
    result = AShareRiskResult(
        t1_risk_score=0.0,
        t1_risk_level="low",
        max_overnight_gap=None,
    )

    price_df = packet.price_series
    stock_code = packet.metadata.get("stock_code", "")

    # ── 1. T+1 制度风险 ──────────────────────────────────────────────────
    t1_score = 0.0

    if price_df is not None and not price_df.empty:
        close_col = "close_adj" if "close_adj" in price_df.columns else "close"
        open_col = "open_adj" if "open_adj" in price_df.columns else "open"

        # 20日年化波动率 > 30%？
        if close_col in price_df.columns and len(price_df) >= 20:
            returns = price_df[close_col].pct_change().dropna()
            vol_20d = float(returns.tail(20).std() * np.sqrt(252) * 100)
            if vol_20d > 30:
                t1_score += 20

        # 近60日最大隔夜低开幅度 > 5%？
        if (
            open_col in price_df.columns
            and close_col in price_df.columns
            and len(price_df) >= 2
        ):
            recent = price_df.tail(60)
            prev_close = recent[close_col].shift(1)
            curr_open = recent[open_col]
            overnight_gaps = ((curr_open - prev_close) / prev_close * 100).dropna()
            if len(overnight_gaps) > 0:
                max_gap = float(overnight_gaps.min())  # 最差（最负的）跳空
                result.max_overnight_gap = round(max_gap, 2)
                if abs(max_gap) > 5:
                    t1_score += 15

    # 3日内有业绩公告（通过 news_raw 检测关键词）
    news = packet.news_raw or []
    today_str = datetime.now().strftime("%Y%m%d")
    upcoming_events = []
    for item in news:
        ann_date = str(item.get("ann_date", ""))
        title = str(item.get("title", ""))
        if ann_date >= today_str and any(
            kw in title for kw in ["业绩", "年报", "季报", "半年报", "预告"]
        ):
            upcoming_events.append(title)
    if upcoming_events:
        t1_score += 25
    result.upcoming_events = upcoming_events

    result.t1_risk_score = min(t1_score, 100.0)
    if t1_score < 20:
        result.t1_risk_level = "low"
    elif t1_score < 40:
        result.t1_risk_level = "medium"
    elif t1_score < 60:
        result.t1_risk_level = "high"
    else:
        result.t1_risk_level = "extreme"

    # ── 2. 涨跌停流动性风险 ─────────────────────────────────────────────
    limit_score = 0.0

    if price_df is not None and not price_df.empty and "pct_chg" in price_df.columns:
        last_row = price_df.iloc[-1]
        pct = float(last_row.get("pct_chg", 0) or 0)
        code_upper = stock_code.upper()
        if "300" in code_upper or "688" in code_upper:
            limit_pct = 19.5
        elif "ST" in code_upper:
            limit_pct = 4.5
        else:
            limit_pct = 9.5

        if pct >= limit_pct:
            result.is_limit_up = True
            # 封单金额无法从行情直接获取，默认当作不满足1000万条件（保守）
            result.limit_up_amount = 0.0
            limit_score += 20

        if pct <= -limit_pct:
            result.is_limit_down = True

    # 市场跌停家数 > 100？
    sentiment_df = packet.market_sentiment_raw
    if (
        sentiment_df is not None
        and not sentiment_df.empty
        and "limit_down_count" in sentiment_df.columns
    ):
        last_down = int(sentiment_df.iloc[-1].get("limit_down_count", 0))
        if last_down > 100:
            limit_score += 30

    result.limit_up_risk_score = min(limit_score, 100.0)

    # ── 3. 停牌风险 ──────────────────────────────────────────────────────
    suspend_score = 0.0
    result.is_suspended_today = packet.is_suspended

    if not packet.is_suspended:
        # 近90日内有停牌记录？通过 metadata 中 anomalies 或直接用 price_df 判断
        # 简单检测：通过 price_df 中成交量为0的交易日识别可能停牌
        if price_df is not None and len(price_df) >= 2 and "vol" in price_df.columns:
            recent_90 = price_df.tail(90)
            zero_vol_days = int((recent_90["vol"] == 0).sum())
            if zero_vol_days > 0:
                result.recent_suspend_count = zero_vol_days
                suspend_score += 10

        # 业绩窗口期（季末后45天内：1-2月中旬、4-5月中旬、7-8月中旬、10-11月中旬）
        month = datetime.now().month
        day = datetime.now().day
        in_window = (
            (month == 1)
            or (month == 2 and day <= 15)
            or (month == 4)
            or (month == 5 and day <= 15)
            or (month == 7)
            or (month == 8 and day <= 15)
            or (month == 10)
            or (month == 11 and day <= 15)
        )
        result.in_earnings_window = in_window
        if in_window:
            suspend_score += 15

    result.suspend_risk_score = min(suspend_score, 100.0)

    # ── 4. 融资融券风险 ──────────────────────────────────────────────────
    margin_score = 0.0
    margin_df = packet.margin_raw

    if margin_df is not None and not margin_df.empty:
        result.data_available = True
        last_margin = margin_df.iloc[-1]

        margin_ratio = last_margin.get("margin_ratio")
        margin_change_5d = last_margin.get("margin_change_5d")

        if margin_ratio is not None and not pd.isna(margin_ratio):
            result.margin_ratio = round(float(margin_ratio), 2)
            if result.margin_ratio > 15:
                margin_score += 30

        if margin_change_5d is not None and not pd.isna(margin_change_5d):
            result.margin_change_5d = round(float(margin_change_5d), 2)
            if result.margin_change_5d < -20:
                margin_score += 25

        # 两条件同时满足（融资占比>10% 且 5日降幅>10%）→ 额外+20
        cond_ratio = result.margin_ratio is not None and result.margin_ratio > 10
        cond_change = (
            result.margin_change_5d is not None and result.margin_change_5d < -10
        )
        if cond_ratio and cond_change:
            margin_score += 20
    else:
        result.data_available = False

    result.margin_risk_score = min(margin_score, 100.0)

    # ── 5. 综合评分（加权） ───────────────────────────────────────────────
    # 权重：融资40% + T+1 25% + 涨跌停20% + 停牌15%
    composite = (
        result.margin_risk_score * 0.40
        + result.t1_risk_score * 0.25
        + result.limit_up_risk_score * 0.20
        + result.suspend_risk_score * 0.15
    )
    result.composite_score = round(composite, 1)

    if composite >= 70:
        result.recommendation = "veto"
    elif composite >= 50:
        result.recommendation = "reduce_position"
    elif composite >= 30:
        result.recommendation = "cautious"
    else:
        result.recommendation = "normal"

    return result


def format_risk_results(
    var_result: VaRResult,
    a_share_result: Optional[AShareRiskResult],
) -> str:
    """
    将风控计算结果格式化为 risk_metric_tool 所需的 Markdown 字符串。
    注入 Stage 3 所有风控智能体的 user_context。
    """
    lines = ["## 风险度量指标（纯代码计算结果）\n"]

    # VaR 结果
    lines.append("### 一、VaR 计算（历史模拟法，95%置信水平）\n")
    if var_result.error:
        lines.append(f"⚠️ VaR 计算失败：{var_result.error}\n")
    else:
        lines.append("| 指标 | 数值 |")
        lines.append("|-----|------|")
        lines.append(f"| 日VaR | {var_result.var_daily_pct:.2f}% |")
        lines.append(
            f"| {var_result.holding_days}日持有期VaR | {var_result.var_holding_pct:.2f}% |"
        )
        lines.append(f"| VaR金额 | ¥{var_result.var_amount:,.0f} 元 |")
        lines.append(f"| 置信水平 | {var_result.confidence_level * 100:.0f}% |")
        lines.append(f"| 历史数据点数 | {var_result.data_points} 个交易日 |")
        threshold_text = (
            "**⚠️ 超过10%阈值，建议降仓**"
            if var_result.exceeds_threshold
            else "✅ 未超过阈值"
        )
        if var_result.exceeds_threshold is None:
            threshold_text = "⚠️ 数据不足无法判断"
        lines.append(f"| 是否超过阈值 | {threshold_text} |")
        lines.append("")

    # A股特有风险
    if a_share_result is None:
        lines.append("### 二、A股特有风险评分\n")
        lines.append("*本标的非A股，不适用A股风险评分。*\n")
    else:
        lines.append("### 二、A股特有风险评分\n")
        lines.append("| 风险维度 | 分项评分 | 权重 | 加权得分 |")
        lines.append("|---------|---------|-----|---------|")
        lines.append(
            f"| 融资融券风险 | {a_share_result.margin_risk_score:.0f} | 40% | {a_share_result.margin_risk_score * 0.4:.1f} |"
        )
        lines.append(
            f"| T+1制度风险 | {a_share_result.t1_risk_score:.0f} | 25% | {a_share_result.t1_risk_score * 0.25:.1f} |"
        )
        lines.append(
            f"| 涨跌停流动性风险 | {a_share_result.limit_up_risk_score:.0f} | 20% | {a_share_result.limit_up_risk_score * 0.2:.1f} |"
        )
        lines.append(
            f"| 停牌风险 | {a_share_result.suspend_risk_score:.0f} | 15% | {a_share_result.suspend_risk_score * 0.15:.1f} |"
        )
        lines.append(f"| **综合评分** | | | **{a_share_result.composite_score:.1f}** |")
        lines.append("")

        rec_map = {
            "veto": "🚫 **建议否决交易**（综合评分≥70，风险过高）",
            "reduce_position": "⚠️ **建议降仓30%执行**（综合评分50-69）",
            "cautious": "🟡 **谨慎可接受**（综合评分30-49，充分披露风险）",
            "normal": "✅ **风险可控**（综合评分<30，正常执行）",
        }
        lines.append(
            f"**交易建议**：{rec_map.get(a_share_result.recommendation, a_share_result.recommendation)}\n"
        )

        lines.append("**分项详情**：")
        lines.append(f"- T+1风险等级：{a_share_result.t1_risk_level}")
        if a_share_result.max_overnight_gap is not None:
            lines.append(
                f"  - 近60日最大隔夜低开：{a_share_result.max_overnight_gap:.2f}%"
            )
        if a_share_result.upcoming_events:
            lines.append(
                f"  - 近期重大事件：{', '.join(a_share_result.upcoming_events[:3])}"
            )
        lines.append(
            f"- 当日涨停：{'是' if a_share_result.is_limit_up else '否'}，跌停：{'是' if a_share_result.is_limit_down else '否'}"
        )
        if a_share_result.data_available:
            mr = (
                f"{a_share_result.margin_ratio:.2f}%"
                if a_share_result.margin_ratio is not None
                else "N/A"
            )
            mc = (
                f"{a_share_result.margin_change_5d:.2f}%"
                if a_share_result.margin_change_5d is not None
                else "N/A"
            )
            lines.append(f"- 融资余额占流通市值：{mr}，5日变化：{mc}")
        else:
            lines.append("- 融资融券数据：不可用（可能无融资资格）")
        if a_share_result.in_earnings_window:
            lines.append("- ⚠️ 当前处于业绩窗口期")

    return "\n".join(lines)
