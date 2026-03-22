from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _to_akshare_date(date_str: str) -> str:
    """Convert YYYYMMDD to YYYY-MM-DD for AkShare APIs that require it."""
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"


async def fetch_all(stock_code: str, start_date: Optional[str] = None,
                    end_date: Optional[str] = None) -> tuple[dict, set[str]]:
    """
    AkShare 备用数据适配器。返回 (raw_data_dict, available_tools_set)。
    字段名与 tushare_adapter 保持一致，作为补充/备用数据源。
    仅获取 Tushare 可能缺失的字段（日线行情、基本面、新闻公告）。
    """
    import akshare as ak

    if start_date is None:
        start_date = _days_ago(400)
    if end_date is None:
        end_date = _today()

    logger.info(f"[AkShare] 开始拉取 {stock_code} 数据（{start_date} ~ {end_date}）")
    raw: dict = {"metadata": {"stock_code": stock_code, "source": "akshare", "date": end_date}}
    available: set[str] = set()

    # 解析代码格式（AkShare 使用不同格式）
    code_upper = stock_code.upper()
    is_a_share = code_upper.endswith((".SZ", ".SH"))
    is_hk = code_upper.endswith(".HK")

    # 提取纯代码（去掉后缀）
    pure_code = stock_code.split(".")[0]

    # 1. 行情数据
    try:
        if is_a_share:
            # A股：前复权日线数据
            df = ak.stock_zh_a_hist(
                symbol=pure_code,
                period="daily",
                start_date=_to_akshare_date(start_date),
                end_date=_to_akshare_date(end_date),
                adjust="qfq",  # 前复权
            )
            if df is not None and not df.empty:
                # AkShare 字段映射 → 标准字段
                rename_map = {
                    "日期": "trade_date",
                    "开盘": "open", "最高": "high", "最低": "low", "收盘": "close",
                    "成交量": "vol", "成交额": "amount", "涨跌幅": "pct_chg",
                    "涨跌额": "change",
                }
                df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
                # trade_date 转为 YYYYMMDD
                if "trade_date" in df.columns:
                    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")
                # 前复权数据直接当作 *_adj 字段
                for col in ["open", "high", "low", "close"]:
                    if col in df.columns:
                        df[f"{col}_adj"] = df[col]
                df = df.sort_values("trade_date").reset_index(drop=True)
                raw["price_series"] = df
                available.update(["price_tool", "indicator_tool"])
            else:
                raw["price_series"] = None
        elif is_hk:
            df = ak.stock_hk_daily(symbol=pure_code, adjust="qfq")
            if df is not None and not df.empty:
                rename_map = {
                    "date": "trade_date",
                    "open": "open", "high": "high", "low": "low", "close": "close",
                    "volume": "vol",
                }
                df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
                if "trade_date" in df.columns:
                    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")
                    # 按日期过滤
                    df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)]
                for col in ["open", "high", "low", "close"]:
                    if col in df.columns:
                        df[f"{col}_adj"] = df[col]
                if "close" in df.columns and len(df) > 1:
                    df["pct_chg"] = df["close"].pct_change() * 100
                df = df.sort_values("trade_date").reset_index(drop=True)
                raw["price_series"] = df
                available.update(["price_tool", "indicator_tool"])
            else:
                raw["price_series"] = None
        else:
            # 美股
            df = ak.stock_us_daily(symbol=pure_code, adjust="qfq")
            if df is not None and not df.empty:
                rename_map = {
                    "date": "trade_date",
                    "open": "open", "high": "high", "low": "low", "close": "close",
                    "volume": "vol",
                }
                df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
                if "trade_date" in df.columns:
                    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")
                    df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)]
                for col in ["open", "high", "low", "close"]:
                    if col in df.columns:
                        df[f"{col}_adj"] = df[col]
                if "close" in df.columns and len(df) > 1:
                    df["pct_chg"] = df["close"].pct_change() * 100
                df = df.sort_values("trade_date").reset_index(drop=True)
                raw["price_series"] = df
                available.update(["price_tool", "indicator_tool"])
            else:
                raw["price_series"] = None
    except Exception as e:
        logger.warning(f"[AkShare] 行情数据拉取失败 {stock_code}: {e}")
        raw["price_series"] = None

    # 2. 基本面数据（仅 A 股，AkShare 提供实时指标）
    if is_a_share:
        try:
            df_basic = ak.stock_a_lg_indicator(symbol=pure_code)
            if df_basic is not None and not df_basic.empty:
                rename_map = {
                    "trade_date": "trade_date",
                    "pe": "pe_ttm", "pb": "pb_mrq",
                    "ps": "ps_ttm", "dv_ratio": "dividend_yield",
                    "total_mv": "total_mv", "float_mv": "circ_mv",
                }
                df_basic = df_basic.rename(columns={k: v for k, v in rename_map.items() if k in df_basic.columns})
                if "trade_date" in df_basic.columns:
                    df_basic["trade_date"] = pd.to_datetime(df_basic["trade_date"]).dt.strftime("%Y%m%d")
                    df_basic = df_basic[
                        (df_basic["trade_date"] >= start_date) & (df_basic["trade_date"] <= end_date)
                    ]
                df_basic = df_basic.sort_values("trade_date").reset_index(drop=True)
                raw["daily_basic"] = df_basic
                available.update(["fundamental_tool", "snapshot_tool"])
            else:
                raw["daily_basic"] = None
        except Exception as e:
            logger.warning(f"[AkShare] 基本面数据拉取失败 {stock_code}: {e}")
            raw["daily_basic"] = None
    else:
        raw["daily_basic"] = None

    # 3. 资金流向（仅 A 股）
    if is_a_share:
        try:
            df_flow = ak.stock_individual_fund_flow(stock=pure_code, market="sh" if code_upper.endswith(".SH") else "sz")
            if df_flow is not None and not df_flow.empty:
                rename_map = {
                    "日期": "trade_date",
                    "主力净流入-净额": "main_net_today",
                    "超大单净流入-净额": "main_buy",
                    "小单净流入-净额": "retail_net_today",
                }
                df_flow = df_flow.rename(columns={k: v for k, v in rename_map.items() if k in df_flow.columns})
                if "trade_date" in df_flow.columns:
                    df_flow["trade_date"] = pd.to_datetime(df_flow["trade_date"]).dt.strftime("%Y%m%d")
                    df_flow = df_flow[
                        (df_flow["trade_date"] >= start_date) & (df_flow["trade_date"] <= end_date)
                    ]
                df_flow = df_flow.sort_values("trade_date").reset_index(drop=True)
                if "main_net_today" in df_flow.columns:
                    # 万元 → 统一单位（与 Tushare 一致）
                    df_flow["main_net_today"] = df_flow["main_net_today"] / 10000
                    df_flow["main_net_3d"] = df_flow["main_net_today"].rolling(3).sum()
                    df_flow["main_net_5d"] = df_flow["main_net_today"].rolling(5).sum()
                    df_flow["main_net_10d"] = df_flow["main_net_today"].rolling(10).sum()
                raw["capital_flow_raw"] = df_flow
                available.update(["capital_flow_tool"])
            else:
                raw["capital_flow_raw"] = None
        except Exception as e:
            logger.warning(f"[AkShare] 资金流向数据拉取失败 {stock_code}: {e}")
            raw["capital_flow_raw"] = None
    else:
        raw["capital_flow_raw"] = None

    # 4. 融资融券（仅 A 股）
    if is_a_share:
        try:
            df_margin = ak.stock_margin_detail_szse(symbol=pure_code) if code_upper.endswith(".SZ") else \
                        ak.stock_margin_detail_sse(symbol=pure_code)
            if df_margin is not None and not df_margin.empty:
                rename_map = {
                    "交易日期": "trade_date",
                    "融资余额": "margin_balance",
                    "融券余量": "short_balance",
                }
                df_margin = df_margin.rename(columns={k: v for k, v in rename_map.items() if k in df_margin.columns})
                if "trade_date" in df_margin.columns:
                    df_margin["trade_date"] = pd.to_datetime(df_margin["trade_date"]).dt.strftime("%Y%m%d")
                    df_margin = df_margin[
                        (df_margin["trade_date"] >= start_date) & (df_margin["trade_date"] <= end_date)
                    ]
                df_margin = df_margin.sort_values("trade_date").reset_index(drop=True)
                if "margin_balance" in df_margin.columns:
                    df_margin["margin_change_5d"] = df_margin["margin_balance"].pct_change(5) * 100
                raw["margin_raw"] = df_margin
                available.update(["margin_tool"])
            else:
                raw["margin_raw"] = None
        except Exception as e:
            logger.warning(f"[AkShare] 融资融券数据拉取失败 {stock_code}: {e}")
            raw["margin_raw"] = None
    else:
        raw["margin_raw"] = None

    # 5. 龙虎榜（仅 A 股）
    raw["dragon_tiger_raw"] = None

    # 6. 公告/新闻（仅 A 股）
    if is_a_share:
        try:
            df_news = ak.stock_notice_report(symbol=pure_code)
            if df_news is not None and not df_news.empty:
                rename_map = {
                    "公告日期": "ann_date",
                    "公告标题": "title",
                }
                df_news = df_news.rename(columns={k: v for k, v in rename_map.items() if k in df_news.columns})
                if "ann_date" in df_news.columns:
                    cutoff = _days_ago(60)
                    df_news["ann_date"] = pd.to_datetime(df_news["ann_date"]).dt.strftime("%Y%m%d")
                    df_news = df_news[df_news["ann_date"] >= cutoff]
                raw["news_raw"] = df_news[["ann_date", "title"]].head(50).to_dict("records") if not df_news.empty else []
                if raw["news_raw"]:
                    available.update(["news_tool"])
            else:
                raw["news_raw"] = []
        except Exception as e:
            logger.warning(f"[AkShare] 公告新闻数据拉取失败 {stock_code}: {e}")
            raw["news_raw"] = []
    else:
        raw["news_raw"] = []

    # 7. 板块/概念分类（仅 A 股）
    if is_a_share:
        try:
            df_concept = ak.stock_concept_name_em()
            # AkShare 概念板块需要匹配
            raw["sector_raw"] = {"concepts": []}
            available.update(["sector_tool"])
        except Exception as e:
            logger.warning(f"[AkShare] 板块概念数据拉取失败 {stock_code}: {e}")
            raw["sector_raw"] = {"concepts": []}
    else:
        raw["sector_raw"] = {"concepts": []}

    # 8. 停牌信息（默认未停牌，AkShare 停牌查询有限）
    raw["suspend_info"] = {"is_suspended": False}

    # 9. 市场情绪（全市场涨跌停统计，仅 A 股）
    if is_a_share:
        try:
            df_limit = ak.stock_limit_up_down_em()
            if df_limit is not None and not df_limit.empty:
                today = _today()
                up_count = int((df_limit["涨跌"] == "涨停").sum()) if "涨跌" in df_limit.columns else 0
                down_count = int((df_limit["涨跌"] == "跌停").sum()) if "涨跌" in df_limit.columns else 0
                raw["market_sentiment_raw"] = pd.DataFrame([{
                    "trade_date": today,
                    "limit_up_count": up_count,
                    "limit_down_count": down_count,
                }])
                available.update(["sentiment_tool"])
            else:
                raw["market_sentiment_raw"] = None
        except Exception as e:
            logger.warning(f"[AkShare] 市场情绪数据拉取失败: {e}")
            raw["market_sentiment_raw"] = None
    else:
        raw["market_sentiment_raw"] = None

    # 10. 财务数据（仅 A 股）
    if is_a_share:
        try:
            df_income = ak.stock_financial_report_sina(stock=pure_code, symbol="利润表")
            financial: dict = {}
            if df_income is not None and not df_income.empty:
                financial["income"] = df_income.head(4).to_dict("records")
            raw["financial_raw"] = financial if financial else None
        except Exception as e:
            logger.warning(f"[AkShare] 财务数据拉取失败 {stock_code}: {e}")
            raw["financial_raw"] = None
    else:
        raw["financial_raw"] = None

    raw["metadata"]["available_count"] = len(available)
    logger.info(f"[AkShare] {stock_code} 数据拉取完成，可用工具: {available}")
    return raw, available


async def merge_with_tushare(tushare_raw: dict, tushare_available: set[str],
                             stock_code: str, start_date: Optional[str] = None,
                             end_date: Optional[str] = None) -> tuple[dict, set[str]]:
    """
    以 Tushare 数据为主，用 AkShare 补充缺失字段。
    仅对 Tushare 中为 None 的字段尝试从 AkShare 获取。
    """
    # 若 Tushare 已有足够数据，直接返回
    missing_keys = [k for k in ["price_series", "daily_basic"] if tushare_raw.get(k) is None]
    if not missing_keys:
        return tushare_raw, tushare_available

    try:
        ak_raw, ak_available = await fetch_all(stock_code, start_date, end_date)
    except Exception as e:
        logger.warning(f"[AkShare] 备用数据源拉取失败 {stock_code}: {e}")
        return tushare_raw, tushare_available

    merged_raw = dict(tushare_raw)
    merged_available = set(tushare_available)

    for key in missing_keys:
        if ak_raw.get(key) is not None:
            merged_raw[key] = ak_raw[key]
            logger.info(f"[AkShare] 补充缺失字段 '{key}' for {stock_code}")

    # 同步 available_tools
    for key, tool_name in [("price_series", "price_tool"), ("price_series", "indicator_tool"),
                            ("daily_basic", "fundamental_tool"), ("daily_basic", "snapshot_tool")]:
        if merged_raw.get(key) is not None:
            merged_available.add(tool_name)

    return merged_raw, merged_available
