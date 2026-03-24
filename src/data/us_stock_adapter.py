"""
美股数据适配器（基于 yfinance）。

免费数据源，支持美股历史行情和基本面数据。
数据特点：
- 延迟约 15-20 分钟（非实时）
- 自动处理复权（前复权）
- 数据质量约 95-98%（学术研究数据）
- 无 API Key 要求

安装依赖：pip install yfinance
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from src.data.market_router import DataAdapter, MarketType, normalize_code

logger = logging.getLogger(__name__)


def _yfinance_date(date_str: str) -> datetime:
    """YYYYMMDD -> datetime。"""
    return datetime.strptime(date_str, "%Y%m%d")


def _days_ago(n: int) -> str:
    """返回 n 天前的日期（YYYYMMDD）。"""
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


def _today() -> str:
    """返回今天日期（YYYYMMDD）。"""
    return datetime.now().strftime("%Y%m%d")


class YFinanceAdapter(DataAdapter):
    """
    Yahoo Finance 美股数据适配器。

    支持功能：
    - 历史 OHLCV 数据（自动前复权）
    - 基本面数据（PE、PB、市值等）
    - 财务报表信息

    不支持功能：
    - 融资融券数据（美股机制不同）
    - 龙虎榜（无此概念）
    - 股东人数（美股披露不同）
    """

    def __init__(self):
        self._yf = None
        self._enabled = True

    @property
    def name(self) -> str:
        return "yfinance"

    @property
    def supported_markets(self) -> list[MarketType]:
        return [MarketType.US_STOCK]

    def _get_yf(self):
        """延迟导入 yfinance。"""
        if self._yf is None:
            try:
                import yfinance as yf
                self._yf = yf
            except ImportError:
                logger.error("yfinance 未安装，请运行: pip install yfinance")
                raise
        return self._yf

    def is_available(self) -> bool:
        """yfinance 始终可用（无需配置）。"""
        return self._enabled

    async def fetch_all(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> tuple[dict, set[str]]:
        """
        获取美股完整数据。

        Args:
            stock_code: 美股代码，如 "AAPL", "TSLA"
            start_date: 开始日期 YYYYMMDD，默认 400 天前
            end_date: 结束日期 YYYYMMDD，默认今天

        Returns:
            (raw_data_dict, available_tools_set)
        """
        yf = self._get_yf()

        if start_date is None:
            start_date = _days_ago(400)
        if end_date is None:
            end_date = _today()

        identity = normalize_code(stock_code)
        ticker_symbol = identity.pure_code  # yfinance 使用纯代码

        logger.info(f"[YFinance] 开始拉取 {ticker_symbol} 数据（{start_date} ~ {end_date}）")

        raw: dict = {
            "metadata": {
                "stock_code": stock_code,
                "normalized_code": ticker_symbol,
                "market": "us_stock",
                "source": "yfinance",
                "start_date": start_date,
                "end_date": end_date,
            }
        }
        available: set[str] = set()

        # 创建 Ticker 对象
        ticker = yf.Ticker(ticker_symbol)

        # 1. 获取历史行情数据
        try:
            # yfinance 日期格式：YYYY-MM-DD
            start_fmt = _yfinance_date(start_date).strftime("%Y-%m-%d")
            end_fmt = _yfinance_date(end_date).strftime("%Y-%m-%d")

            # 获取历史数据（自动复权）
            hist = ticker.history(start=start_fmt, end=end_fmt, auto_adjust=True)

            if hist is not None and not hist.empty:
                # 标准化列名以匹配现有系统
                df = self._normalize_price_data(hist, start_date)
                raw["price_series"] = df
                available.update(["market_data_tool"])
                logger.info(f"[YFinance] 获取 {len(df)} 条历史数据")
            else:
                logger.warning(f"[YFinance] {ticker_symbol} 无历史数据")
                raw["price_series"] = None
        except Exception as e:
            logger.warning(f"[YFinance] 历史数据获取失败 {ticker_symbol}: {e}")
            raw["price_series"] = None

        # 2. 获取基本面数据（info）
        try:
            info = ticker.info
            if info:
                raw["stock_info"] = info
                # 提取关键指标
                daily_basic = self._extract_daily_basic(info, raw.get("price_series"))
                if daily_basic is not None:
                    raw["daily_basic"] = daily_basic
                    available.update(["fundamental_tool"])
                logger.info(f"[YFinance] 基本面数据已获取")
            else:
                raw["stock_info"] = None
                raw["daily_basic"] = None
        except Exception as e:
            logger.warning(f"[YFinance] 基本面数据获取失败 {ticker_symbol}: {e}")
            raw["stock_info"] = None
            raw["daily_basic"] = None

        # 3. 获取财务报表数据
        try:
            financials = ticker.financials
            if financials is not None and not financials.empty:
                raw["financials"] = financials
                logger.debug(f"[YFinance] 财务报表数据已获取")
            else:
                raw["financials"] = None
        except Exception as e:
            logger.debug(f"[YFinance] 财务报表获取失败: {e}")
            raw["financials"] = None

        # 4. 获取资产负债表
        try:
            balance_sheet = ticker.balance_sheet
            if balance_sheet is not None and not balance_sheet.empty:
                raw["balance_sheet"] = balance_sheet
            else:
                raw["balance_sheet"] = None
        except Exception as e:
            raw["balance_sheet"] = None

        # 5. 获取现金流表
        try:
            cashflow = ticker.cashflow
            if cashflow is not None and not cashflow.empty:
                raw["cashflow"] = cashflow
            else:
                raw["cashflow"] = None
        except Exception as e:
            raw["cashflow"] = None

        # 美股特有：获取机构持股信息
        try:
            institutional_holders = ticker.institutional_holders
            if institutional_holders is not None and not institutional_holders.empty:
                raw["institutional_holders"] = institutional_holders.to_dict("records")
            else:
                raw["institutional_holders"] = None
        except Exception as e:
            raw["institutional_holders"] = None

        # 美股不支持的功能
        raw["capital_flow_raw"] = None
        raw["margin_raw"] = None
        raw["dragon_tiger_raw"] = None
        raw["shareholder_raw"] = None

        return raw, available

    def _normalize_price_data(self, hist: pd.DataFrame, end_date: str) -> pd.DataFrame:
        """
        标准化价格数据格式。

        将 yfinance 返回的数据转换为系统标准格式。
        """
        df = hist.reset_index()

        # yfinance 列名：Open, High, Low, Close, Volume, Dividends, Stock Splits
        # 转换为标准列名
        column_map = {
            "Date": "trade_date",
            "Datetime": "trade_date",  #  intraday 数据
            "Open": "open_adj",        # yfinance auto_adjust=True 已复权
            "High": "high_adj",
            "Low": "low_adj",
            "Close": "close_adj",
            "Volume": "vol",
        }

        # 选择并重命名列
        available_cols = {k: v for k, v in column_map.items() if k in df.columns}
        df = df[list(available_cols.keys())].rename(columns=available_cols)

        # 格式化日期列为 YYYYMMDD
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")

        # yfinance auto_adjust=True 时，价格已前复权
        # 为了兼容，设置 open/high/low/close = open_adj/high_adj/low_adj/close_adj
        df["open"] = df["open_adj"]
        df["high"] = df["high_adj"]
        df["low"] = df["low_adj"]
        df["close"] = df["close_adj"]

        # 计算涨跌幅 pct_chg
        if len(df) > 1:
            df["pct_chg"] = df["close_adj"].pct_change() * 100
            df["pct_chg"] = df["pct_chg"].fillna(0)
        else:
            df["pct_chg"] = 0

        # 添加市场类型标识
        df["market"] = "us_stock"

        return df

    def _extract_daily_basic(
        self,
        info: dict,
        price_df: Optional[pd.DataFrame] = None
    ) -> Optional[pd.DataFrame]:
        """
        从 info 中提取基本面指标，转换为 daily_basic 格式。

        美股与 A 股差异较大，部分字段可能缺失。
        """
        try:
            # 获取当前日期（或价格数据最后日期）
            if price_df is not None and not price_df.empty:
                trade_date = price_df["trade_date"].iloc[-1]
            else:
                trade_date = _today()

            # 构建基本面数据字典
            # 注意：美股很多指标命名与 A 股不同
            data = {
                "trade_date": [trade_date],
                # 估值指标
                "pe_ttm": info.get("trailingPE"),           # 市盈率 TTM
                "pe_lyr": info.get("trailingPE"),           # 无区分时相同
                "pb_mrq": info.get("priceToBook"),          # 市净率
                "ps_ttm": info.get("priceToSalesTrailing12Months"),  # 市销率
                # 股息率
                "dividend_yield": info.get("dividendYield", 0) * 100 if info.get("dividendYield") else None,
                # 市值（转换为亿元单位，与 A 股保持一致）
                "total_mv": info.get("marketCap", 0) / 1e8 if info.get("marketCap") else None,
                "circ_mv": info.get("marketCap", 0) / 1e8 if info.get("marketCap") else None,  # 美股全流通
                # 其他指标
                "turnover_rate": None,  # yfinance 不提供换手率
            }

            # 创建 DataFrame
            df = pd.DataFrame(data)
            return df

        except Exception as e:
            logger.warning(f"提取基本面数据失败: {e}")
            return None


def create_us_adapter() -> Optional[YFinanceAdapter]:
    """
    创建美股适配器工厂函数。

    如果 yfinance 未安装，返回 None 并记录警告。
    """
    try:
        import yfinance as yf  # noqa: F401
        return YFinanceAdapter()
    except ImportError:
        logger.warning("yfinance 未安装，美股数据源不可用。运行: pip install yfinance")
        return None
