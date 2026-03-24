"""
宏观数据适配器。

提供宏观经济数据的拉取和缓存功能。
数据来源于 AKShare（免费）和 Tushare Pro（需积分）。

覆盖维度：
- 中国宏观：GDP、CPI、PPI、PMI、M2、LPR、社融
- 全球宏观：美联储利率、美元指数、汇率、油价（需外部API）
- 市场宏观：北向资金趋势、融资余额趋势、全市场估值
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


def _to_date_str(dt_str: str) -> str:
    """统一日期格式为 YYYY-MM-DD"""
    if len(dt_str) == 8:
        return f"{dt_str[:4]}-{dt_str[4:6]}-{dt_str[6:]}"
    return dt_str


async def fetch_macro_data(stock_code: Optional[str] = None) -> tuple[dict, set[str]]:
    """
    拉取宏观经济数据。

    参数：
        stock_code: 可选，用于判断市场类型（A股/港股/美股）

    返回：
        (raw_data_dict, available_tools_set)
    """
    raw: dict = {
        "metadata": {
            "source": "akshare",
            "date": _today(),
            "stock_code": stock_code or "N/A",
        }
    }
    available: set[str] = set()

    logger.info(f"[Macro] 开始拉取宏观数据（标的: {stock_code or 'N/A'}）")

    # 1. 中国宏观经济指标
    try:
        china_macro = await _fetch_china_macro()
        if china_macro:
            raw["china_macro"] = china_macro
            available.add("macro_china_tool")
            logger.info("[Macro] 中国宏观数据获取成功")
    except Exception as e:
        logger.warning(f"[Macro] 中国宏观数据获取失败: {e}")
        raw["china_macro"] = None

    # 2. 利率与货币政策
    try:
        interest_rates = await _fetch_interest_rates()
        if interest_rates:
            raw["interest_rates"] = interest_rates
            available.add("macro_interest_tool")
            logger.info("[Macro] 利率数据获取成功")
    except Exception as e:
        logger.warning(f"[Macro] 利率数据获取失败: {e}")
        raw["interest_rates"] = None

    # 3. 汇率数据
    try:
        fx_data = await _fetch_fx_data()
        if fx_data:
            raw["fx_data"] = fx_data
            available.add("macro_fx_tool")
            logger.info("[Macro] 汇率数据获取成功")
    except Exception as e:
        logger.warning(f"[Macro] 汇率数据获取失败: {e}")
        raw["fx_data"] = None

    # 4. 全球宏观（油价、美联储等）- 仅记录数据，macro_global_tool 已删除
    try:
        global_macro = await _fetch_global_macro()
        if global_macro:
            raw["global_macro"] = global_macro
            # macro_global_tool 已删除，不加入 available（纯占位符，需外部API）
            logger.info("[Macro] 全球宏观数据获取成功（仅记录，工具已删除）")
    except Exception as e:
        logger.warning(f"[Macro] 全球宏观数据获取失败: {e}")
        raw["global_macro"] = None

    # 5. 市场宏观指标（基于个股代码判断市场）
    if stock_code:
        try:
            market_macro = await _fetch_market_macro(stock_code)
            if market_macro:
                raw["market_macro"] = market_macro
                available.add("macro_market_tool")
                logger.info("[Macro] 市场宏观数据获取成功")
        except Exception as e:
            logger.warning(f"[Macro] 市场宏观数据获取失败: {e}")
            raw["market_macro"] = None

    raw["metadata"]["available_count"] = len(available)
    logger.info(f"[Macro] 宏观数据拉取完成，可用工具: {available}")

    return raw, available


async def _fetch_china_macro() -> Optional[dict]:
    """获取中国宏观经济数据（GDP、CPI、PPI、PMI、M2、社融）"""
    import akshare as ak

    data = {}

    # CPI（消费者价格指数）
    try:
        df_cpi = ak.macro_china_cpi()
        if df_cpi is not None and not df_cpi.empty:
            # 取最近12个月数据
            recent = df_cpi.tail(12)
            latest = recent.iloc[-1]
            prev = recent.iloc[-2] if len(recent) > 1 else None
            data["cpi"] = {
                "latest": float(latest.get("全国-当月", 0)) if "全国-当月" in latest else float(latest.get("今值", 0)),
                "previous": float(prev.get("全国-当月", 0)) if prev is not None and "全国-当月" in prev else None,
                "yoy_change": float(latest.get("全国-同比增长", 0)) if "全国-同比增长" in latest else None,
                "trend": recent.to_dict("records")[-6:] if len(recent) >= 6 else recent.to_dict("records"),
            }
    except Exception as e:
        logger.debug(f"[Macro] CPI获取失败: {e}")

    # PPI（生产者价格指数）
    try:
        df_ppi = ak.macro_china_ppi()
        if df_ppi is not None and not df_ppi.empty:
            recent = df_ppi.tail(12)
            latest = recent.iloc[-1]
            prev = recent.iloc[-2] if len(recent) > 1 else None
            data["ppi"] = {
                "latest": float(latest.get("当月", 0)) if "当月" in latest else float(latest.get("今值", 0)),
                "previous": float(prev.get("当月", 0)) if prev is not None and "当月" in prev else None,
                "yoy_change": float(latest.get("当月同比增长", 0)) if "当月同比增长" in latest else None,
                "trend": recent.to_dict("records")[-6:] if len(recent) >= 6 else recent.to_dict("records"),
            }
    except Exception as e:
        logger.debug(f"[Macro] PPI获取失败: {e}")

    # PMI（采购经理指数）
    try:
        df_pmi = ak.macro_china_pmi()
        if df_pmi is not None and not df_pmi.empty:
            recent = df_pmi.tail(12)
            latest = recent.iloc[-1]
            # PMI 列名通常是 '制造业-指数' 或类似
            pmi_col = [c for c in latest.index if '制造业' in str(c) or 'PMI' in str(c) or '指数' in str(c)]
            pmi_value = float(latest[pmi_col[0]]) if pmi_col else float(latest.get('今值', 0))
            data["pmi"] = {
                "latest": pmi_value,
                "previous": float(recent.iloc[-2][pmi_col[0]]) if len(recent) > 1 and pmi_col else None,
                "trend": recent.to_dict("records")[-6:] if len(recent) >= 6 else recent.to_dict("records"),
                "interpretation": "荣枯线50，>50扩张，<50收缩",
            }
    except Exception as e:
        logger.debug(f"[Macro] PMI获取失败: {e}")

    # GDP
    try:
        df_gdp = ak.macro_china_gdp()
        if df_gdp is not None and not df_gdp.empty:
            recent = df_gdp.tail(8)  # 最近8个季度
            latest = recent.iloc[-1]
            data["gdp"] = {
                "latest_quarter": str(latest.get("季度", "")) if "季度" in latest else str(latest.get("时间", "")),
                "latest_yoy": float(latest.get("国内生产总值-同比增长", 0)) if "国内生产总值-同比增长" in latest else None,
                "quarterly_trend": recent.to_dict("records")[-4:] if len(recent) >= 4 else recent.to_dict("records"),
            }
    except Exception as e:
        logger.debug(f"[Macro] GDP获取失败: {e}")

    # M2货币供应量
    try:
        df_m2 = ak.macro_china_m2_yearly()
        if df_m2 is not None and not df_m2.empty:
            recent = df_m2.tail(12)
            data["m2"] = {
                "latest": float(recent.iloc[-1].get("今值", 0)) if len(recent) > 0 else None,
                "previous": float(recent.iloc[-2].get("今值", 0)) if len(recent) > 1 else None,
                "yoy_change": float(recent.iloc[-1].get("涨跌幅", 0)) if len(recent) > 0 else None,
                "trend": recent.to_dict("records")[-6:] if len(recent) >= 6 else recent.to_dict("records"),
            }
    except Exception as e:
        logger.debug(f"[Macro] M2获取失败: {e}")

    # 社会融资规模
    try:
        df_shrz = ak.macro_china_shrzgm()
        if df_shrz is not None and not df_shrz.empty:
            recent = df_shrz.tail(12)
            data["social_finance"] = {
                "latest": float(recent.iloc[-1].get("今值", 0)) if len(recent) > 0 else None,
                "unit": "亿元",
                "yoy_change": float(recent.iloc[-1].get("涨跌幅", 0)) if len(recent) > 0 else None,
                "trend": recent.to_dict("records")[-6:] if len(recent) >= 6 else recent.to_dict("records"),
            }
    except Exception as e:
        logger.debug(f"[Macro] 社融数据获取失败: {e}")

    return data if data else None


async def _fetch_interest_rates() -> Optional[dict]:
    """获取利率数据（LPR、美联储利率）"""
    import akshare as ak

    data = {}

    # 中国LPR
    try:
        df_lpr = ak.macro_china_lpr()
        if df_lpr is not None and not df_lpr.empty:
            recent = df_lpr.tail(12)
            latest = recent.iloc[-1] if len(recent) > 0 else None
            # 列名: TRADE_DATE, LPR1Y, LPR5Y, RATE_1, RATE_2
            data["lpr"] = {
                "1y": float(latest.get("LPR1Y", 0)) if latest is not None else None,
                "5y": float(latest.get("LPR5Y", 0)) if latest is not None else None,
                "date": str(latest.get("TRADE_DATE", "")) if latest is not None else None,
                "trend": recent.to_dict("records")[-6:] if len(recent) >= 6 else recent.to_dict("records"),
            }
    except Exception as e:
        logger.debug(f"[Macro] LPR获取失败: {e}")

    # 美联储利率
    try:
        df_fed = ak.macro_bank_usa_interest_rate()
        if df_fed is not None and not df_fed.empty:
            # 过滤掉 NaN 的记录
            df_valid = df_fed.dropna(subset=["今值"])
            if not df_valid.empty:
                recent = df_valid.tail(12)
                latest = recent.iloc[-1]
                data["fed_rate"] = {
                    "latest": float(latest.get("今值", 0)),
                    "previous": float(recent.iloc[-2].get("今值", 0)) if len(recent) > 1 else None,
                    "date": str(latest.get("日期", "")),
                    "trend": recent.to_dict("records")[-6:] if len(recent) >= 6 else recent.to_dict("records"),
                }
    except Exception as e:
        logger.debug(f"[Macro] 美联储利率获取失败: {e}")

    # SHIBOR（上海银行间同业拆放利率）
    try:
        df_shibor = ak.macro_china_shibor_all()
        if df_shibor is not None and not df_shibor.empty:
            recent = df_shibor.tail(10)
            latest = recent.iloc[-1] if len(recent) > 0 else None
            # 列名: 日期, O/N-定价, 1W-定价, 1M-定价, ...
            data["shibor"] = {
                "overnight": float(latest.get("O/N-定价", 0)) if latest is not None else None,
                "1w": float(latest.get("1W-定价", 0)) if latest is not None else None,
                "1m": float(latest.get("1M-定价", 0)) if latest is not None else None,
                "date": str(latest.get("日期", "")) if latest is not None else None,
            }
    except Exception as e:
        logger.debug(f"[Macro] SHIBOR获取失败: {e}")

    return data if data else None


async def _fetch_fx_data() -> Optional[dict]:
    """获取汇率数据"""
    import akshare as ak

    data = {}

    # 美元兑人民币（使用外汇牌价）
    try:
        df_fx = ak.currency_boc_sina()
        if df_fx is not None and not df_fx.empty:
            # 获取最新日期的美元汇率
            latest = df_fx.iloc[-1]
            prev = df_fx.iloc[-2] if len(df_fx) > 1 else None
            data["usd_cny"] = {
                "latest": float(latest.get("中行折算价", 0)),
                "buy": float(latest.get("中行汇买价", 0)),
                "sell": float(latest.get("中行钞卖价/汇卖价", 0)),
                "date": str(latest.get("日期", "")),
                "change": (
                    float(latest.get("中行折算价", 0)) - float(prev.get("中行折算价", 0))
                    if prev is not None else 0
                ),
                "note": "数据来自中国银行外汇牌价",
            }
    except Exception as e:
        logger.debug(f"[Macro] USD/CNY获取失败: {e}")

    # 美元指数（通过汇率间接获取，AKShare无直接接口）
    try:
        # 尝试通过currency_boc_safe获取欧元、日元，计算美元指数近似值
        df_eur = ak.currency_boc_safe("欧元")
        df_jpy = ak.currency_boc_safe("日元")
        # 简化处理：仅记录汇率变化趋势
        data["dxy_hint"] = {
            "note": "美元指数需通过外部API获取，此处提供主要货币对参考",
            "eur_usd_trend": "需外部数据",
            "usd_jpy_trend": "需外部数据",
        }
    except Exception as e:
        logger.debug(f"[Macro] 美元指数计算失败: {e}")

    return data if data else None


async def _fetch_global_macro() -> Optional[dict]:
    """获取全球宏观数据（油价、美债收益率等）"""
    data = {}

    # 油价数据（AKShare无直接接口，提供提示）
    data["oil_price"] = {
        "note": "布伦特原油/WTI价格需通过EIA API或Alpha Vantage获取",
        "brent": None,
        "wti": None,
        "trend": "需外部数据源",
    }

    # 美债收益率（AKShare无直接接口）
    data["us_bond_yield"] = {
        "note": "10年期美债收益率需通过外部API获取",
        "yield_10y": None,
        "yield_2y": None,
        "spread": None,  # 期限利差
    }

    # VIX恐慌指数（AKShare无直接接口）
    data["vix"] = {
        "note": "VIX指数需通过外部API获取",
        "latest": None,
        "level": "需外部数据源",  # <20低波动，20-30中等，>30高波动
    }

    return data


async def _fetch_market_macro(stock_code: str) -> Optional[dict]:
    """获取市场层面的宏观指标（北向资金、融资余额趋势等）"""
    import akshare as ak

    code_upper = stock_code.upper()
    is_a_share = code_upper.endswith((".SZ", ".SH"))

    data = {}

    if is_a_share:
        # A股市场宏观指标

        # 1. 全市场北向资金（沪深港通）
        try:
            df_hsgt = ak.stock_hsgt_hist_em()
            if df_hsgt is not None and not df_hsgt.empty:
                # 过滤掉 NaN 的记录，获取最新有效数据
                df_valid = df_hsgt.dropna(subset=["当日成交净买额"])
                if not df_valid.empty:
                    recent = df_valid.tail(20)
                    latest = recent.iloc[-1]

                    # 计算近5日、近20日累计净流入
                    net_inflow_5d = recent.tail(5)["当日成交净买额"].sum() if len(recent) >= 5 else 0
                    net_inflow_20d = recent["当日成交净买额"].sum() if len(recent) >= 20 else 0

                    data["northbound"] = {
                        "latest_daily": float(latest.get("当日成交净买额", 0)),
                        "unit": "亿元",
                        "net_inflow_5d": float(net_inflow_5d),
                        "net_inflow_20d": float(net_inflow_20d),
                        "trend": "流入" if net_inflow_5d > 0 else "流出",
                        "latest_date": str(latest.get("日期", "")),
                        "recent": recent.to_dict("records")[-10:] if len(recent) >= 10 else recent.to_dict("records"),
                    }
        except Exception as e:
            logger.debug(f"[Macro] 北向资金获取失败: {e}")

        # 2. 全市场融资余额趋势
        try:
            df_margin = ak.stock_margin_szse() if code_upper.endswith(".SZ") else ak.stock_margin_sse()
            if df_margin is not None and not df_margin.empty:
                recent = df_margin.tail(20)
                latest = recent.iloc[-1] if len(recent) > 0 else None
                prev = recent.iloc[-2] if len(recent) > 1 else None

                data["market_margin"] = {
                    "latest_balance": float(latest.get("融资余额", 0)) if latest is not None else 0,
                    "unit": "亿元",
                    "change_1d": (
                        float(latest.get("融资余额", 0)) - float(prev.get("融资余额", 0))
                        if latest is not None and prev is not None else 0
                    ),
                    "trend_20d": "上升" if len(recent) >= 20 and recent.tail(20)["融资余额"].iloc[-1] > recent.tail(20)["融资余额"].iloc[0] else "下降",
                }
        except Exception as e:
            logger.debug(f"[Macro] 市场融资余额获取失败: {e}")

        # 3. 全市场估值水平（使用中证指数估值）
        try:
            # 使用 stock_zh_index_value_csindex 获取沪深300估值
            df_index_pe = ak.stock_zh_index_value_csindex(symbol="000300")
            if df_index_pe is not None and not df_index_pe.empty:
                latest = df_index_pe.iloc[-1] if len(df_index_pe) > 0 else None
                data["market_valuation"] = {
                    "hs300_pe": float(latest.get("市盈率", 0)) if latest is not None else None,
                    "hs300_pb": float(latest.get("市净率", 0)) if latest is not None else None,
                    "dividend_yield": float(latest.get("股息率", 0)) if latest is not None else None,
                    "date": str(latest.get("日期", "")) if latest is not None else None,
                    "source": "中证指数",
                }
        except Exception as e:
            logger.debug(f"[Macro] 市场估值获取失败: {e}")

    else:
        # 港股/美股市场宏观指标（简化处理）
        data["note"] = "港股/美股市场宏观指标需通过专门接口获取"

    return data if data else None
