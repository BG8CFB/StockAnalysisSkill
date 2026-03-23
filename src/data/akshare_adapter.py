from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import io

# -----------------------------------------------------------------------------
# Patch pandas.read_excel to support bytes input directly (for AkShare compat)
# -----------------------------------------------------------------------------
_original_read_excel = pd.read_excel
def _patched_read_excel(*args, **kwargs):
    if len(args) > 0 and isinstance(args[0], bytes):
        args = (io.BytesIO(args[0]),) + args[1:]
    elif "io" in kwargs and isinstance(kwargs["io"], bytes):
        kwargs["io"] = io.BytesIO(kwargs["io"])
    return _original_read_excel(*args, **kwargs)
pd.read_excel = _patched_read_excel
# -----------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _to_akshare_date(date_str: str) -> str:
    """YYYYMMDD → YYYY-MM-DD"""
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"


def _market_prefix(stock_code: str) -> str:
    """返回新浪财经格式的市场前缀代码，如 sh600000 / sz000001"""
    code_upper = stock_code.upper()
    pure = stock_code.split(".")[0]
    if code_upper.endswith(".SH"):
        return f"sh{pure}"
    if code_upper.endswith(".SZ"):
        return f"sz{pure}"
    # 港股/美股无前缀，直接返回纯代码
    return pure


def _recent_weekdays(end_date: str, n: int = 15) -> list[str]:
    """从 end_date 向前生成最多 n 个工作日日期（不排除节假日，仅排除周末）。"""
    result = []
    dt = datetime.strptime(end_date, "%Y%m%d")
    while len(result) < n:
        if dt.weekday() < 5:
            result.append(dt.strftime("%Y%m%d"))
        dt -= timedelta(days=1)
    return result


async def fetch_all(stock_code: str, start_date: Optional[str] = None,
                    end_date: Optional[str] = None) -> tuple[dict, set[str]]:
    """
    AkShare 全功能数据适配器。
    返回 (raw_data_dict, available_tools_set)，字段名与 tushare_adapter 保持一致。
    独立使用（无 Tushare Token）或作为 Tushare 补充均可。
    每个子请求独立 try/except，失败不影响其他字段。
    """
    import akshare as ak

    if start_date is None:
        start_date = _days_ago(400)
    if end_date is None:
        end_date = _today()

    logger.info(f"[AkShare] 开始拉取 {stock_code} 数据（{start_date} ~ {end_date}）")
    raw: dict = {"metadata": {"stock_code": stock_code, "source": "akshare", "date": end_date}}
    available: set[str] = set()

    code_upper = stock_code.upper()
    is_a_share = code_upper.endswith((".SZ", ".SH"))
    is_hk = code_upper.endswith(".HK")
    pure_code = stock_code.split(".")[0]
    sina_code = _market_prefix(stock_code)   # sh600000 / sz000001

    # ────────────────────────────────────────────────────────────────────────
    # 1. 行情数据（前复权 OHLCV）
    # ────────────────────────────────────────────────────────────────────────
    try:
        if is_a_share:
            df = None
            try:
                df = ak.stock_zh_a_hist(
                    symbol=pure_code,
                    period="daily",
                    start_date=_to_akshare_date(start_date),
                    end_date=_to_akshare_date(end_date),
                    adjust="qfq",
                )
                if df is not None and df.empty:
                    raise ValueError("stock_zh_a_hist returned empty dataframe (maybe future dates)")
            except Exception as e_hist:
                logger.warning(f"[AkShare] stock_zh_a_hist 拉取失败，尝试 fallback {stock_code}: {e_hist}")
                try:
                    df = ak.stock_zh_a_daily(
                        symbol=sina_code,
                        start_date=_to_akshare_date(start_date),
                        end_date=_to_akshare_date(end_date),
                        adjust="qfq",
                    )
                except Exception as e_daily:
                    logger.warning(f"[AkShare] stock_zh_a_daily fallback 失败 {stock_code}: {e_daily}")

            if df is not None and not df.empty:
                rename_map = {
                    "日期": "trade_date",
                    "date": "trade_date",
                    "开盘": "open", "最高": "high", "最低": "low", "收盘": "close",
                    "成交量": "vol", "volume": "vol", "成交额": "amount", "涨跌幅": "pct_chg",
                    "涨跌额": "change", "换手率": "turnover_rate", "turnover": "turnover_rate"
                }
                df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
                if "trade_date" in df.columns:
                    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")
                for col in ["open", "high", "low", "close"]:
                    if col in df.columns:
                        df[f"{col}_adj"] = df[col]
                df = df.sort_values("trade_date").reset_index(drop=True)
                if "close" in df.columns and "pct_chg" not in df.columns:
                    df["pct_chg"] = df["close"].pct_change() * 100
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

    # ────────────────────────────────────────────────────────────────────────
    # 2. 每日基本面（PE/PB/PS/换手率/市值）
    #    stock_value_em 返回 A 股个股每日估值
    # ────────────────────────────────────────────────────────────────────────
    if is_a_share:
        try:
            df_basic = ak.stock_value_em(symbol=pure_code)
            if df_basic is not None and not df_basic.empty:
                rename_map = {
                    "数据日期": "trade_date",
                    "PE(TTM)": "pe_ttm",
                    "市净率": "pb_mrq",
                    "市销率": "ps_ttm",
                    "总市值": "total_mv",
                    "流通市值": "circ_mv",
                }
                df_basic = df_basic.rename(
                    columns={k: v for k, v in rename_map.items() if k in df_basic.columns}
                )
                if "trade_date" in df_basic.columns:
                    df_basic["trade_date"] = pd.to_datetime(
                        df_basic["trade_date"]
                    ).dt.strftime("%Y%m%d")
                    df_basic = df_basic[
                        (df_basic["trade_date"] >= start_date)
                        & (df_basic["trade_date"] <= end_date)
                    ]
                # 若行情数据有换手率字段，合并进来
                if "turnover_rate" not in df_basic.columns and raw.get("price_series") is not None:
                    ps = raw["price_series"]
                    if "turnover_rate" in ps.columns:
                        df_basic = df_basic.merge(
                            ps[["trade_date", "turnover_rate"]], on="trade_date", how="left"
                        )
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

    # ────────────────────────────────────────────────────────────────────────
    # 3. 资金流向（仅 A 股）
    #    stock_individual_fund_flow 返回个股历史资金流向
    # ────────────────────────────────────────────────────────────────────────
    if is_a_share:
        try:
            market_str = "sh" if code_upper.endswith(".SH") else "sz"
            df_flow = ak.stock_individual_fund_flow(stock=pure_code, market=market_str)
            if df_flow is not None and not df_flow.empty:
                rename_map = {
                    "日期": "trade_date",
                    "主力净流入-净额": "main_net_today",
                    "超大单净流入-净额": "main_buy",
                    "小单净流入-净额": "retail_net_today",
                    "主力净流入-净占比": "main_ratio",
                }
                df_flow = df_flow.rename(
                    columns={k: v for k, v in rename_map.items() if k in df_flow.columns}
                )
                if "trade_date" in df_flow.columns:
                    df_flow["trade_date"] = pd.to_datetime(
                        df_flow["trade_date"]
                    ).dt.strftime("%Y%m%d")
                    df_flow = df_flow[
                        (df_flow["trade_date"] >= start_date)
                        & (df_flow["trade_date"] <= end_date)
                    ]
                df_flow = df_flow.sort_values("trade_date").reset_index(drop=True)
                # AkShare 资金流向单位为元，转换为万元（与 Tushare 统一）
                for col in ["main_net_today", "main_buy", "retail_net_today"]:
                    if col in df_flow.columns:
                        df_flow[col] = pd.to_numeric(df_flow[col], errors="coerce") / 10000
                # 滚动累计
                if "main_net_today" in df_flow.columns:
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

    # ────────────────────────────────────────────────────────────────────────
    # 4. 融资融券（仅 A 股）
    #    按近 10 个交易日逐日查询全市场融资融券数据，按股票代码过滤
    # ────────────────────────────────────────────────────────────────────────
    if is_a_share:
        try:
            margin_rows = []
            recent_dates = _recent_weekdays(end_date, n=20)
            for trade_dt in recent_dates[:20]:
                try:
                    if code_upper.endswith(".SZ"):
                        df_day = ak.stock_margin_detail_szse(date=trade_dt)
                    else:
                        df_day = ak.stock_margin_detail_sse(date=trade_dt)
                    if df_day is None or df_day.empty:
                        continue
                    # 找到本股票行：第一列通常是股票代码
                    code_col = df_day.columns[0]
                    matched = df_day[
                        df_day[code_col].astype(str).str.zfill(6) == pure_code.zfill(6)
                    ]
                    if not matched.empty:
                        row_dict = matched.iloc[0].to_dict()
                        row_dict["trade_date"] = trade_dt
                        margin_rows.append(row_dict)
                    if len(margin_rows) >= 10:
                        break
                except Exception:
                    continue

            if margin_rows:
                df_margin = pd.DataFrame(margin_rows)
                # 字段映射（上交所 / 深交所字段名略有差异，尝试多种可能）
                rename_candidates = {
                    # 上交所
                    "融资余额(元)": "margin_balance",
                    "融券余量(股)": "short_balance",
                    # 深交所
                    "融资余额": "margin_balance",
                    "融券余量": "short_balance",
                    "融资买入额": "margin_buy",
                    "融券卖出量": "short_sell",
                }
                df_margin = df_margin.rename(
                    columns={k: v for k, v in rename_candidates.items() if k in df_margin.columns}
                )
                df_margin = df_margin.sort_values("trade_date").reset_index(drop=True)
                # 转换单位：若为元则转换为万元
                if "margin_balance" in df_margin.columns:
                    raw_val = pd.to_numeric(df_margin["margin_balance"].iloc[-1], errors="coerce")
                    if raw_val and raw_val > 1e8:   # 判断是否为元单位
                        df_margin["margin_balance"] = (
                            pd.to_numeric(df_margin["margin_balance"], errors="coerce") / 10000
                        )
                    df_margin["margin_change_5d"] = (
                        pd.to_numeric(df_margin["margin_balance"], errors="coerce")
                        .pct_change(5) * 100
                    )
                if "short_balance" in df_margin.columns:
                    df_margin["short_balance"] = pd.to_numeric(
                        df_margin["short_balance"], errors="coerce"
                    )
                raw["margin_raw"] = df_margin
                available.update(["margin_tool"])
            else:
                raw["margin_raw"] = None
        except Exception as e:
            logger.warning(f"[AkShare] 融资融券数据拉取失败 {stock_code}: {e}")
            raw["margin_raw"] = None
    else:
        raw["margin_raw"] = None

    # ────────────────────────────────────────────────────────────────────────
    # 5. 龙虎榜（仅 A 股）
    #    stock_lhb_detail_em 获取历史区间记录并筛选个股
    # ────────────────────────────────────────────────────────────────────────
    if is_a_share:
        try:
            # 取近 90 日的日期
            start_lhb = _days_ago(90)
            end_lhb = end_date
            df_lhb = ak.stock_lhb_detail_em(start_date=start_lhb, end_date=end_lhb)
            if df_lhb is not None and not df_lhb.empty:
                df_lhb = df_lhb[df_lhb["代码"] == pure_code]
                if not df_lhb.empty:
                    rename_map = {
                        "上榜日": "trade_date",
                        "龙虎榜买入额": "buy_amount",
                        "龙虎榜卖出额": "sell_amount",
                        "龙虎榜净买额": "net_amount",
                        "上榜原因": "reason",
                    }
                    df_lhb = df_lhb.rename(
                        columns={k: v for k, v in rename_map.items() if k in df_lhb.columns}
                    )
                    if "trade_date" in df_lhb.columns:
                        df_lhb["trade_date"] = pd.to_datetime(
                            df_lhb["trade_date"]
                        ).dt.strftime("%Y%m%d")
                    df_lhb = df_lhb.sort_values("trade_date", ascending=False).reset_index(drop=True)
                    records = df_lhb.head(30).to_dict("records")
                    if records:
                        raw["dragon_tiger_raw"] = records
                        available.update(["dragon_tiger_tool"])
                    else:
                        raw["dragon_tiger_raw"] = None
                else:
                    raw["dragon_tiger_raw"] = None
            else:
                raw["dragon_tiger_raw"] = None
        except Exception as e:
            logger.warning(f"[AkShare] 龙虎榜数据拉取失败 {stock_code}: {e}")
            raw["dragon_tiger_raw"] = None
    else:
        raw["dragon_tiger_raw"] = None

    # ────────────────────────────────────────────────────────────────────────
    # 6. 公告/新闻（仅 A 股）
    # ────────────────────────────────────────────────────────────────────────
    if is_a_share:
        try:
            df_news = ak.stock_news_em(symbol=pure_code)
            if df_news is not None and not df_news.empty:
                rename_map = {
                    "发布时间": "ann_date",
                    "新闻标题": "title",
                }
                df_news = df_news.rename(
                    columns={k: v for k, v in rename_map.items() if k in df_news.columns}
                )
                if "ann_date" in df_news.columns:
                    cutoff = _days_ago(60)
                    # 格式化日期并截断时分秒
                    df_news["ann_date"] = pd.to_datetime(
                        df_news["ann_date"].str[:10]
                    ).dt.strftime("%Y%m%d")
                    df_news = df_news[df_news["ann_date"] >= cutoff]
                news_records = (
                    df_news[["ann_date", "title"]].head(50).to_dict("records")
                    if not df_news.empty else []
                )
                raw["news_raw"] = news_records
                if news_records:
                    available.update(["news_tool"])
            else:
                raw["news_raw"] = []
        except Exception as e:
            logger.warning(f"[AkShare] 公告新闻数据拉取失败 {stock_code}: {e}")
            raw["news_raw"] = []
    else:
        raw["news_raw"] = []

    # ────────────────────────────────────────────────────────────────────────
    # 7. 板块/行业分类（A 股）
    #    stock_profile_cninfo 可返回个股基本信息，含行业分类
    # ────────────────────────────────────────────────────────────────────────
    if is_a_share:
        try:
            df_info = ak.stock_profile_cninfo(symbol=pure_code)
            concepts = []
            if df_info is not None and not df_info.empty:
                if "所属行业" in df_info.columns:
                    val = df_info.iloc[0]["所属行业"]
                    if val and str(val).strip() and str(val).strip() != "nan":
                        concepts.append(str(val).strip())
            raw["sector_raw"] = {"concepts": concepts}
            available.update(["sector_tool"])
        except Exception as e:
            logger.warning(f"[AkShare] 板块概念数据拉取失败 {stock_code}: {e}")
            raw["sector_raw"] = {"concepts": []}
    else:
        raw["sector_raw"] = {"concepts": []}

    # ────────────────────────────────────────────────────────────────────────
    # 8. 停牌信息（简化：无 AkShare 专用接口，默认未停牌）
    # ────────────────────────────────────────────────────────────────────────
    raw["suspend_info"] = {"is_suspended": False}

    # ────────────────────────────────────────────────────────────────────────
    # 9. 市场情绪（全市场涨跌停统计，近 10 个交易日）
    #    stock_zt_pool_em / stock_zt_pool_dtgc_em 返回指定日期涨跌停股票池
    # ────────────────────────────────────────────────────────────────────────
    if is_a_share:
        try:
            sentiment_rows = []
            recent_dates = _recent_weekdays(end_date, n=20)
            for trade_dt in recent_dates[:20]:
                try:
                    df_up = ak.stock_zt_pool_em(date=trade_dt)
                    df_dn = ak.stock_zt_pool_dtgc_em(date=trade_dt)
                    up_count = len(df_up) if (df_up is not None and not df_up.empty) else 0
                    dn_count = len(df_dn) if (df_dn is not None and not df_dn.empty) else 0
                    if up_count > 0 or dn_count > 0:
                        sentiment_rows.append({
                            "trade_date": trade_dt,
                            "limit_up_count": up_count,
                            "limit_down_count": dn_count,
                        })
                    if len(sentiment_rows) >= 10:
                        break
                except Exception:
                    continue

            if sentiment_rows:
                raw["market_sentiment_raw"] = pd.DataFrame(
                    sorted(sentiment_rows, key=lambda x: x["trade_date"])
                )
                available.update(["sentiment_tool"])
            else:
                raw["market_sentiment_raw"] = None
        except Exception as e:
            logger.warning(f"[AkShare] 市场情绪数据拉取失败: {e}")
            raw["market_sentiment_raw"] = None
    else:
        raw["market_sentiment_raw"] = None

    # ────────────────────────────────────────────────────────────────────────
    # 10. 财务三表（仅 A 股）
    #     stock_financial_report_sina(stock="sh/sz + code", symbol="利润表|资产负债表|现金流量表")
    #     返回：DataFrame，行为报告期，列为中文字段名
    # ────────────────────────────────────────────────────────────────────────
    if is_a_share:
        _financial: dict = {}

        # 利润表字段映射
        INCOME_MAP = {
            "营业总收入": "revenue",
            "营业收入": "revenue",
            "净利润": "n_income",
            "归属于母公司所有者的净利润": "n_income_attr_p",
            "营业成本": "oper_cost",
            "营业总成本": "oper_cost",
            "销售费用": "sell_exp",
            "管理费用": "admin_exp",
            "财务费用": "fin_exp",
            "营业利润": "operate_profit",
            "息税前利润": "ebit",
        }

        # 资产负债表字段映射
        BS_MAP = {
            "资产总计": "total_assets",
            "负债合计": "total_liab",
            "流动资产合计": "total_cur_assets",
            "归属于母公司股东权益合计": "total_hldr_eqy_exc_min_int",
            "归属于母公司所有者权益合计": "total_hldr_eqy_exc_min_int",
            "商誉": "goodwill",
            "货币资金": "money_cap",
            "短期借款": "st_borr",
            "应收账款": "accounts_receiv",
            "存货": "inventories",
        }

        # 现金流量表字段映射
        CF_MAP = {
            "经营活动产生的现金流量净额": "n_cashflow_act",
            "投资活动产生的现金流量净额": "n_cashflow_inv_act",
            "购建固定资产、无形资产和其他长期资产支付的现金": "c_pay_acq_const_fiolta",
            "期末现金及现金等价物余额": "c_cash_equ_end_period",
        }

        def _parse_report(df_report: pd.DataFrame, field_map: dict,
                          date_col: str = "报告日") -> list[dict]:
            """将新浪财务报表 DataFrame 转换为 list[dict]（按报告期降序）。"""
            records = []
            if df_report is None or df_report.empty:
                return records
            cols = df_report.columns.tolist()
            for _, row in df_report.iterrows():
                record: dict = {}
                if date_col in row:
                    record["end_date"] = str(row[date_col])[:10].replace("-", "")
                for cn_name, en_name in field_map.items():
                    if cn_name in cols and en_name not in record:
                        val = row.get(cn_name)
                        if val is not None and not (isinstance(val, float) and pd.isna(val)):
                            try:
                                record[en_name] = float(val)
                            except (TypeError, ValueError):
                                pass
                records.append(record)
            # 按报告期降序
            records.sort(key=lambda x: x.get("end_date", ""), reverse=True)
            return records

        try:
            df_income = ak.stock_financial_report_sina(
                stock=sina_code, symbol="利润表"
            )
            if df_income is not None and not df_income.empty:
                _financial["income"] = _parse_report(df_income, INCOME_MAP)
        except Exception as e:
            logger.warning(f"[AkShare] 利润表拉取失败 {stock_code}: {e}")

        try:
            df_bs = ak.stock_financial_report_sina(
                stock=sina_code, symbol="资产负债表"
            )
            if df_bs is not None and not df_bs.empty:
                _financial["balancesheet"] = _parse_report(df_bs, BS_MAP)
        except Exception as e:
            logger.warning(f"[AkShare] 资产负债表拉取失败 {stock_code}: {e}")

        try:
            df_cf = ak.stock_financial_report_sina(
                stock=sina_code, symbol="现金流量表"
            )
            if df_cf is not None and not df_cf.empty:
                cf_records = _parse_report(df_cf, CF_MAP)
                # 计算自由现金流（经营现金流 - 资本支出）
                for rec in cf_records:
                    ocf = rec.get("n_cashflow_act")
                    capex = rec.get("c_pay_acq_const_fiolta")
                    if ocf is not None and capex is not None:
                        rec["free_cashflow"] = round(ocf - capex, 2)
                _financial["cashflow"] = cf_records
        except Exception as e:
            logger.warning(f"[AkShare] 现金流量表拉取失败 {stock_code}: {e}")

        raw["financial_raw"] = _financial if _financial else None

        # ── 财务核心指标（东方财富财务分析）──────────────────────────────────
        # stock_financial_analysis_indicator_em 接受 Tushare 格式代码如 "000001.SZ"
        try:
            df_fi = ak.stock_financial_analysis_indicator_em(
                symbol=stock_code, indicator="按报告期"
            )
            if df_fi is not None and not df_fi.empty:
                # 东方财富字段 → 标准字段映射
                EM_FI_MAP = {
                    "REPORT_DATE": "end_date",
                    "ROE_WEIGHT": "roe",
                    "ROEJQ": "roe",
                    "ROA": "roa",
                    "GROSS_PROFIT_RATIO": "grossprofit_margin",
                    "GPMARGIN": "grossprofit_margin",
                    "NET_PROFIT_RATIO": "netprofit_margin",
                    "NPMARGIN": "netprofit_margin",
                    "DEBT_ASSET_RATIO": "debt_to_assets",
                    "DEBT_RATIO": "debt_to_assets",
                    "CURRENT_RATIO": "current_ratio",
                    "QUICK_RATIO": "quick_ratio",
                    "TOTAL_ASSET_TURNOVER_RATE": "assets_turn",
                    "ASSETTURN": "assets_turn",
                    "ACCOUNTS_RECE_TURNOVER_DAYS": "arturn_days",
                    "INVENTORY_TURNOVER_DAYS": "invturn_days",
                    "TOTAL_REVENUE_YOY": "tr_yoy",
                    "REVENUE_RATE": "tr_yoy",
                    "PARENT_NETPROFIT_YOY": "netprofit_yoy",
                    "NETPROFIT_RATE": "netprofit_yoy",
                    "OCF_TO_NET_PROFIT": "ocf_to_profit",
                    "OCFTO_PROFIT": "ocf_to_profit",
                    "BPS": "bps",
                    "OCFPS": "ocfps",
                }
                fi_records = []
                # df_fi 通常按 REPORT_DATE 降序，取最新 8 期
                date_col = "REPORT_DATE" if "REPORT_DATE" in df_fi.columns else df_fi.columns[0]
                for _, row in df_fi.head(8).iterrows():
                    record: dict = {}
                    for em_col, std_col in EM_FI_MAP.items():
                        if em_col in df_fi.columns and std_col not in record:
                            val = row.get(em_col)
                            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                                try:
                                    record[std_col] = round(float(val), 4)
                                except (TypeError, ValueError):
                                    pass
                    # 处理日期字段
                    date_val = row.get("REPORT_DATE")
                    if date_val is not None:
                        try:
                            record["end_date"] = str(pd.to_datetime(date_val).strftime("%Y%m%d"))
                        except Exception:
                            record["end_date"] = str(date_val)[:10].replace("-", "")
                    fi_records.append(record)

                if fi_records:
                    if raw.get("financial_raw") is None:
                        raw["financial_raw"] = {}
                    raw["financial_raw"]["fina_indicator"] = fi_records
                    available.update(["fundamental_tool"])
        except Exception as e:
            logger.warning(f"[AkShare] 财务核心指标(em)拉取失败 {stock_code}: {e}")

        # 若 fina_indicator 未拿到但三表有数据，fundamental_tool 仍可通过本地计算激活
        if raw.get("financial_raw"):
            available.update(["fundamental_tool"])
    else:
        raw["financial_raw"] = None

    # ────────────────────────────────────────────────────────────────────────
    # 11. 分红历史（仅 A 股）
    #     stock_history_dividend_detail 返回历史分红明细
    # ────────────────────────────────────────────────────────────────────────
    if is_a_share:
        try:
            df_div = ak.stock_history_dividend_detail(symbol=pure_code, indicator="分红")
            if df_div is not None and not df_div.empty:
                # 字段：公告日期, 送股, 转增, 派息, 进度, 除权除息日, 股权登记日, 红股上市日
                div_records = []
                for _, row in df_div.head(10).iterrows():
                    ann_date = row.get("公告日期")
                    ann_str = ""
                    if ann_date is not None:
                        try:
                            ann_str = pd.to_datetime(ann_date).strftime("%Y%m%d")
                        except Exception:
                            ann_str = str(ann_date)

                    ex_date = row.get("除权除息日")
                    ex_str = ""
                    if ex_date is not None:
                        try:
                            ex_str = pd.to_datetime(ex_date).strftime("%Y%m%d")
                        except Exception:
                            ex_str = str(ex_date)

                    rec_date = row.get("股权登记日")
                    rec_str = ""
                    if rec_date is not None:
                        try:
                            rec_str = pd.to_datetime(rec_date).strftime("%Y%m%d")
                        except Exception:
                            rec_str = str(rec_date)

                    cash_div = row.get("派息")
                    stk_div = (row.get("送股", 0) or 0)
                    div_records.append({
                        "end_date": ann_str,
                        "ann_date": ann_str,
                        "ex_date": ex_str,
                        "record_date": rec_str,
                        "cash_div_tax": float(cash_div) / 10 if cash_div else None,  # 转为每股元
                        "stk_div": float(stk_div) / 10 if stk_div else 0,
                    })
                raw["dividend_raw"] = [r for r in div_records if r.get("end_date")]
            else:
                raw["dividend_raw"] = []
        except Exception as e:
            logger.warning(f"[AkShare] 分红数据拉取失败 {stock_code}: {e}")
            raw["dividend_raw"] = []
    else:
        raw["dividend_raw"] = []

    # ────────────────────────────────────────────────────────────────────────
    # 12. 股东结构（仅 A 股）
    # ────────────────────────────────────────────────────────────────────────
    if is_a_share:
        _shareholder: dict = {}

        # 12a. 股东人数趋势
        try:
            df_holder = ak.stock_zh_a_gdhs_detail_em(symbol=pure_code)
            if df_holder is not None and not df_holder.empty:
                date_col = "股东户数统计截止日"
                num_col = "股东户数-本次"
                if date_col in df_holder.columns and num_col in df_holder.columns:
                    holder_summary = (
                        df_holder[[date_col, num_col]]
                        .drop_duplicates(subset=[date_col])
                        .dropna(subset=[num_col])
                        .sort_values(date_col, ascending=False)
                        .head(8)
                    )
                    holder_records = []
                    for _, row in holder_summary.iterrows():
                        dt_val = row.get(date_col)
                        try:
                            dt_str = pd.to_datetime(dt_val).strftime("%Y%m%d")
                        except Exception:
                            dt_str = str(dt_val)
                        num_val = row.get(num_col)
                        holder_records.append({
                            "end_date": dt_str,
                            "holder_num": int(float(num_val)) if num_val else None,
                        })
                    if holder_records:
                        _shareholder["holder_num"] = holder_records
        except Exception as e:
            logger.warning(f"[AkShare] 股东人数数据拉取失败 {stock_code}: {e}")

        # 12b. 股权质押
        try:
            df_pledge = ak.stock_gpzy_pledge_ratio_em()
            if df_pledge is not None and not df_pledge.empty:
                df_pledge = df_pledge[df_pledge["股票代码"] == pure_code]
                if not df_pledge.empty:
                    p = df_pledge.iloc[0]
                    _shareholder["pledge"] = [{
                        "end_date": str(p.get("交易日期", "")),
                        "pledge_ratio": float(p.get("质押比例", 0)) if p.get("质押比例") else 0.0,
                        "pledge_count": int(p.get("质押笔数", 0)) if p.get("质押笔数") else 0,
                        "unrest_pledge": float(p.get("无限售股质押数", 0)) * 10000 if p.get("无限售股质押数") else 0.0,
                        "rest_pledge": float(p.get("限售股质押数", 0)) * 10000 if p.get("限售股质押数") else 0.0,
                    }]
        except Exception as e:
            logger.warning(f"[AkShare] 质押数据拉取失败 {stock_code}: {e}")

        if _shareholder:
            raw["shareholder_raw"] = _shareholder
            available.update(["shareholder_tool"])
        else:
            raw["shareholder_raw"] = None
    else:
        raw["shareholder_raw"] = None

    raw["metadata"]["available_count"] = len(available)
    logger.info(f"[AkShare] {stock_code} 数据拉取完成，可用工具: {available}")
    return raw, available


async def merge_with_tushare(tushare_raw: dict, tushare_available: set[str],
                              stock_code: str, start_date: Optional[str] = None,
                              end_date: Optional[str] = None) -> tuple[dict, set[str]]:
    """
    以 Tushare 数据为主，按工具粒度进行 AkShare 自动回退。

    对每个数据分区单独检测：若 TU 返回 None / 空列表 / 空 DataFrame（常见原因：
    接口权限不足如 disclosure/fina_indicator、当日无数据如龙虎榜），则自动用
    AkShare 补充该分区。全程只触发一次 AkShare 全量拉取，避免重复请求。

    额外处理：
    - 行情/daily_basic 滞后自动更新（盘中 TU 延迟时用 AK 补全当日数据）
    - financial_raw 内部补充（TU 三表有数据但 fina_indicator 因积分不足缺失时，
      从 AkShare 东方财富接口获取财务指标并写入 financial_raw["fina_indicator"]）

    TU ↔ AK 工具分区对应关系：
      price_series      → price_tool, indicator_tool
      daily_basic       → fundamental_tool, snapshot_tool
      capital_flow_raw  → capital_flow_tool    (TU: moneyflow / AK: individual_fund_flow)
      margin_raw        → margin_tool          (TU: margin_detail / AK: margin_detail_szse/sse)
      dragon_tiger_raw  → dragon_tiger_tool    (TU: top_list 单日 / AK: lhb_stock_detail_em 90日)
      news_raw          → news_tool            (TU: disclosure 需权限 / AK: stock_notice_report)
      sector_raw        → sector_tool          (TU: concept_detail / AK: individual_info_em)
      market_sentiment_raw → sentiment_tool   (TU: limit_list_d / AK: zt_pool_em+dt_pool_em)
      financial_raw     → fundamental_tool     (TU: income+cashflow+bs / AK: sina三表+em指标)
      dividend_raw      → (归属 fundamental)   (TU: dividend / AK: history_dividend_detail)
      shareholder_raw   → shareholder_tool     (TU: holdernumber+pledge / AK: main_holder+pledge_em)
    """
    merged_raw = dict(tushare_raw)
    merged_available = set(tushare_available)

    today_str = _today()

    def _is_section_missing(key: str, val) -> bool:
        """判断 TU 某数据分区是否缺失/无效，需要 AkShare 工具级补充。"""
        if val is None:
            return True
        if isinstance(val, list) and len(val) == 0:
            # news_raw=[] 通常是权限不足；dividend_raw=[] 可能确实无分红
            # 两种情况均尝试 AK 兜底（AK 若也无数据会返回 []，不影响结果）
            return True
        if isinstance(val, pd.DataFrame) and val.empty:
            return True
        if isinstance(val, dict):
            if len(val) == 0:
                return True
            # sector_raw 概念列表为空 = TU concept_detail 未返回数据
            if key == "sector_raw" and not val.get("concepts"):
                return True
        return False

    # 工具分区映射：raw_data 键 → 对应的 available_tool 名称列表
    SECTION_TOOLS: list[tuple[str, list[str]]] = [
        ("price_series",          ["price_tool", "indicator_tool"]),
        ("daily_basic",           ["fundamental_tool", "snapshot_tool"]),
        ("capital_flow_raw",      ["capital_flow_tool"]),
        ("margin_raw",            ["margin_tool"]),
        ("dragon_tiger_raw",      ["dragon_tiger_tool"]),
        ("news_raw",              ["news_tool"]),
        ("sector_raw",            ["sector_tool"]),
        ("market_sentiment_raw",  ["sentiment_tool"]),
        ("financial_raw",         ["fundamental_tool"]),
        ("dividend_raw",          []),   # 分红归属 fundamental_tool，不单独列工具
        ("shareholder_raw",       ["shareholder_tool"]),
    ]

    # ── 1. 检测哪些分区需要 AkShare 补充 ───────────────────────────────────
    missing_sections = {
        key for key, _ in SECTION_TOOLS
        if _is_section_missing(key, tushare_raw.get(key))
    }

    # 行情数据是否滞后（有数据但日期不是今日）
    ts_price = tushare_raw.get("price_series")
    price_is_stale = (
        ts_price is not None
        and not ts_price.empty
        and str(ts_price.iloc[-1].get("trade_date", "")) < today_str
    )

    # TU 三表有数据但 fina_indicator 缺失（积分不足的高频场景）
    tu_fi = tushare_raw.get("financial_raw") or {}
    needs_fina_supplement = (
        isinstance(tu_fi, dict)
        and bool(tu_fi)
        and "fina_indicator" not in tu_fi
    )

    if not missing_sections and not price_is_stale and not needs_fina_supplement:
        return merged_raw, merged_available

    # ── 2. 触发一次 AkShare 全量拉取 ────────────────────────────────────────
    log_parts: list[str] = []
    if missing_sections:
        log_parts.append(f"缺失分区={sorted(missing_sections)}")
    if price_is_stale:
        log_parts.append("行情滞后")
    if needs_fina_supplement:
        log_parts.append("三表有/fina_indicator缺")
    logger.info(f"[Merge] {stock_code} 启动 AkShare 工具级补充：{'，'.join(log_parts)}")

    try:
        ak_raw, _ak_available = await fetch_all(stock_code, start_date, end_date)
    except Exception as e:
        logger.warning(f"[AkShare] 工具级补充拉取失败 {stock_code}: {e}")
        return merged_raw, merged_available

    # ── 3. 逐分区合并（仅填充 TU 缺失的分区）──────────────────────────────
    for key, tools in SECTION_TOOLS:
        if key not in missing_sections:
            continue
        ak_val = ak_raw.get(key)
        if _is_section_missing(key, ak_val):
            logger.debug(f"[Merge] AkShare 也未能提供 '{key}'（{stock_code}），保留空值")
            continue
        merged_raw[key] = ak_val
        merged_available.update(tools)
        logger.info(f"[Merge] AkShare 工具级补充 '{key}' for {stock_code}")

    # ── 4. 行情 / daily_basic 滞后补全 ────────────────────────────────────
    if price_is_stale:
        ak_price = ak_raw.get("price_series")
        if ak_price is not None and not ak_price.empty:
            ts_latest = str(ts_price.iloc[-1].get("trade_date", ""))
            ak_latest = str(ak_price.iloc[-1].get("trade_date", ""))
            if ak_latest > ts_latest:
                merged_raw["price_series"] = ak_price
                logger.info(
                    f"[Merge] AkShare 行情补全：{ts_latest} → {ak_latest}（{stock_code}）"
                )

        # daily_basic 同步更新
        ak_basic = ak_raw.get("daily_basic")
        ts_basic = tushare_raw.get("daily_basic")
        if ak_basic is not None and not ak_basic.empty:
            ts_basic_date = (
                str(ts_basic.iloc[-1].get("trade_date", ""))
                if ts_basic is not None and not ts_basic.empty
                else ""
            )
            ak_basic_date = str(ak_basic.iloc[-1].get("trade_date", ""))
            if ak_basic_date > ts_basic_date:
                merged_raw["daily_basic"] = ak_basic
                logger.info(f"[Merge] AkShare 更新 daily_basic → {ak_basic_date}")

    # ── 5. financial_raw 内部补充 fina_indicator ───────────────────────────
    # 场景：TU 三表拉取成功，但 fina_indicator 因积分不足返回空
    # 解决：从 AkShare 东方财富财务分析接口获取预计算的财务指标
    if needs_fina_supplement:
        ak_fi_dict = ak_raw.get("financial_raw") or {}
        ak_fina_records = ak_fi_dict.get("fina_indicator")
        if ak_fina_records:
            if not isinstance(merged_raw.get("financial_raw"), dict):
                merged_raw["financial_raw"] = {}
            merged_raw["financial_raw"]["fina_indicator"] = ak_fina_records
            merged_available.add("fundamental_tool")
            logger.info(f"[Merge] AkShare 补充 fina_indicator for {stock_code}")

    return merged_raw, merged_available
