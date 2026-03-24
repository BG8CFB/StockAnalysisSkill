"""
港股数据适配器（基于 AkShare）。

免费数据源，支持港股实时行情和基本面数据。
数据来源：东方财富、新浪财经

特点：
- 免费使用
- 覆盖港股主板、创业板
- 包含港股通标的
- 数据延迟约 15 分钟

安装依赖：pip install akshare
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from src.data.market_router import DataAdapter, MarketType, normalize_code

logger = logging.getLogger(__name__)


def _days_ago(n: int) -> str:
    """返回 n 天前的日期（YYYYMMDD）。"""
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


def _today() -> str:
    """返回今天日期（YYYYMMDD）。"""
    return datetime.now().strftime("%Y%m%d")


def _to_akshare_date(date_str: str) -> str:
    """YYYYMMDD -> YYYY-MM-DD（AkShare 格式）。"""
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"


class HKStockAdapter(DataAdapter):
    """
    港股数据适配器（AkShare）。

    支持功能：
    - 历史 OHLCV 数据（前复权）
    - 港股基本面数据（PE、PB、市值）
    - 港股通资金流向
    - 港股新闻/公告

    不支持功能：
    - 融资融券（港股机制不同）
    - 龙虎榜（无此概念）
    - 涨跌停（港股无涨跌停限制）
    """

    def __init__(self):
        self._enabled = True
        self._ak = None

    @property
    def name(self) -> str:
        return "akshare_hk"

    @property
    def supported_markets(self) -> list[MarketType]:
        return [MarketType.HK_STOCK]

    def _get_ak(self):
        """延迟导入 akshare。"""
        if self._ak is None:
            try:
                import akshare as ak
                self._ak = ak
            except ImportError:
                logger.error("akshare 未安装，请运行: pip install akshare")
                raise
        return self._ak

    def is_available(self) -> bool:
        """检查 akshare 是否可用。"""
        try:
            self._get_ak()
            return self._enabled
        except ImportError:
            return False

    async def fetch_all(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> tuple[dict, set[str]]:
        """
        获取港股完整数据。

        Args:
            stock_code: 港股代码，如 "0700.HK", "9988.HK"
            start_date: 开始日期 YYYYMMDD，默认 400 天前
            end_date: 结束日期 YYYYMMDD，默认今天

        Returns:
            (raw_data_dict, available_tools_set)
        """
        ak = self._get_ak()

        if start_date is None:
            start_date = _days_ago(400)
        if end_date is None:
            end_date = _today()

        identity = normalize_code(stock_code)
        pure_code = identity.pure_code  # 纯数字代码，如 "0700"

        logger.info(f"[HKStock] 开始拉取 {stock_code} 数据（{start_date} ~ {end_date}）")

        raw: dict = {
            "metadata": {
                "stock_code": stock_code,
                "normalized_code": pure_code,
                "market": "hk_stock",
                "source": "akshare",
                "start_date": start_date,
                "end_date": end_date,
            }
        }
        available: set[str] = set()

        # 1. 历史行情数据（使用东方财富接口，更稳定）
        try:
            # 使用 stock_hk_hist 获取港股历史数据（东财数据源）
            # 注意：港股代码需要补零到5位，如 0700 -> 00700
            formatted_code = pure_code.zfill(5)
            df = ak.stock_hk_hist(symbol=formatted_code, period="daily", adjust="qfq")
            if df is not None and not df.empty:
                df = self._normalize_price_data(df, start_date, end_date)
                raw["price_series"] = df
                available.update(["market_data_tool"])
                logger.info(f"[HKStock] 获取 {len(df)} 条历史数据")
            else:
                logger.warning(f"[HKStock] {stock_code} 无历史数据")
                raw["price_series"] = None
        except Exception as e:
            logger.warning(f"[HKStock] 历史数据获取失败 {stock_code}: {e}")
            raw["price_series"] = None

        # 2. 港股基本面数据（尝试多种方式）
        try:
            daily_basic = await self._fetch_hk_fundamental(ak, pure_code, raw.get("price_series"))
            if daily_basic is not None and not daily_basic.empty:
                raw["daily_basic"] = daily_basic
                available.update(["fundamental_tool"])
                logger.info(f"[HKStock] 基本面数据已获取")
            else:
                raw["daily_basic"] = None
        except Exception as e:
            logger.warning(f"[HKStock] 基本面数据获取失败 {stock_code}: {e}")
            raw["daily_basic"] = None

        # 3. 港股通持股数据（特有数据）
        try:
            hkstock_hold = ak.stock_hk_ggt_components_em()
            if hkstock_hold is not None and not hkstock_hold.empty:
                # 检查是否在港股通标的
                stock_in_ggt = hkstock_hold[hkstock_hold["代码"] == pure_code]
                if not stock_in_ggt.empty:
                    raw["hk_ggt_info"] = stock_in_ggt.to_dict("records")[0]
                    logger.info(f"[HKStock] {stock_code} 是港股通标的")
                else:
                    raw["hk_ggt_info"] = None
            else:
                raw["hk_ggt_info"] = None
        except Exception as e:
            logger.debug(f"[HKStock] 港股通数据获取失败: {e}")
            raw["hk_ggt_info"] = None

        # 4. 尝试获取港股新闻
        try:
            news = ak.stock_hk_news_main(symbol=pure_code)
            if news is not None and not news.empty:
                raw["news_raw"] = news.head(20).to_dict("records")
                available.update(["news_tool"])
            else:
                raw["news_raw"] = None
        except Exception as e:
            logger.debug(f"[HKStock] 新闻数据获取失败: {e}")
            raw["news_raw"] = None

        # 港股不支持的功能（与 A 股差异）
        raw["capital_flow_raw"] = None
        raw["margin_raw"] = None
        raw["dragon_tiger_raw"] = None
        raw["shareholder_raw"] = None
        raw["market_sentiment_raw"] = None

        return raw, available

    def _normalize_price_data(
        self,
        df: pd.DataFrame,
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """
        标准化港股价格数据。

        处理 stock_hk_hist 返回的中文列名，转换为标准格式。
        """
        # 中文列名映射（东财数据）
        rename_map = {
            "日期": "trade_date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "vol",
            "成交额": "amount",
            "振幅": "amplitude",
            "涨跌幅": "pct_chg",
            "涨跌额": "change_amount",
            "换手率": "turnover_rate",
        }

        # 尝试中文列名映射
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        # 如果没有匹配到中文列名，尝试英文列名（备用）
        if "trade_date" not in df.columns and "date" in df.columns:
            df = df.rename(columns={"date": "trade_date"})

        # 处理日期列
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")
            df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)]

        # 设置复权价格（stock_hk_hist 已处理复权）
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[f"{col}_adj"] = df[col]

        # 确保 pct_chg 存在（如果原始数据没有，则计算）
        if "pct_chg" not in df.columns and "close" in df.columns and len(df) > 1:
            df["pct_chg"] = df["close"].pct_change() * 100
            df["pct_chg"] = df["pct_chg"].fillna(0)
        elif "pct_chg" not in df.columns:
            df["pct_chg"] = 0

        # 添加市场标识
        df["market"] = "hk_stock"

        return df.sort_values("trade_date").reset_index(drop=True)

    async def _fetch_hk_fundamental(
        self,
        ak,
        pure_code: str,
        price_df: Optional[pd.DataFrame] = None
    ) -> Optional[pd.DataFrame]:
        """
        获取港股基本面数据。

        尝试多个数据源：
        1. 港股实时行情（获取当前估值）
        2. 港股个股资讯（获取基本面）
        """
        try:
            # 尝试获取港股实时行情（包含估值数据）
            hk_spot = ak.stock_hk_spot_em()
            if hk_spot is not None and not hk_spot.empty:
                stock_spot = hk_spot[hk_spot["代码"] == pure_code]
                if not stock_spot.empty:
                    return self._extract_from_spot(stock_spot, price_df)
        except Exception as e:
            logger.debug(f"港股实时行情获取失败: {e}")

        # 备选：从价格数据估算
        if price_df is not None and not price_df.empty:
            return self._create_basic_from_price(price_df)

        return None

    def _extract_from_spot(
        self,
        spot_df: pd.DataFrame,
        price_df: Optional[pd.DataFrame] = None
    ) -> pd.DataFrame:
        """从实时行情提取基本面数据。"""
        row = spot_df.iloc[0]

        # 获取日期
        if price_df is not None and not price_df.empty:
            trade_date = price_df["trade_date"].iloc[-1]
        else:
            trade_date = _today()

        data = {
            "trade_date": [trade_date],
            "pe_ttm": self._safe_float(row.get("市盈率")),
            "pb_mrq": self._safe_float(row.get("市净率")),
            "ps_ttm": None,  # 港股通常不直接提供 PS
            "dividend_yield": self._safe_float(row.get("股息率")),
            "total_mv": self._safe_float(row.get("总市值")) / 1e8 if row.get("总市值") else None,  # 转换为亿元
            "circ_mv": self._safe_float(row.get("流通市值")) / 1e8 if row.get("流通市值") else None,
            "turnover_rate": self._safe_float(row.get("换手率")),
        }

        return pd.DataFrame(data)

    def _create_basic_from_price(self, price_df: pd.DataFrame) -> pd.DataFrame:
        """从价格数据创建基础基本面数据（仅包含价格相关字段）。"""
        trade_date = price_df["trade_date"].iloc[-1]

        data = {
            "trade_date": [trade_date],
            "pe_ttm": None,
            "pb_mrq": None,
            "ps_ttm": None,
            "dividend_yield": None,
            "total_mv": None,
            "circ_mv": None,
            "turnover_rate": None,
        }

        return pd.DataFrame(data)

    def _safe_float(self, value) -> Optional[float]:
        """安全转换为 float。"""
        if value is None or pd.isna(value):
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None


def create_hk_adapter() -> Optional[HKStockAdapter]:
    """
    创建港股适配器工厂函数。

    如果 akshare 未安装，返回 None。
    """
    try:
        import akshare as ak  # noqa: F401
        return HKStockAdapter()
    except ImportError:
        logger.warning("akshare 未安装，港股数据源不可用")
        return None
