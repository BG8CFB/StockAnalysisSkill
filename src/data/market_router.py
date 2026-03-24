"""
市场类型定义与路由模块。

提供统一的市场识别、数据源路由和适配器管理功能。
支持 A股、港股、美股三大市场的数据获取。
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Optional, Protocol
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


class MarketType(Enum):
    """市场类型枚举。"""
    A_SHARE = "a_share"           # A股：000001.SZ, 600000.SH
    HK_STOCK = "hk_stock"         # 港股：0700.HK, 9988.HK
    US_STOCK = "us_stock"         # 美股：AAPL, TSLA, BRK.B
    UNKNOWN = "unknown"           # 未知


@dataclass(frozen=True)
class StockIdentity:
    """标准化股票标识。"""
    original_code: str            # 原始代码
    market: MarketType            # 市场类型
    normalized_code: str          # 标准化代码（供数据源使用）
    exchange: Optional[str] = None  # 交易所代码

    @property
    def pure_code(self) -> str:
        """纯数字/字母代码部分。"""
        return self.original_code.split(".")[0]


def detect_market(stock_code: str) -> MarketType:
    """
    根据股票代码识别市场类型。

    识别规则：
    - A股：以 .SZ 或 .SH 结尾（不区分大小写）
    - 港股：以 .HK 结尾（不区分大小写）
    - 美股：纯字母，或包含 . 但非上述后缀（如 BRK.B）

    Args:
        stock_code: 股票代码，如 "000001.SZ", "0700.HK", "AAPL"

    Returns:
        MarketType 枚举值

    Raises:
        ValueError: 无法识别的市场类型
    """
    if not stock_code or not isinstance(stock_code, str):
        raise ValueError(f"无效的股票代码: {stock_code}")

    code_upper = stock_code.upper().strip()

    # A股识别
    if code_upper.endswith((".SZ", ".SH")):
        return MarketType.A_SHARE

    # 港股识别
    if code_upper.endswith(".HK"):
        return MarketType.HK_STOCK

    # 美股识别：纯字母代码，或包含 . 但非上述后缀
    # 支持格式：AAPL, TSLA, BRK.B, GOOGL
    if code_upper.replace(".", "").replace("-", "").isalpha():
        return MarketType.US_STOCK

    logger.warning(f"无法识别市场类型: {stock_code}，尝试作为美股处理")
    return MarketType.US_STOCK


def normalize_code(stock_code: str, market: Optional[MarketType] = None) -> StockIdentity:
    """
    标准化股票代码，返回统一标识对象。

    Args:
        stock_code: 原始股票代码
        market: 可选，已知的市场的类型（用于跳过自动检测）

    Returns:
        StockIdentity 标准化标识对象
    """
    original = stock_code.strip()

    if market is None:
        market = detect_market(original)

    # 根据市场类型标准化
    if market == MarketType.A_SHARE:
        normalized = original.upper()
        exchange = "SZ" if normalized.endswith(".SZ") else "SH"
    elif market == MarketType.HK_STOCK:
        normalized = original.upper()
        exchange = "HK"
    elif market == MarketType.US_STOCK:
        # yfinance 格式：直接使用大写字母代码
        normalized = original.upper()
        exchange = None  # 美股交易所信息从数据源获取
    else:
        normalized = original
        exchange = None

    return StockIdentity(
        original_code=original,
        market=market,
        normalized_code=normalized,
        exchange=exchange
    )


class DataAdapter(Protocol):
    """
    数据源适配器协议。

    所有市场适配器必须实现此协议。
    """

    @property
    def name(self) -> str:
        """适配器名称。"""
        ...

    @property
    def supported_markets(self) -> list[MarketType]:
        """支持的市场类型列表。"""
        ...

    async def fetch_all(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> tuple[dict, set[str]]:
        """
        获取完整股票数据。

        Args:
            stock_code: 标准化股票代码
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            (raw_data_dict, available_tools_set)
        """
        ...

    def is_available(self) -> bool:
        """检查适配器是否可用（配置是否完整）。"""
        ...


class MarketRouter:
    """
    市场数据源路由器。

    根据股票代码自动识别市场，路由到对应的适配器。
    支持主备数据源切换。
    """

    def __init__(self):
        self._adapters: dict[MarketType, list[DataAdapter]] = {
            MarketType.A_SHARE: [],
            MarketType.HK_STOCK: [],
            MarketType.US_STOCK: [],
        }
        self._fallback_map: dict[MarketType, list[MarketType]] = {}

    def register_adapter(self, adapter: DataAdapter, priority: int = 0) -> None:
        """
        注册数据源适配器。

        Args:
            adapter: 适配器实例
            priority: 优先级，数字越小优先级越高
        """
        for market in adapter.supported_markets:
            if market in self._adapters:
                # 按优先级插入
                adapters = self._adapters[market]
                inserted = False
                for i, existing in enumerate(adapters):
                    # 简单实现：直接追加，后续按顺序尝试
                    pass
                adapters.append(adapter)
                logger.info(f"注册适配器 {adapter.name} 到市场 {market.value}")

    async def fetch_all(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> tuple[dict, set[str]]:
        """
        获取股票数据（自动路由到对应市场适配器）。

        Args:
            stock_code: 股票代码
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            (raw_data_dict, available_tools_set)

        Raises:
            ValueError: 无可用适配器
            Exception: 所有适配器都失败
        """
        identity = normalize_code(stock_code)
        market = identity.market

        logger.info(f"[MarketRouter] 识别 {stock_code} 为 {market.value}")

        adapters = self._adapters.get(market, [])
        if not adapters:
            raise ValueError(f"市场 {market.value} 没有可用的数据源适配器")

        last_error = None
        for adapter in adapters:
            if not adapter.is_available():
                logger.debug(f"适配器 {adapter.name} 不可用，跳过")
                continue

            try:
                logger.info(f"[MarketRouter] 使用 {adapter.name} 获取 {stock_code} 数据")
                return await adapter.fetch_all(
                    identity.normalized_code,
                    start_date=start_date,
                    end_date=end_date
                )
            except Exception as e:
                logger.warning(f"适配器 {adapter.name} 获取数据失败: {e}")
                last_error = e
                continue

        if last_error:
            raise last_error
        raise ValueError(f"市场 {market.value} 的所有适配器都不可用")

    def get_market_tools(self, stock_code: str) -> dict[str, list[str]]:
        """
        获取某市场支持的工具列表。

        Returns:
            {"supported": [...], "unsupported": [...]}
        """
        market = detect_market(stock_code)

        # 定义各市场支持的工具
        tools_map = {
            MarketType.A_SHARE: {
                "supported": [
                    "market_data_tool", "fundamental_tool", "microstructure_tool",
                    "sentiment_tool", "sector_tool", "news_tool", "risk_metric_tool"
                ],
                "unsupported": []
            },
            MarketType.HK_STOCK: {
                "supported": [
                    "market_data_tool", "fundamental_tool",
                    "sentiment_tool", "sector_tool", "news_tool", "risk_metric_tool"
                ],
                "unsupported": ["microstructure_tool"]  # 港股无融资/龙虎榜
            },
            MarketType.US_STOCK: {
                "supported": [
                    "market_data_tool", "fundamental_tool",
                    "sentiment_tool", "sector_tool", "news_tool", "risk_metric_tool"
                ],
                "unsupported": ["microstructure_tool"]  # 美股无融资/龙虎榜
            }
        }

        return tools_map.get(market, {"supported": [], "unsupported": []})


# 全局路由器实例
_router: Optional[MarketRouter] = None


def get_router() -> MarketRouter:
    """获取全局 MarketRouter 实例（单例）。"""
    global _router
    if _router is None:
        _router = MarketRouter()
    return _router


def reset_router() -> None:
    """重置全局路由器（主要用于测试）。"""
    global _router
    _router = None
