from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.data.cleaner import CleanedDataPacket

logger = logging.getLogger(__name__)


@dataclass
class CalculatedDataPacket:
    """清洗 + 计算后的完整数据包，供工具层注入智能体。"""
    metadata: dict
    is_suspended: bool
    # 原始数据（传递给工具层）
    price_series: Optional[pd.DataFrame]
    daily_basic: Optional[pd.DataFrame]
    capital_flow_raw: Optional[pd.DataFrame]
    margin_raw: Optional[pd.DataFrame]
    dragon_tiger_raw: Optional[list]
    sector_raw: Optional[dict]
    news_raw: Optional[list]
    financial_raw: Optional[dict]
    market_sentiment_raw: Optional[pd.DataFrame]
    anomalies: list[dict]
    missing_fields: list[str]
    available_tools: set[str]
    # 技术指标
    macd: dict = field(default_factory=dict)
    rsi: dict = field(default_factory=dict)
    kdj: dict = field(default_factory=dict)
    bollinger: dict = field(default_factory=dict)
    ma_system: dict = field(default_factory=dict)
    volume_indicators: dict = field(default_factory=dict)
    # 量化因子
    momentum: dict = field(default_factory=dict)
    value: dict = field(default_factory=dict)
    volatility: dict = field(default_factory=dict)
    capital_flow: dict = field(default_factory=dict)


def calculate(packet: CleanedDataPacket) -> CalculatedDataPacket:
    """计算所有技术指标和量化因子。"""
    logger.info(f"[指标] {packet.metadata.get('stock_code', 'unknown')} 开始计算技术指标")
    calc = CalculatedDataPacket(
        metadata=packet.metadata,
        is_suspended=packet.is_suspended,
        price_series=packet.price_series,
        daily_basic=packet.daily_basic,
        capital_flow_raw=packet.capital_flow_raw,
        margin_raw=packet.margin_raw,
        dragon_tiger_raw=packet.dragon_tiger_raw,
        sector_raw=packet.sector_raw,
        news_raw=packet.news_raw,
        financial_raw=packet.financial_raw,
        market_sentiment_raw=packet.market_sentiment_raw,
        anomalies=packet.anomalies,
        missing_fields=list(packet.missing_fields),
        available_tools=set(packet.available_tools),
    )

    if packet.is_suspended or packet.price_series is None or packet.price_series.empty:
        logger.info(f"[指标] 技术指标计算完成（MACD/RSI/KDJ/布林带/均线/VaR）")
        return calc

    df = packet.price_series
    close = df["close_adj"].dropna()
    high = df["high_adj"].dropna() if "high_adj" in df.columns else close
    low = df["low_adj"].dropna() if "low_adj" in df.columns else close
    vol = df["vol"] if "vol" in df.columns else pd.Series(dtype=float)

    # 按 docs/data/03_计算引擎规范.md 顺序计算
    calc.macd = compute_macd(close)
    calc.rsi = compute_rsi(close)
    calc.kdj = compute_kdj(high, low, close)
    calc.bollinger = compute_bollinger(close)
    calc.ma_system = compute_ma_system(close)
    calc.volume_indicators = compute_volume_indicators(vol, high, low, close)
    calc.momentum = compute_momentum_factors(close)
    calc.volatility = compute_volatility_factors(close)

    if packet.daily_basic is not None:
        calc.value = compute_value_factors(packet.daily_basic)

    if packet.capital_flow_raw is not None:
        calc.capital_flow = compute_capital_flow(packet.capital_flow_raw)

    logger.info(f"[指标] 技术指标计算完成（MACD/RSI/KDJ/布林带/均线/VaR）")
    return calc


# ──────────────────────────────────────────────
# 技术指标计算函数
# ──────────────────────────────────────────────

def compute_macd(close: pd.Series, fast=12, slow=26, signal=9) -> dict:
    """MACD 指标。字段名严格按 docs/data/03_计算引擎规范.md。"""
    if len(close) < slow + signal:
        return {}

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    bar = 2 * (dif - dea)

    last_dif = float(dif.iloc[-1])
    last_dea = float(dea.iloc[-1])

    macd_signal = "bullish" if last_dif > last_dea else "bearish"

    # 检测交叉
    cross = "none"
    cross_days = 0
    if len(dif) >= 2:
        prev_diff = float(dif.iloc[-2]) - float(dea.iloc[-2])
        curr_diff = last_dif - last_dea
        if prev_diff <= 0 < curr_diff:
            cross = "golden_cross"
            cross_days = 1
        elif prev_diff >= 0 > curr_diff:
            cross = "death_cross"
            cross_days = 1
        else:
            # 查找最近交叉距今天数
            for i in range(len(dif) - 2, 0, -1):
                d1 = float(dif.iloc[i]) - float(dea.iloc[i])
                d0 = float(dif.iloc[i - 1]) - float(dea.iloc[i - 1])
                if d0 * d1 < 0:
                    cross = "golden_cross" if d1 > 0 else "death_cross"
                    cross_days = len(dif) - 1 - i
                    break

    return {
        "macd_dif": round(last_dif, 4),
        "macd_dea": round(last_dea, 4),
        "macd_bar": round(float(bar.iloc[-1]), 4),
        "macd_signal": macd_signal,
        "macd_cross": cross,
        "macd_cross_days": cross_days,
    }


def compute_rsi(close: pd.Series, period: int = 14) -> dict:
    """RSI 指标。"""
    if len(close) < period + 1:
        return {}

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = float(rsi.iloc[-1])

    if val >= 70:
        sig = "overbought"
    elif val >= 55:
        sig = "strong"
    elif val >= 45:
        sig = "neutral"
    elif val >= 30:
        sig = "weak"
    else:
        sig = "oversold"

    # 简单背离检测（价格新高但 RSI 未创新高）
    divergence = "none"
    if len(close) >= 20 and len(rsi.dropna()) >= 5:
        recent_price = close.iloc[-5:]
        recent_rsi = rsi.iloc[-5:]
        if recent_price.iloc[-1] > recent_price.max() * 0.98 and recent_rsi.iloc[-1] < recent_rsi.max() * 0.95:
            divergence = "bearish_divergence"
        elif recent_price.iloc[-1] < recent_price.min() * 1.02 and recent_rsi.iloc[-1] > recent_rsi.min() * 1.05:
            divergence = "bullish_divergence"

    return {
        "rsi_14": round(val, 2),
        "rsi_signal": sig,
        "rsi_divergence": divergence,
    }


def compute_kdj(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 9) -> dict:
    """KDJ 指标。"""
    if len(close) < period:
        return {}

    lowest_low = low.rolling(period).min()
    highest_high = high.rolling(period).max()
    denom = (highest_high - lowest_low).replace(0, np.nan)
    rsv = ((close - lowest_low) / denom * 100).fillna(50)

    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d

    kv, dv, jv = float(k.iloc[-1]), float(d.iloc[-1]), float(j.iloc[-1])

    if kv > 80 and dv > 80:
        sig = "overbought"
    elif kv < 20 and dv < 20:
        sig = "oversold"
    elif len(k) >= 2 and float(k.iloc[-2]) <= float(d.iloc[-2]) and kv > dv:
        sig = "golden_cross"
    elif len(k) >= 2 and float(k.iloc[-2]) >= float(d.iloc[-2]) and kv < dv:
        sig = "death_cross"
    else:
        sig = "neutral"

    return {
        "kdj_k": round(kv, 2),
        "kdj_d": round(dv, 2),
        "kdj_j": round(jv, 2),
        "kdj_signal": sig,
    }


def compute_bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0) -> dict:
    """布林带指标。"""
    if len(close) < period:
        return {}

    middle = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std

    mv, uv, lv = float(middle.iloc[-1]), float(upper.iloc[-1]), float(lower.iloc[-1])
    cv = float(close.iloc[-1])

    bandwidth = round((uv - lv) / mv * 100, 2) if mv != 0 else 0
    position = round((cv - lv) / (uv - lv), 4) if (uv - lv) != 0 else 0.5

    if cv > uv:
        sig = "above_upper"
    elif cv < lv:
        sig = "below_lower"
    elif len(close) >= 2:
        prev = float(close.iloc[-2])
        prev_mid = float(middle.iloc[-2])
        if prev < prev_mid and cv > mv:
            sig = "break_middle_up"
        else:
            sig = "within_band"
    else:
        sig = "within_band"

    return {
        "bb_upper": round(uv, 4),
        "bb_middle": round(mv, 4),
        "bb_lower": round(lv, 4),
        "bb_bandwidth": bandwidth,
        "bb_position": position,
        "bb_signal": sig,
    }


def compute_ma_system(close: pd.Series) -> dict:
    """均线系统。"""
    result: dict[str, Any] = {}
    periods = [5, 10, 20, 30, 60, 120, 250]

    ma_vals: dict[int, float] = {}
    for p in periods:
        if len(close) >= p:
            val = float(close.rolling(p).mean().iloc[-1])
            result[f"ma_{p}"] = round(val, 4)
            ma_vals[p] = val
        else:
            result[f"ma_{p}"] = None

    # 多头排列：MA5 > MA10 > MA20 > MA60
    keys = [5, 10, 20, 60]
    if all(ma_vals.get(k) is not None for k in keys):
        result["ma_bullish_arrange"] = all(
            ma_vals[keys[i]] > ma_vals[keys[i + 1]] for i in range(len(keys) - 1)
        )
        result["ma_bearish_arrange"] = all(
            ma_vals[keys[i]] < ma_vals[keys[i + 1]] for i in range(len(keys) - 1)
        )
    else:
        result["ma_bullish_arrange"] = None
        result["ma_bearish_arrange"] = None

    # 均线交叉检测
    for short, long in [(5, 10), (5, 20), (10, 20), (20, 60)]:
        key = f"ma_cross_{short}_{long}"
        if len(close) >= long + 2:
            ma_s = close.rolling(short).mean()
            ma_l = close.rolling(long).mean()
            prev = float(ma_s.iloc[-2]) - float(ma_l.iloc[-2])
            curr = float(ma_s.iloc[-1]) - float(ma_l.iloc[-1])
            if prev <= 0 < curr:
                result[key] = "golden"
            elif prev >= 0 > curr:
                result[key] = "death"
            else:
                result[key] = "none"
        else:
            result[key] = None

    # 价格偏离均线
    cv = float(close.iloc[-1])
    for p, label in [(20, "ma20"), (60, "ma60")]:
        key = f"price_vs_{label}"
        if ma_vals.get(p):
            result[key] = round((cv - ma_vals[p]) / ma_vals[p] * 100, 2)
        else:
            result[key] = None

    return result


def compute_volume_indicators(vol: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> dict:
    """成交量指标。振幅公式：(今日最高 - 今日最低) / 昨日收盘 × 100。"""
    if vol is None or len(vol) < 5:
        return {}

    vol_ma5 = float(vol.rolling(5).mean().iloc[-1])
    vol_today = float(vol.iloc[-1])
    volume_ratio = round(vol_today / vol_ma5, 2) if vol_ma5 != 0 else 1.0

    # 振幅：(今日最高 - 今日最低) / 昨日收盘 × 100
    amplitude = None
    if len(close) >= 2 and high is not None and low is not None and len(high) >= 1 and len(low) >= 1:
        prev_close = float(close.iloc[-2])
        if prev_close != 0:
            amplitude = round(
                (float(high.iloc[-1]) - float(low.iloc[-1])) / prev_close * 100, 2
            )

    return {
        "volume_ratio": volume_ratio,
        "amplitude": amplitude,
    }


# ──────────────────────────────────────────────
# 量化因子计算函数
# ──────────────────────────────────────────────

def compute_momentum_factors(close: pd.Series) -> dict:
    """动量因子。字段名严格按规范。"""
    result: dict[str, Any] = {}
    n = len(close)

    def mom(days_ago: int, skip: int = 0) -> Optional[float]:
        end_idx = n - 1 - skip
        start_idx = end_idx - days_ago
        if start_idx < 0 or end_idx < 0:
            return None
        v_end = float(close.iloc[end_idx])
        v_start = float(close.iloc[start_idx])
        if v_start == 0:
            return None
        return round(v_end / v_start - 1, 4)

    result["mom_1m"] = mom(20)
    result["mom_3m"] = mom(60)
    result["mom_6m"] = mom(120)
    result["mom_3m_skip1m"] = mom(60, skip=20)
    result["mom_6m_skip1m"] = mom(120, skip=20)

    # 动量评分（基准50）
    score = 50
    m1 = result["mom_1m"]
    m3 = result["mom_3m"]
    m6 = result["mom_6m"]
    if m1 is not None:
        if m1 > 0.05:
            score += 15
        elif m1 < -0.05:
            score -= 15
    if m3 is not None:
        if m3 > 0.15:
            score += 15
        elif m3 < -0.10:
            score -= 15
    if m6 is not None:
        if m6 > 0.30:
            score += 10
        elif m6 < -0.20:
            score -= 10

    result["momentum_score"] = max(0, min(100, score))
    return result


def compute_value_factors(daily_basic: pd.DataFrame) -> dict:
    """价值因子，从最新一行基本面数据提取。"""
    if daily_basic is None or daily_basic.empty:
        return {}

    latest = daily_basic.iloc[-1]

    def get(col: str) -> Optional[float]:
        v = latest.get(col)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v)

    pe = get("pe_ttm")
    pb = get("pb_mrq")
    ps = get("ps_ttm")
    div = get("dividend_yield")

    # 价值评分（基准50）
    score = 50
    if pe is not None and pe > 0:
        if pe < 10:
            score += 20
        elif pe < 20:
            score += 10
        elif pe > 50:
            score -= 15
    if pb is not None and pb > 0:
        if pb < 1:
            score += 15
        elif pb < 2:
            score += 5
    if div is not None:
        if div > 5:
            score += 20
        elif div > 3:
            score += 10

    return {
        "pe_ttm": round(pe, 2) if pe else None,
        "pb_mrq": round(pb, 2) if pb else None,
        "ps_ttm": round(ps, 2) if ps else None,
        "dividend_yield": round(div, 2) if div else None,
        "value_score": max(0, min(100, score)),
    }


def compute_volatility_factors(close: pd.Series) -> dict:
    """波动率因子。"""
    if len(close) < 20:
        return {}

    returns = close.pct_change().dropna()

    # 20日年化波动率
    vol_20d = float(returns.iloc[-20:].std() * np.sqrt(252) * 100)

    # 20日最大回撤
    window = close.iloc[-20:]
    peak = window.expanding().max()
    drawdown = (window - peak) / peak
    max_dd = float(drawdown.min() * 100)

    # 波动率评分（基准50，低波动 = 高分）
    score = 50
    if vol_20d < 20:
        score += 25
    elif vol_20d < 30:
        score += 15
    elif vol_20d < 40:
        score += 5
    elif vol_20d > 80:
        score -= 35
    elif vol_20d > 60:
        score -= 20

    return {
        "volatility_20d": round(vol_20d, 2),
        "max_drawdown_20d": round(max_dd, 2),
        "volatility_score": max(0, min(100, score)),
    }


def compute_capital_flow(capital_flow_raw: pd.DataFrame) -> dict:
    """资金流向（A股专属）。"""
    if capital_flow_raw is None or capital_flow_raw.empty:
        return {"capital_signal": "data_unavailable"}

    latest = capital_flow_raw.iloc[-1]

    def get(col: str) -> Optional[float]:
        v = latest.get(col)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v)

    main_today = get("main_net_today")
    main_3d = get("main_net_3d")
    main_5d = get("main_net_5d")
    main_10d = get("main_net_10d")
    retail_today = get("retail_net_today")
    northbound = get("northbound_flow")
    main_ratio = get("main_ratio")

    # 资金流评分（基准50）
    score = 50
    if main_today is not None:
        if main_today > 5000:
            score += 20
        elif main_today > 1000:
            score += 10
        elif main_today < -5000:
            score -= 20
        elif main_today < -1000:
            score -= 10
    if main_5d is not None:
        if main_5d > 10000:
            score += 15
        elif main_5d < -10000:
            score -= 15
    if northbound is not None:
        if northbound > 10000:
            score += 10
        elif northbound < -10000:
            score -= 10

    score = max(0, min(100, score))

    if score >= 80:
        signal = "strong_inflow"
    elif score >= 65:
        signal = "inflow"
    elif score >= 36:
        signal = "neutral"
    elif score >= 21:
        signal = "outflow"
    else:
        signal = "strong_outflow"

    return {
        "main_net_today": main_today,
        "main_net_3d": main_3d,
        "main_net_5d": main_5d,
        "main_net_10d": main_10d,
        "retail_net_today": retail_today,
        "northbound_flow": northbound,
        "main_ratio": main_ratio,
        "capital_score": score,
        "capital_signal": signal,
    }
