from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CleanedDataPacket:
    """清洗后的标准化数据包。"""
    metadata: dict                         # code, name, date, source, quality_level
    is_suspended: bool                     # 当日是否停牌
    price_series: Optional[pd.DataFrame]  # 前复权 OHLCV + pct_chg
    daily_basic: Optional[pd.DataFrame]   # PE/PB/PS 等基本面数据
    capital_flow_raw: Optional[pd.DataFrame]  # 资金流向（A股）
    margin_raw: Optional[pd.DataFrame]    # 融资融券
    dragon_tiger_raw: Optional[list]      # 龙虎榜
    sector_raw: Optional[dict]            # 板块分类
    news_raw: Optional[list]              # 新闻公告
    financial_raw: Optional[dict]         # 财务三表摘要（含 fina_indicator / income / cashflow / balancesheet）
    market_sentiment_raw: Optional[pd.DataFrame]  # 市场整体涨跌停统计
    shareholder_raw: Optional[dict]       # 股东人数趋势 / 质押 / 回购（仅 A 股）
    dividend_raw: Optional[list]          # 分红历史
    anomalies: list[dict] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    available_tools: set[str] = field(default_factory=set)
    macro_data: dict = field(default_factory=dict)  # 宏观数据


def clean(raw: dict, available_tools: set[str]) -> CleanedDataPacket:
    """
    数据清洗主入口。
    严格按 docs/data/02_数据清洗规范.md 执行：
    1. 前复权处理
    2. 停牌检测（硬终止）
    3. 异常值检测标记
    4. 空值处理
    """
    metadata = raw.get("metadata", {})
    stock_code = raw.get("metadata", {}).get("stock_code", "unknown")
    logger.info(f"[清洗] {stock_code} 开始数据清洗")

    # Step 2: 停牌检测（优先级最高）
    suspend_info = raw.get("suspend_info")
    is_suspended = False
    if suspend_info:
        is_suspended = bool(suspend_info.get("is_suspended", False))

    if is_suspended:
        result = CleanedDataPacket(
            metadata={**metadata, "quality_level": "suspended"},
            is_suspended=True,
            price_series=None,
            daily_basic=None,
            capital_flow_raw=None,
            margin_raw=None,
            dragon_tiger_raw=None,
            sector_raw=None,
            news_raw=None,
            financial_raw=None,
            market_sentiment_raw=None,
            shareholder_raw=None,
            dividend_raw=[],
            missing_fields=["all_fields_unavailable_due_to_suspension"],
            available_tools=set(),
            macro_data=raw.get("macro_data", {}),
        )
        logger.info(f"[清洗] {stock_code} 数据清洗完成（停牌={result.is_suspended}，可用工具={len(result.available_tools)}个）")
        return result

    # Step 1: 前复权处理
    price_series = _process_price_series(raw.get("price_series"))
    anomalies: list[dict] = []
    missing_fields: list[str] = []

    if price_series is not None and not price_series.empty:
        # Step 3: 异常值检测
        anomalies = _detect_anomalies(price_series, stock_code)
    else:
        missing_fields.append("price_series")

    # Step 4: 处理其他字段空值
    daily_basic = raw.get("daily_basic")
    if daily_basic is None or (isinstance(daily_basic, pd.DataFrame) and daily_basic.empty):
        missing_fields.append("daily_basic")
        daily_basic = None

    capital_flow = raw.get("capital_flow_raw")
    if capital_flow is None or (isinstance(capital_flow, pd.DataFrame) and capital_flow.empty):
        missing_fields.append("capital_flow_raw")
        capital_flow = None

    margin = raw.get("margin_raw")
    if margin is None or (isinstance(margin, pd.DataFrame) and margin.empty):
        missing_fields.append("margin_raw")
        margin = None

    quality_level = "complete"
    if missing_fields:
        quality_level = "partial"

    result = CleanedDataPacket(
        metadata={**metadata, "quality_level": quality_level},
        is_suspended=False,
        price_series=price_series,
        daily_basic=daily_basic if isinstance(daily_basic, pd.DataFrame) else None,
        capital_flow_raw=capital_flow if isinstance(capital_flow, pd.DataFrame) else None,
        margin_raw=margin if isinstance(margin, pd.DataFrame) else None,
        dragon_tiger_raw=raw.get("dragon_tiger_raw"),
        sector_raw=raw.get("sector_raw"),
        news_raw=raw.get("news_raw"),
        financial_raw=raw.get("financial_raw"),
        market_sentiment_raw=raw.get("market_sentiment_raw"),
        shareholder_raw=raw.get("shareholder_raw"),
        dividend_raw=raw.get("dividend_raw", []),
        anomalies=anomalies,
        missing_fields=missing_fields,
        available_tools=available_tools,
        macro_data=raw.get("macro_data", {}),
    )
    logger.info(f"[清洗] {stock_code} 数据清洗完成（停牌={result.is_suspended}，可用工具={len(result.available_tools)}个）")
    return result


def _process_price_series(price_df: Any) -> Optional[pd.DataFrame]:
    """前复权处理：确保 open_adj/high_adj/low_adj/close_adj 字段存在。"""
    if price_df is None:
        return None
    if not isinstance(price_df, pd.DataFrame) or price_df.empty:
        return None

    df = price_df.copy()

    # 如果已有 *_adj 字段，直接使用；否则使用原始价格并标记
    for col in ["open", "high", "low", "close"]:
        adj_col = f"{col}_adj"
        if adj_col not in df.columns:
            if col in df.columns:
                df[adj_col] = df[col]
            else:
                df[adj_col] = np.nan

    # 确保必要列存在
    for col in ["vol", "amount", "pct_chg"]:
        if col not in df.columns:
            df[col] = np.nan

    # 按日期升序排列
    if "trade_date" in df.columns:
        df = df.sort_values("trade_date").reset_index(drop=True)

    return df


def _detect_anomalies(df: pd.DataFrame, stock_code: str) -> list[dict]:
    """检测涨跌停、量价异常等事件并打标签。"""
    anomalies = []
    code_upper = stock_code.upper()

    # 确定涨跌停阈值
    if "SZ300" in code_upper or "SZ688" in code_upper or code_upper.startswith("688"):
        limit_pct = 19.5  # 创业板/科创板 ±20%
    elif "ST" in code_upper:
        limit_pct = 4.5   # ST 股 ±5%
    else:
        limit_pct = 9.5   # 主板 ±10%

    if "pct_chg" not in df.columns or "close_adj" not in df.columns:
        return anomalies

    # 计算20日平均成交量
    vol_col = "vol" if "vol" in df.columns else None

    for i, row in df.iterrows():
        date = str(row.get("trade_date", i))
        pct = row.get("pct_chg", 0) or 0

        # 涨停检测
        if pct >= limit_pct:
            tag = "one_word_limit_up" if (
                row.get("open_adj") == row.get("high_adj") == row.get("low_adj") == row.get("close_adj")
            ) else "limit_up"
            anomalies.append({"date": date, "type": tag, "pct_chg": pct})

        # 跌停检测
        elif pct <= -limit_pct:
            tag = "one_word_limit_down" if (
                row.get("open_adj") == row.get("high_adj") == row.get("low_adj") == row.get("close_adj")
            ) else "limit_down"
            anomalies.append({"date": date, "type": tag, "pct_chg": pct})

    # 量价异常：成交量超过20日均量的5倍
    if vol_col and len(df) >= 20:
        df["vol_ma20"] = df[vol_col].rolling(20).mean()
        mask = df[vol_col] > df["vol_ma20"] * 5
        for i, row in df[mask].iterrows():
            date = str(row.get("trade_date", i))
            anomalies.append({"date": date, "type": "volume_surge",
                               "vol_ratio": round(row[vol_col] / row["vol_ma20"], 1)})

    # 跳空缺口：开盘价与前收盘价差距超过3%
    if "open_adj" in df.columns and len(df) >= 2:
        for i in range(1, len(df)):
            prev_close = df.iloc[i - 1]["close_adj"]
            curr_open = df.iloc[i]["open_adj"]
            if prev_close and prev_close != 0:
                gap_pct = abs(curr_open - prev_close) / prev_close * 100
                if gap_pct > 3:
                    date = str(df.iloc[i].get("trade_date", i))
                    anomalies.append({"date": date, "type": "gap_open",
                                      "gap_pct": round(gap_pct, 2)})

    return anomalies[-60:]  # 只保留最近60条异常记录
