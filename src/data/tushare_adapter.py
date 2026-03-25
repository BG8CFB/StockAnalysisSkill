from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_pro_api = None


def _get_pro():
    global _pro_api
    if _pro_api is None:
        import tushare as ts
        from src.config import settings
        _pro_api = ts.pro_api(settings.tushare_token)
    return _pro_api


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


async def fetch_all(stock_code: str, start_date: Optional[str] = None,
                    end_date: Optional[str] = None) -> tuple[dict, set[str]]:
    """
    拉取完整股票数据并映射为标准字段。
    返回 (raw_data_dict, available_tools_set)。
    每个子请求独立 try/except，失败不影响其他字段。
    """
    if start_date is None:
        start_date = _days_ago(400)
    if end_date is None:
        end_date = _today()

    logger.info(f"[Tushare] 开始拉取 {stock_code} 数据（{start_date} ~ {end_date}）")
    raw: dict = {"metadata": {"stock_code": stock_code, "source": "tushare", "date": end_date}}
    available: set[str] = set()

    pro = _get_pro()

    # 1. 行情数据（前复权）
    try:
        df = pro.daily(ts_code=stock_code, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            # 获取复权因子
            try:
                adj_df = pro.adj_factor(ts_code=stock_code, start_date=start_date, end_date=end_date)
                if adj_df is not None and not adj_df.empty:
                    adj_df = adj_df.rename(columns={"adj_factor": "adj"})
                    df = df.merge(adj_df[["trade_date", "adj"]], on="trade_date", how="left")
                    df = df.sort_values("trade_date").reset_index(drop=True)
                    df["adj"] = df["adj"].ffill().bfill().fillna(1.0)
                    
                    # 计算前复权：当日收盘价 * (当日复权因子 / 最新复权因子)
                    latest_adj = df["adj"].iloc[-1] if not df.empty else 1.0
                    for col in ["open", "high", "low", "close"]:
                        df[f"{col}_adj"] = df[col] * (df["adj"] / latest_adj)
                else:
                    df = df.sort_values("trade_date").reset_index(drop=True)
                    for col in ["open", "high", "low", "close"]:
                        df[f"{col}_adj"] = df[col]
            except Exception:
                df = df.sort_values("trade_date").reset_index(drop=True)
                for col in ["open", "high", "low", "close"]:
                    df[f"{col}_adj"] = df[col]

            raw["price_series"] = df
            available.add("market_data_tool")
        else:
            raw["price_series"] = None
    except Exception as e:
        logger.warning(f"[Tushare] 日线数据拉取失败 {stock_code}: {e}")
        raw["price_series"] = None

    # 2. 基本面数据（每日指标）
    try:
        df_basic = pro.daily_basic(ts_code=stock_code, start_date=start_date, end_date=end_date,
                                   fields="ts_code,trade_date,pe_ttm,pb,ps_ttm,dv_ttm,turnover_rate,circ_mv,total_mv")
        if df_basic is not None and not df_basic.empty:
            df_basic = df_basic.rename(columns={"pb": "pb_mrq", "dv_ttm": "dividend_yield"})
            df_basic = df_basic.sort_values("trade_date").reset_index(drop=True)
            raw["daily_basic"] = df_basic
            available.add("fundamental_tool")
        else:
            raw["daily_basic"] = None
    except Exception as e:
        logger.warning(f"[Tushare] 基本面数据拉取失败 {stock_code}: {e}")
        raw["daily_basic"] = None

    # 3. 资金流向（A股）
    if stock_code.upper().endswith((".SZ", ".SH")):
        try:
            df_flow = pro.moneyflow(ts_code=stock_code, start_date=start_date, end_date=end_date)
            if df_flow is not None and not df_flow.empty:
                # 字段映射：Tushare → 标准字段
                rename_map = {
                    "net_mf_amount": "main_net_today",   # 主力净流入（万元）
                    "buy_lg_amount": "main_buy",
                    "sell_lg_amount": "main_sell",
                    "buy_sm_amount": "retail_buy",
                    "sell_sm_amount": "retail_sell",
                }
                df_flow = df_flow.rename(columns={k: v for k, v in rename_map.items() if k in df_flow.columns})
                # 计算散户净流入
                if "retail_buy" in df_flow.columns and "retail_sell" in df_flow.columns:
                    df_flow["retail_net_today"] = df_flow["retail_buy"] - df_flow["retail_sell"]
                # 计算多日累计
                if "main_net_today" in df_flow.columns:
                    df_flow = df_flow.sort_values("trade_date").reset_index(drop=True)
                    df_flow["main_net_3d"] = df_flow["main_net_today"].rolling(3).sum()
                    df_flow["main_net_5d"] = df_flow["main_net_today"].rolling(5).sum()
                    df_flow["main_net_10d"] = df_flow["main_net_today"].rolling(10).sum()
                    if "trade_amount" in df_flow.columns and df_flow["trade_amount"].iloc[-1] > 0:
                        df_flow["main_ratio"] = df_flow["main_net_today"].abs() / df_flow["trade_amount"] * 100
                    else:
                        df_flow["main_ratio"] = None

                raw["capital_flow_raw"] = df_flow
                available.add("microstructure_tool")
            else:
                raw["capital_flow_raw"] = None
        except Exception as e:
            logger.warning(f"[Tushare] 资金流向数据拉取失败 {stock_code}: {e}")
            raw["capital_flow_raw"] = None

        # 北向资金（全市场）
        try:
            df_north = pro.moneyflow_hsgt(start_date=start_date, end_date=end_date)
            if df_north is not None and not df_north.empty:
                df_north = df_north.rename(columns={"north_money": "northbound_flow"})
                # Tushare 返回的 hsgt 字段类型可能是 str，需要转换
                if "northbound_flow" in df_north.columns:
                    df_north["northbound_flow"] = pd.to_numeric(df_north["northbound_flow"], errors="coerce")
                cap_flow = raw.get("capital_flow_raw")
                if cap_flow is not None and "northbound_flow" in df_north.columns:
                    raw["capital_flow_raw"] = cap_flow.merge(
                        df_north[["trade_date", "northbound_flow"]], on="trade_date", how="left"
                    )
        except Exception as e:
            logger.warning(f"[Tushare] 北向资金数据拉取失败: {e}")
    else:
        raw["capital_flow_raw"] = None

    # 4. 融资融券
    try:
        df_margin = pro.margin_detail(ts_code=stock_code, start_date=start_date, end_date=end_date)
        if df_margin is not None and not df_margin.empty:
            df_margin = df_margin.rename(columns={
                "rzye": "margin_balance",
                "rqmcl": "short_balance",
            })
            df_margin = df_margin.sort_values("trade_date").reset_index(drop=True)
            # 计算融资占流通市值比
            daily_basic = raw.get("daily_basic")
            if daily_basic is not None and "circ_mv" in daily_basic.columns:
                df_basic_mv = daily_basic[["trade_date", "circ_mv"]]
                df_margin = df_margin.merge(df_basic_mv, on="trade_date", how="left")
                df_margin["margin_ratio"] = (
                    df_margin["margin_balance"] / (df_margin["circ_mv"] * 10000) * 100
                )
            else:
                df_margin["margin_ratio"] = None
            # 5日融资余额变化率
            if "margin_balance" in df_margin.columns:
                df_margin["margin_change_5d"] = df_margin["margin_balance"].pct_change(5) * 100
            raw["margin_raw"] = df_margin
            available.add("microstructure_tool")
        else:
            raw["margin_raw"] = None
    except Exception as e:
        logger.warning(f"[Tushare] 融资融券数据拉取失败 {stock_code}: {e}")
        raw["margin_raw"] = None

    # 5. 龙虎榜（最近交易日）
    try:
        df_dt = pro.top_list(trade_date=end_date, ts_code=stock_code)
        if df_dt is not None and not df_dt.empty:
            raw["dragon_tiger_raw"] = df_dt.head(30).to_dict("records")
            available.add("microstructure_tool")
        else:
            raw["dragon_tiger_raw"] = None
    except Exception as e:
        logger.warning(f"[Tushare] 龙虎榜数据拉取失败 {stock_code}: {e}")
        raw["dragon_tiger_raw"] = None

    # 6. 公司公告（disclosure 接口）
    try:
        df_ann = pro.disclosure(ts_code=stock_code, start_date=_days_ago(60), end_date=end_date)
        if df_ann is not None and not df_ann.empty:
            cols = [c for c in ["ann_date", "title", "url"] if c in df_ann.columns]
            raw["news_raw"] = df_ann[cols].head(50).to_dict(orient="records")
            available.update(["news_tool"])
        else:
            raw["news_raw"] = []
    except Exception as e:
        logger.debug(f"[Tushare] 公司公告接口不可用 {stock_code}: {e}")
        raw["news_raw"] = []

    # 7. 板块/概念分类
    try:
        df_concept = pro.concept_detail(ts_code=stock_code)
        if df_concept is not None and not df_concept.empty:
            raw["sector_raw"] = {
                "concepts": df_concept["concept_name"].tolist() if "concept_name" in df_concept.columns else [],
            }
        else:
            raw["sector_raw"] = {"concepts": []}
        available.update(["sector_tool"])
    except Exception as e:
        logger.warning(f"[Tushare] 板块概念数据拉取失败 {stock_code}: {e}")
        raw["sector_raw"] = {"concepts": []}

    # 8. 停牌信息
    try:
        df_suspend = pro.suspend_d(ts_code=stock_code, start_date=_days_ago(10), end_date=end_date)
        if df_suspend is not None and not df_suspend.empty:
            today_str = _today()
            today_row = df_suspend[df_suspend["trade_date"] == today_str]
            raw["suspend_info"] = {
                "is_suspended": not today_row.empty,
                "suspend_type": str(today_row.iloc[0].get("suspend_type", "")) if not today_row.empty else None,
                "recent_suspends": df_suspend.to_dict("records"),
            }
        else:
            raw["suspend_info"] = {"is_suspended": False}
    except Exception as e:
        logger.warning(f"[Tushare] 停牌信息拉取失败 {stock_code}: {e}")
        raw["suspend_info"] = {"is_suspended": False}

    # 9. 市场情绪（全市场涨跌停统计）
    try:
        df_limit = pro.limit_list_d(start_date=_days_ago(15), end_date=end_date,
                                    fields="trade_date,ts_code,limit")
        if df_limit is not None and not df_limit.empty:
            sentiment_data = []
            for date, grp in df_limit.groupby("trade_date"):
                up_count = (grp["limit"] == "U").sum() if "limit" in grp.columns else 0
                down_count = (grp["limit"] == "D").sum() if "limit" in grp.columns else 0
                sentiment_data.append({
                    "trade_date": date,
                    "limit_up_count": int(up_count),
                    "limit_down_count": int(down_count),
                })
            raw["market_sentiment_raw"] = pd.DataFrame(sentiment_data).sort_values("trade_date")
            available.update(["sentiment_tool"])
        else:
            raw["market_sentiment_raw"] = None
    except Exception as e:
        logger.warning(f"[Tushare] 涨跌停统计数据拉取失败: {e}")
        raw["market_sentiment_raw"] = None

    # 10. 财务三表（扩展字段版）
    # 利润表 + 现金流量表 + 资产负债表，report_type='1' 取合并报表，最新 8 期
    _financial: dict = {}
    try:
        _income_fields = (
            "ts_code,end_date,report_type,revenue,n_income,n_income_attr_p,"
            "oper_cost,sell_exp,admin_exp,fin_exp,operate_profit,ebit"
        )
        df_income = pro.income(ts_code=stock_code, fields=_income_fields)
        if df_income is not None and not df_income.empty:
            if "report_type" in df_income.columns:
                df_income = df_income[df_income["report_type"] == "1"]
            df_income = df_income.sort_values(by="end_date", ascending=False).reset_index(drop=True)
            _financial["income"] = df_income.head(8).to_dict(orient="records")
    except Exception as e:
        logger.warning(f"[Tushare] 利润表数据拉取失败 {stock_code}: {e}")

    try:
        _cashflow_fields = (
            "ts_code,end_date,report_type,n_cashflow_act,free_cashflow,"
            "c_pay_acq_const_fiolta,c_cash_equ_end_period,n_cashflow_inv_act"
        )
        df_cashflow = pro.cashflow(ts_code=stock_code, fields=_cashflow_fields)
        if df_cashflow is not None and not df_cashflow.empty:
            if "report_type" in df_cashflow.columns:
                df_cashflow = df_cashflow[df_cashflow["report_type"] == "1"]
            df_cashflow = df_cashflow.sort_values(by="end_date", ascending=False).reset_index(drop=True)
            _financial["cashflow"] = df_cashflow.head(8).to_dict(orient="records")
    except Exception as e:
        logger.warning(f"[Tushare] 现金流量表数据拉取失败 {stock_code}: {e}")

    try:
        _bs_fields = (
            "ts_code,end_date,report_type,total_assets,total_liab,total_cur_assets,"
            "total_hldr_eqy_exc_min_int,goodwill,money_cap,st_borr,"
            "accounts_receiv,inventories"
        )
        df_bs = pro.balancesheet(ts_code=stock_code, fields=_bs_fields)
        if df_bs is not None and not df_bs.empty:
            if "report_type" in df_bs.columns:
                df_bs = df_bs[df_bs["report_type"] == "1"]
            df_bs = df_bs.sort_values(by="end_date", ascending=False).reset_index(drop=True)
            _financial["balancesheet"] = df_bs.head(8).to_dict(orient="records")
    except Exception as e:
        logger.warning(f"[Tushare] 资产负债表数据拉取失败 {stock_code}: {e}")

    raw["financial_raw"] = _financial if _financial else None

    # 11. 财务指标预算接口（fina_indicator）
    # Tushare 已预算好的 ROE / 毛利率 / 增速 / 流动比率等，优先作为 fundamental_tool 主数据源。
    # 若此接口失败（返回空/积分不足），financial_raw 不含 'fina_indicator' 键，
    # calculator.py 将自动从三表原始数据本地计算（fallback）。
    # AkShare 预留：未来可在此处增加 akshare_adapter.fetch_fina_indicator() 兜底。
    try:
        _fi_fields = (
            "ts_code,ann_date,end_date,"
            "roe,roa,grossprofit_margin,netprofit_margin,"
            "current_ratio,quick_ratio,debt_to_assets,"
            "tr_yoy,netprofit_yoy,or_yoy,"
            "assets_turn,arturn_days,invturn_days,"
            "ocf_to_profit,ocf_to_debt,"
            "fcff,fcfe,"
            "bps,ocfps,ebitda"
        )
        df_fi = pro.fina_indicator(ts_code=stock_code, fields=_fi_fields)
        if df_fi is not None and not df_fi.empty:
            df_fi = df_fi.sort_values(by="end_date", ascending=False).reset_index(drop=True)
            fin_raw = raw.get("financial_raw")
            if fin_raw is None:
                fin_raw = {}
                raw["financial_raw"] = fin_raw
            fin_raw["fina_indicator"] = df_fi.head(8).to_dict(orient="records")
            available.update(["fundamental_tool"])
    except Exception as e:
        logger.warning(f"[Tushare] 财务指标(fina_indicator)数据拉取失败 {stock_code}: {e}")

    # 12. 分红历史（近 10 次）
    try:
        df_div = pro.dividend(
            ts_code=stock_code,
            fields="ts_code,end_date,ann_date,record_date,ex_date,cash_div_tax,stk_div"
        )
        if df_div is not None and not df_div.empty:
            df_div = df_div.sort_values(by="end_date", ascending=False).reset_index(drop=True)
            raw["dividend_raw"] = df_div.head(10).to_dict(orient="records")
        else:
            raw["dividend_raw"] = []
    except Exception as e:
        logger.warning(f"[Tushare] 分红数据拉取失败 {stock_code}: {e}")
        raw["dividend_raw"] = []

    # 13-15. 股东结构数据（仅 A 股）
    # shareholder_raw = {
    #   "holder_num":  list[dict],  # 股东人数趋势（最近 8 期）
    #   "pledge":      list[dict],  # 质押统计（最近 4 期）
    #   "repurchase":  list[dict],  # 回购记录（最近 10 条）
    # }
    if stock_code.upper().endswith((".SZ", ".SH")):
        _shareholder: dict = {}

        # 13. 股东人数趋势
        try:
            df_hold_num = pro.stk_holdernumber(ts_code=stock_code)
            if df_hold_num is not None and not df_hold_num.empty:
                df_hold_num = df_hold_num.sort_values("end_date", ascending=False).reset_index(drop=True)
                _shareholder["holder_num"] = df_hold_num.head(8).to_dict("records")
        except Exception as e:
            logger.warning(f"[Tushare] 股东人数数据拉取失败 {stock_code}: {e}")

        # 14. 股权质押统计
        try:
            df_pledge = pro.pledge_stat(ts_code=stock_code)
            if df_pledge is not None and not df_pledge.empty:
                df_pledge = df_pledge.sort_values("end_date", ascending=False).reset_index(drop=True)
                _shareholder["pledge"] = df_pledge.head(4).to_dict("records")
        except Exception as e:
            logger.warning(f"[Tushare] 质押统计数据拉取失败 {stock_code}: {e}")

        # 15. 股票回购记录
        try:
            df_repurchase = pro.repurchase(ts_code=stock_code)
            if df_repurchase is not None and not df_repurchase.empty:
                df_repurchase = df_repurchase.sort_values("ann_date", ascending=False).reset_index(drop=True)
                _shareholder["repurchase"] = df_repurchase.head(10).to_dict("records")
        except Exception as e:
            logger.warning(f"[Tushare] 回购数据拉取失败 {stock_code}: {e}")

        if _shareholder:
            raw["shareholder_raw"] = _shareholder
            available.add("fundamental_tool")
        else:
            raw["shareholder_raw"] = None
    else:
        raw["shareholder_raw"] = None

    raw["metadata"]["available_count"] = len(available)
    logger.info(f"[Tushare] {stock_code} 数据拉取完成，可用工具: {available}")
    return raw, available
