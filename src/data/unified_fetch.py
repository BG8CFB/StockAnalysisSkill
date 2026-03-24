"""
数据层统一入口模块。

提供跨市场的数据获取能力：
- A股：Tushare（主）+ AkShare（备）
- 港股：AkShare（主）
- 美股：YFinance（主）

使用方式：
    from src.data.unified_fetch import fetch_stock_data
    raw_data, available_tools = await fetch_stock_data("AAPL")
    raw_data, available_tools = await fetch_stock_data("0700.HK")
    raw_data, available_tools = await fetch_stock_data("000001.SZ")
"""

from __future__ import annotations

import logging
from typing import Optional

from src.data.market_router import get_router, MarketType, normalize_code
from src.data.tushare_adapter import fetch_all as tushare_fetch
from src.data.akshare_adapter import fetch_all as akshare_fetch
from src.data.us_stock_adapter import create_us_adapter, YFinanceAdapter
from src.data.hk_stock_adapter import create_hk_adapter, HKStockAdapter

logger = logging.getLogger(__name__)

# 标记是否已初始化
_is_initialized = False


class TushareAdapterWrapper:
    """Tushare 适配器包装器（兼容 DataAdapter 协议）。"""

    def __init__(self):
        self.name = "tushare"

    @property
    def supported_markets(self):
        return [MarketType.A_SHARE]

    def is_available(self) -> bool:
        try:
            from src.config import settings
            return bool(settings.tushare_token)
        except Exception:
            return False

    async def fetch_all(self, stock_code: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
        return await tushare_fetch(stock_code, start_date, end_date)


class AkShareAdapterWrapper:
    """AkShare 适配器包装器（兼容 DataAdapter 协议）。"""

    def __init__(self):
        self.name = "akshare"
        self._ak = None

    @property
    def supported_markets(self):
        return [MarketType.A_SHARE, MarketType.HK_STOCK]

    def is_available(self) -> bool:
        try:
            import akshare as ak  # noqa: F401
            return True
        except ImportError:
            return False

    async def fetch_all(self, stock_code: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
        return await akshare_fetch(stock_code, start_date, end_date)


def initialize_data_layer():
    """初始化数据层，注册所有适配器。"""
    global _is_initialized

    if _is_initialized:
        return

    router = get_router()

    # 1. 注册 A股适配器（优先级：Tushare > AkShare）
    tushare_wrapper = TushareAdapterWrapper()
    if tushare_wrapper.is_available():
        router.register_adapter(tushare_wrapper, priority=0)
        logger.info("[DataLayer] Tushare 适配器已注册")
    else:
        logger.warning("[DataLayer] Tushare Token 未配置，跳过")

    # AkShare 作为 A 股备选，同时支持港股/美股
    akshare_wrapper = AkShareAdapterWrapper()
    if akshare_wrapper.is_available():
        router.register_adapter(akshare_wrapper, priority=1)
        logger.info("[DataLayer] AkShare 适配器已注册")
    else:
        logger.warning("[DataLayer] AkShare 未安装，跳过")

    # 2. 注册美股专用适配器（yfinance）
    us_adapter = create_us_adapter()
    if us_adapter:
        router.register_adapter(us_adapter, priority=0)
        logger.info("[DataLayer] YFinance 美股适配器已注册")
    else:
        logger.warning("[DataLayer] YFinance 未安装，美股数据不可用")

    # 3. 注册港股专用适配器
    hk_adapter = create_hk_adapter()
    if hk_adapter:
        router.register_adapter(hk_adapter, priority=0)
        logger.info("[DataLayer] HKStock 港股适配器已注册")
    else:
        logger.warning("[DataLayer] AkShare 未安装，港股数据不可用")

    _is_initialized = True
    logger.info("[DataLayer] 数据层初始化完成")


async def fetch_stock_data(
    stock_code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple[dict, set[str]]:
    """
    统一数据获取入口。

    自动识别市场类型，路由到对应的数据源适配器。

    Args:
        stock_code: 股票代码
            - A股: "000001.SZ", "600000.SH"
            - 港股: "0700.HK", "9988.HK"
            - 美股: "AAPL", "TSLA"
        start_date: 开始日期 YYYYMMDD
        end_date: 结束日期 YYYYMMDD

    Returns:
        (raw_data_dict, available_tools_set)

    Example:
        >>> data, tools = await fetch_stock_data("AAPL")
        >>> data, tools = await fetch_stock_data("0700.HK")
        >>> data, tools = await fetch_stock_data("000001.SZ")
    """
    # 确保已初始化
    if not _is_initialized:
        initialize_data_layer()

    router = get_router()

    # 识别市场类型
    identity = normalize_code(stock_code)
    market = identity.market

    logger.info(f"[UnifiedFetch] 获取 {stock_code} ({market.value}) 数据")

    try:
        return await router.fetch_all(stock_code, start_date, end_date)
    except Exception as e:
        logger.error(f"[UnifiedFetch] 获取 {stock_code} 数据失败: {e}")
        raise


def get_market_info(stock_code: str) -> dict:
    """
    获取股票的市场信息和支持的工具。

    Returns:
        {
            "market": "us_stock|hk_stock|a_share",
            "supported_tools": [...],
            "unsupported_tools": [...],
            "description": "..."
        }
    """
    identity = normalize_code(stock_code)
    market = identity.market

    router = get_router()
    tools_info = router.get_market_tools(stock_code)

    descriptions = {
        MarketType.A_SHARE: "A股市场，支持融资融券、龙虎榜、股东结构等完整功能",
        MarketType.HK_STOCK: "港股市场，支持港股通资金流向",
        MarketType.US_STOCK: "美股市场，支持机构持股数据",
    }

    return {
        "market": market.value,
        "supported_tools": tools_info["supported"],
        "unsupported_tools": tools_info["unsupported"],
        "description": descriptions.get(market, "未知市场"),
    }
