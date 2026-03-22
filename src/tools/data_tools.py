from __future__ import annotations

"""
11 个数据工具函数。
每个函数输入 CalculatedDataPacket（或特殊参数），输出格式化 Markdown 字符串。
包含元信息头部（来源/时间/质量等级）。
缺失字段返回标准占位符字符串。
"""

import pandas as pd

from src.data.calculator import CalculatedDataPacket

_NA_PLACEHOLDER = "[{tool}未激活：{reason}，本维度数据不可用，分析时请标注N/A]"


def _na(tool: str, reason: str) -> str:
    return _NA_PLACEHOLDER.format(tool=tool, reason=reason)


def _meta_header(packet: CalculatedDataPacket) -> str:
    meta = packet.metadata
    return (
        f"**数据来源**：{meta.get('source', 'unknown')} | "
        f"**截止日期**：{meta.get('date', 'N/A')} | "
        f"**质量等级**：{meta.get('quality_level', 'N/A')} | "
        f"**标的代码**：{meta.get('stock_code', 'N/A')}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# 1. price_tool
# ──────────────────────────────────────────────────────────────────────────────

def price_tool(packet: CalculatedDataPacket, window: int = 250) -> str:
    """
    技术分析师：250日前复权OHLCV序列 + 近20日异常事件。
    """
    if packet.price_series is None or packet.price_series.empty:
        return _na("price_tool", "行情数据不可用")

    df = packet.price_series.tail(window)
    lines = [f"## 行情数据（前复权，近{len(df)}个交易日）\n", _meta_header(packet), ""]

    # OHLCV 表格（最近20行展示，完整数据供参考）
    display = df.tail(20)
    lines.append("### 最近20日OHLCV（前复权）")
    lines.append("| 日期 | 开盘(adj) | 最高(adj) | 最低(adj) | 收盘(adj) | 成交量 | 涨跌幅% |")
    lines.append("|-----|---------|---------|---------|---------|------|-------|")
    for _, row in display.iterrows():
        date = str(row.get("trade_date", ""))
        o_val = f"{row.get('open_adj', 0):.2f}" if pd.notna(row.get("open_adj")) else "N/A"
        h_val = f"{row.get('high_adj', 0):.2f}" if pd.notna(row.get("high_adj")) else "N/A"
        l_val = f"{row.get('low_adj', 0):.2f}" if pd.notna(row.get("low_adj")) else "N/A"
        c_val = f"{row.get('close_adj', 0):.2f}" if pd.notna(row.get("close_adj")) else "N/A"
        v_val = f"{row.get('vol', 0):.0f}" if pd.notna(row.get("vol")) else "N/A"
        p_val = f"{row.get('pct_chg', 0):.2f}" if pd.notna(row.get("pct_chg")) else "N/A"
        lines.append(f"| {date} | {o_val} | {h_val} | {l_val} | {c_val} | {v_val} | {p_val} |")

    lines.append(f"\n*完整历史：共{len(df)}条记录（{df.iloc[0].get('trade_date','?')} ~ {df.iloc[-1].get('trade_date','?')}）*")

    # 近20日异常事件
    anomalies = [a for a in (packet.anomalies or []) if a.get("date", "") >= str(df.iloc[-20].get("trade_date", ""))]
    if anomalies:
        lines.append("\n### 近20日异常事件")
        for a in anomalies[-20:]:
            tag_map = {
                "limit_up": "涨停", "limit_down": "跌停",
                "one_word_limit_up": "一字涨停", "one_word_limit_down": "一字跌停",
                "volume_surge": "成交量异常", "gap_open": "跳空缺口",
            }
            tag = tag_map.get(a.get("type", ""), a.get("type", ""))
            detail = ""
            if "pct_chg" in a:
                detail = f"涨跌幅：{a['pct_chg']:.2f}%"
            elif "vol_ratio" in a:
                detail = f"量比：{a['vol_ratio']}x"
            elif "gap_pct" in a:
                detail = f"跳空：{a['gap_pct']:.2f}%"
            lines.append(f"- {a.get('date', '')}: **{tag}** {detail}")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 2. indicator_tool
# ──────────────────────────────────────────────────────────────────────────────

def indicator_tool(packet: CalculatedDataPacket) -> str:
    """技术分析师：全量计算好的技术指标值。"""
    if not packet.macd and not packet.rsi and not packet.kdj:
        return _na("indicator_tool", "技术指标未计算（行情数据不足）")

    lines = ["## 技术指标（最新计算值）\n", _meta_header(packet), ""]

    def _fmt(v) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "N/A"
        if isinstance(v, float):
            return f"{v:.4f}"
        return str(v)

    # MACD
    m = packet.macd
    lines.append("### MACD（12-26-9）")
    lines.append(f"| DIF | DEA | MACD柱 | 信号 | 最近金叉/死叉距今天数 |")
    lines.append(f"|-----|-----|-------|------|---------------------|")
    lines.append(
        f"| {_fmt(m.get('macd_dif'))} | {_fmt(m.get('macd_dea'))} "
        f"| {_fmt(m.get('macd_bar'))} | {m.get('macd_signal','N/A')} "
        f"| {_fmt(m.get('macd_cross_days'))} |"
    )

    # RSI
    r = packet.rsi
    lines.append("\n### RSI（14）")
    lines.append(f"| RSI14 | 信号 | 背离 |")
    lines.append(f"|-------|------|------|")
    lines.append(f"| {_fmt(r.get('rsi_14'))} | {r.get('rsi_signal','N/A')} | {r.get('rsi_divergence','N/A')} |")

    # KDJ
    k = packet.kdj
    lines.append("\n### KDJ（9-3-3）")
    lines.append(f"| K | D | J | 信号 |")
    lines.append(f"|---|---|---|------|")
    lines.append(f"| {_fmt(k.get('kdj_k'))} | {_fmt(k.get('kdj_d'))} | {_fmt(k.get('kdj_j'))} | {k.get('kdj_signal','N/A')} |")

    # 布林带
    b = packet.bollinger
    lines.append("\n### 布林带（20-2）")
    lines.append(f"| 上轨 | 中轨 | 下轨 | 带宽 | 位置% | 信号 |")
    lines.append(f"|------|------|------|------|-------|------|")
    lines.append(
        f"| {_fmt(b.get('bb_upper'))} | {_fmt(b.get('bb_middle'))} "
        f"| {_fmt(b.get('bb_lower'))} | {_fmt(b.get('bb_bandwidth'))} "
        f"| {_fmt(b.get('bb_position'))} | {b.get('bb_signal','N/A')} |"
    )

    # 均线系统
    ma = packet.ma_system
    lines.append("\n### 均线系统")
    ma_vals = [(f"MA{p}", ma.get(f"ma_{p}")) for p in [5, 10, 20, 60, 120, 250]]
    lines.append("| " + " | ".join(n for n, _ in ma_vals) + " |")
    lines.append("| " + " | ".join("-----" for _ in ma_vals) + " |")
    lines.append("| " + " | ".join(_fmt(v) for _, v in ma_vals) + " |")
    lines.append(f"- 多头排列：{'是' if ma.get('ma_bullish_arrange') else '否'}  "
                 f"空头排列：{'是' if ma.get('ma_bearish_arrange') else '否'}")
    lines.append(f"- 价格偏离MA20：{_fmt(ma.get('price_vs_ma20'))}%  偏离MA60：{_fmt(ma.get('price_vs_ma60'))}%")
    cross_info = []
    for key in ["ma_cross_5_20", "ma_cross_10_60", "ma_cross_20_60"]:
        if ma.get(key):
            cross_info.append(f"{key.replace('ma_cross_','').replace('_','/')}均线：{ma[key]}")
    if cross_info:
        lines.append(f"- 近期均线交叉：{', '.join(cross_info)}")

    # 成交量指标
    vi = packet.volume_indicators
    lines.append("\n### 成交量指标")
    lines.append(f"- 量比（当日成交量/5日均量）：{_fmt(vi.get('volume_ratio'))}")
    lines.append(f"- 振幅（今日）：{_fmt(vi.get('amplitude'))}%")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 3. fundamental_tool
# ──────────────────────────────────────────────────────────────────────────────

def fundamental_tool(packet: CalculatedDataPacket) -> str:
    """
    基本面分析师：
    - 估值指标（PE/PB/PS/股息率/市值）
    - 财务核心指标（ROE/毛利率/净利率/负债率/流动比率/增速/现金流/FCF）
    - 资产负债表摘要（总资产/净资产/商誉/货币资金）
    - 利润表明细（营收/净利润/营业成本/三费）
    - 现金流量表摘要
    - 分红历史
    - 近60日PE/PB估值趋势

    数据优先级：financial_indicators（来自 fina_indicator API 或本地 fallback）
    """
    if packet.daily_basic is None or packet.daily_basic.empty:
        return _na("fundamental_tool", "每日基本面数据不可用")

    df = packet.daily_basic
    lines = ["## 基本面数据\n", _meta_header(packet), ""]

    def _v(val, fmt="{:.2f}", suffix=""):
        """安全格式化数值，None/NaN 返回 N/A。"""
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return "N/A"
        try:
            return fmt.format(float(val)) + suffix
        except (TypeError, ValueError):
            return "N/A"

    # ── 1. 最新估值指标 ─────────────────────────────────────────────────────────
    last = df.iloc[-1]
    lines.append("### 最新估值指标")
    lines.append("| PE(TTM) | PB(MRQ) | PS(TTM) | 股息率 | 换手率 | 流通市值(亿) | 总市值(亿) |")
    lines.append("|---------|---------|---------|-------|-------|-----------|---------|")
    pe = _v(last.get("pe_ttm"))
    pb = _v(last.get("pb_mrq"))
    ps = _v(last.get("ps_ttm"))
    dv = _v(last.get("dividend_yield"), suffix="%")
    tr = _v(last.get("turnover_rate"), suffix="%")
    cm = f"{float(last['circ_mv'])/10000:.2f}" if pd.notna(last.get("circ_mv")) else "N/A"
    tm = f"{float(last['total_mv'])/10000:.2f}" if pd.notna(last.get("total_mv")) else "N/A"
    lines.append(f"| {pe} | {pb} | {ps} | {dv} | {tr} | {cm} | {tm} |")

    val = packet.value
    if val:
        lines.append(f"\n**价值因子评分**：{val.get('value_score', 'N/A')}/100")

    # ── 2. 财务核心指标（来自 financial_indicators）────────────────────────────
    fi = packet.financial_indicators
    if fi:
        end_date_str = fi.get("end_date", "最新期")
        lines.append(f"\n### 财务核心指标（报告期：{end_date_str}）")
        lines.append("| 指标 | 数值 | 参考标准 |")
        lines.append("|-----|------|---------|")

        def _fi(key, fmt="{:.2f}", suffix=""):
            v = fi.get(key)
            return _v(v, fmt, suffix)

        lines.append(f"| ROE（净资产收益率） | {_fi('roe', suffix='%')} | >15% 优秀，>20% 卓越 |")
        lines.append(f"| ROA（总资产收益率） | {_fi('roa', suffix='%')} | >5% 良好 |")
        lines.append(f"| 毛利率 | {_fi('grossprofit_margin', suffix='%')} | 越高竞争壁垒越强 |")
        lines.append(f"| 净利率 | {_fi('netprofit_margin', suffix='%')} | 反映费用控制水平 |")
        lines.append(f"| 资产负债率 | {_fi('debt_to_assets', suffix='%')} | <40% 稳健，>70% 高风险 |")
        lines.append(f"| 流动比率 | {_fi('current_ratio')} | >2 健康，<1 警惕 |")
        lines.append(f"| 速动比率 | {_fi('quick_ratio')} | >1 安全 |")
        lines.append(f"| 总资产周转率 | {_fi('assets_turn', '{:.3f}')} | 越高运营效率越高 |")
        lines.append(f"| 应收账款周转天数 | {_fi('arturn_days', '{:.1f}', '天')} | 越短回款越快 |")
        lines.append(f"| 存货周转天数 | {_fi('invturn_days', '{:.1f}', '天')} | 越短越好 |")

        lines.append("\n### 成长性指标（同比）")
        lines.append("| 指标 | 数值 |")
        lines.append("|-----|------|")
        lines.append(f"| 营收同比增速 | {_fi('tr_yoy', suffix='%')} |")
        lines.append(f"| 净利润同比增速 | {_fi('netprofit_yoy', suffix='%')} |")
        lines.append(f"| 营业收入同比增速(or_yoy) | {_fi('or_yoy', suffix='%')} |")

        lines.append("\n### 现金流质量")
        ocf_ratio_v = fi.get("ocf_to_profit")
        if ocf_ratio_v is not None:
            try:
                ocf_f = float(ocf_ratio_v)
                if ocf_f >= 0.8:
                    quality = "✅ 优质（利润含金量高）"
                elif ocf_f >= 0.5:
                    quality = "⚠️ 一般"
                else:
                    quality = "❌ 偏低（利润含金量存疑）"
            except (TypeError, ValueError):
                quality = ""
        else:
            quality = ""
        lines.append(f"- **经营现金流/净利润（含金量）**：{_fi('ocf_to_profit')} {quality}")

        fcff_v = fi.get("fcff")
        if fcff_v is not None:
            try:
                lines.append(f"- **企业自由现金流（FCF）**：{float(fcff_v)/1e8:.2f} 亿元")
            except (TypeError, ValueError):
                lines.append(f"- **企业自由现金流（FCF）**：N/A")
        else:
            lines.append("- **企业自由现金流（FCF）**：N/A")

        ebitda_v = fi.get("ebitda")
        if ebitda_v is not None:
            try:
                lines.append(f"- **EBITDA**：{float(ebitda_v)/1e8:.2f} 亿元")
            except (TypeError, ValueError):
                pass

        lines.append(f"\n- **每股净资产（BPS）**：{_fi('bps')} 元")
        lines.append(f"- **每股经营现金流（OCFPS）**：{_fi('ocfps')} 元")
    else:
        lines.append("\n*财务核心指标暂不可用（fina_indicator 接口未返回数据，本地计算亦失败）*")

    # ── 3. 资产负债表摘要 ─────────────────────────────────────────────────────
    fin_raw = packet.financial_raw or {}
    bs_list = fin_raw.get("balancesheet", [])
    if bs_list:
        bs = bs_list[0]

        def _yuan(v):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return "N/A"
            try:
                return f"{float(v)/1e8:.2f}亿"
            except (TypeError, ValueError):
                return "N/A"

        lines.append(f"\n### 资产负债表摘要（{bs.get('end_date', '最新期')}）")
        total_assets = bs.get("total_assets")
        net_equity = bs.get("total_hldr_eqy_exc_min_int")
        total_liab = bs.get("total_liab")
        goodwill = bs.get("goodwill")
        money_cap = bs.get("money_cap")
        st_borr = bs.get("st_borr")

        lines.append(f"- **总资产**：{_yuan(total_assets)}")
        lines.append(f"- **总负债**：{_yuan(total_liab)}")
        lines.append(f"- **净资产（归母）**：{_yuan(net_equity)}")
        lines.append(f"- **货币资金**：{_yuan(money_cap)}")
        lines.append(f"- **短期借款**：{_yuan(st_borr)}")

        if goodwill and net_equity:
            try:
                g_f, e_f = float(goodwill), float(net_equity)
                if e_f > 0:
                    ratio = g_f / e_f * 100
                    flag = " ⚠️ 商誉占比过高，减值风险大" if ratio > 30 else ""
                    lines.append(f"- **商誉**：{_yuan(goodwill)}（占净资产 {ratio:.1f}%）{flag}")
                else:
                    lines.append(f"- **商誉**：{_yuan(goodwill)}")
            except (TypeError, ValueError):
                lines.append(f"- **商誉**：{_yuan(goodwill)}")
        elif goodwill:
            lines.append(f"- **商誉**：{_yuan(goodwill)}")

    # ── 4. 利润表明细 ─────────────────────────────────────────────────────────
    income_list = fin_raw.get("income", [])
    if income_list:
        lines.append("\n### 利润表明细（近2期）")
        lines.append("| 报告期 | 营收(亿) | 净利润(亿) | 毛利率 | 三费合计(亿) | 营业利润(亿) |")
        lines.append("|-------|---------|----------|-------|-----------|-----------|")
        for rec in income_list[:2]:
            ed = rec.get("end_date", "")
            rev = rec.get("revenue")
            ni_a = rec.get("n_income_attr_p") or rec.get("n_income")
            oc = rec.get("oper_cost")
            s_exp = rec.get("sell_exp") or 0
            a_exp = rec.get("admin_exp") or 0
            f_exp = rec.get("fin_exp") or 0
            op = rec.get("operate_profit")

            rev_s = f"{float(rev)/1e8:.2f}" if rev else "N/A"
            ni_s = f"{float(ni_a)/1e8:.2f}" if ni_a else "N/A"
            try:
                gm_s = f"{(float(rev)-float(oc))/float(rev)*100:.1f}%" if rev and oc and float(rev) > 0 else "N/A"
            except (TypeError, ValueError):
                gm_s = "N/A"
            try:
                fee_s = f"{(float(s_exp)+float(a_exp)+float(f_exp))/1e8:.2f}"
            except (TypeError, ValueError):
                fee_s = "N/A"
            op_s = f"{float(op)/1e8:.2f}" if op else "N/A"
            lines.append(f"| {ed} | {rev_s} | {ni_s} | {gm_s} | {fee_s} | {op_s} |")

    # ── 5. 现金流量表摘要 ─────────────────────────────────────────────────────
    cf_list = fin_raw.get("cashflow", [])
    if cf_list:
        lines.append("\n### 现金流量表摘要（近2期）")
        lines.append("| 报告期 | 经营活动净现金流(亿) | 自由现金流(亿) | 资本支出(亿) | 期末现金(亿) |")
        lines.append("|-------|-----------------|------------|-----------|-----------|")
        for rec in cf_list[:2]:
            ed = rec.get("end_date", "")
            ocf_v = rec.get("n_cashflow_act")
            fcf_v = rec.get("free_cashflow")
            capex_v = rec.get("c_pay_acq_const_fiolta")
            end_cash_v = rec.get("c_cash_equ_end_period")

            def _cf_fmt(v):
                return f"{float(v)/1e8:.2f}" if v else "N/A"

            lines.append(f"| {ed} | {_cf_fmt(ocf_v)} | {_cf_fmt(fcf_v)} | {_cf_fmt(capex_v)} | {_cf_fmt(end_cash_v)} |")

    # ── 6. 分红历史 ───────────────────────────────────────────────────────────
    div_list = packet.dividend_raw or []
    if div_list:
        lines.append("\n### 分红历史（近5次）")
        lines.append("| 报告年度 | 每股现金分红(税后/元) | 送股比例 | 权益登记日 | 除权除息日 |")
        lines.append("|---------|------------------|--------|---------|---------|")
        for d in div_list[:5]:
            ed = d.get("end_date", "")
            cdtax = d.get("cash_div_tax")
            stk = d.get("stk_div", 0)
            rec_date = d.get("record_date", "N/A")
            ex_date = d.get("ex_date", "N/A")
            cdtax_s = f"{float(cdtax):.4f}" if cdtax else "N/A"
            stk_s = f"{float(stk):.4f}" if stk else "0"
            lines.append(f"| {ed} | {cdtax_s} | {stk_s} | {rec_date} | {ex_date} |")
    else:
        lines.append("\n*暂无分红记录*")

    # ── 7. 近60日PE/PB估值趋势 ───────────────────────────────────────────────
    recent_60 = df.tail(60)
    if len(recent_60) >= 2:
        lines.append("\n### 近60日PE/PB估值趋势（每6日取样）")
        lines.append("| 日期 | PE(TTM) | PB(MRQ) |")
        lines.append("|-----|---------|---------|")
        sample = recent_60.iloc[::6]
        for _, row in sample.iterrows():
            d_str = str(row.get("trade_date", ""))
            pe_v = f"{float(row.get('pe_ttm',0)):.1f}" if pd.notna(row.get("pe_ttm")) else "N/A"
            pb_v = f"{float(row.get('pb_mrq',0)):.2f}" if pd.notna(row.get("pb_mrq")) else "N/A"
            lines.append(f"| {d_str} | {pe_v} | {pb_v} |")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 4. capital_flow_tool
# ──────────────────────────────────────────────────────────────────────────────

def capital_flow_tool(packet: CalculatedDataPacket) -> str:
    """市场微观结构分析师：资金流向指标（近10日）。"""
    cf_df = packet.capital_flow_raw
    cf_calc = packet.capital_flow

    if (cf_df is None or cf_df.empty) and not cf_calc:
        return _na("capital_flow_tool", "资金流向数据不可用（数据源积分不足或非A股）")

    lines = ["## 资金流向数据\n", _meta_header(packet), ""]

    # 近10日明细
    if cf_df is not None and not cf_df.empty:
        recent = cf_df.tail(10)
        lines.append("### 近10日主力资金流向（万元）")
        lines.append("| 日期 | 主力净流入(今日) | 3日累计 | 5日累计 | 10日累计 | 散户净流入 | 北向资金 |")
        lines.append("|-----|--------------|--------|--------|---------|---------|---------|")
        def _f(v):
            if pd.isna(v) or v == "": return "N/A"
            try: return f"{float(v):.0f}"
            except (ValueError, TypeError): return "N/A"

        for _, row in recent.iterrows():
            d = str(row.get("trade_date", ""))
            mn = _f(row.get("main_net_today", 0))
            m3 = _f(row.get("main_net_3d", 0))
            m5 = _f(row.get("main_net_5d", 0))
            m10 = _f(row.get("main_net_10d", 0))
            rn = _f(row.get("retail_net_today", 0))
            nb = _f(row.get("northbound_flow", 0))
            lines.append(f"| {d} | {mn} | {m3} | {m5} | {m10} | {rn} | {nb} |")

    # 资金评分
    if cf_calc:
        lines.append(f"\n**资金流向评分**：{cf_calc.get('capital_score', 'N/A')}/100")
        lines.append(f"**资金信号**：{cf_calc.get('capital_signal', 'N/A')}")
        mr = cf_calc.get("main_ratio")
        if mr is not None:
            lines.append(f"**主力净流入占成交额比**：{mr:.2f}%")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 5. margin_tool
# ──────────────────────────────────────────────────────────────────────────────

def margin_tool(packet: CalculatedDataPacket) -> str:
    """市场微观结构分析师：融资融券数据（近10日）。"""
    if packet.margin_raw is None or packet.margin_raw.empty:
        return _na("margin_tool", "融资融券数据不可用（无融资资格或数据源不支持）")

    df = packet.margin_raw.tail(10)
    lines = ["## 融资融券数据\n", _meta_header(packet), ""]

    lines.append("### 近10日融资融券（注：数据延迟T+1）")
    lines.append("| 日期 | 融资余额(万元) | 融资占流通市值% | 5日变化率% | 融券余量 |")
    lines.append("|-----|------------|--------------|---------|---------|")
    for _, row in df.iterrows():
        d = str(row.get("trade_date", ""))
        mb = f"{row.get('margin_balance',0):.0f}" if pd.notna(row.get("margin_balance")) else "N/A"
        mr = f"{row.get('margin_ratio',0):.2f}" if pd.notna(row.get("margin_ratio")) else "N/A"
        mc = f"{row.get('margin_change_5d',0):.2f}" if pd.notna(row.get("margin_change_5d")) else "N/A"
        sb = f"{row.get('short_balance',0):.0f}" if pd.notna(row.get("short_balance")) else "N/A"
        lines.append(f"| {d} | {mb} | {mr} | {mc} | {sb} |")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 6. dragon_tiger_tool
# ──────────────────────────────────────────────────────────────────────────────

def dragon_tiger_tool(packet: CalculatedDataPacket) -> str:
    """市场微观结构分析师：最近3次龙虎榜记录。"""
    records = packet.dragon_tiger_raw
    if not records:
        return _na("dragon_tiger_tool", "龙虎榜数据不可用或近期未上榜")

    lines = ["## 龙虎榜数据\n", _meta_header(packet), ""]
    lines.append("### 最近龙虎榜记录（最多30条）")

    for rec in records[:30]:
        lines.append(f"- **{rec.get('trade_date', '?')}** | "
                     f"原因：{rec.get('reason', 'N/A')} | "
                     f"买入额：{rec.get('buy_amount', 0):.0f}万 | "
                     f"卖出额：{rec.get('sell_amount', 0):.0f}万 | "
                     f"净额：{rec.get('net_amount', 0):.0f}万")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 7. sentiment_tool
# ──────────────────────────────────────────────────────────────────────────────

def sentiment_tool(packet: CalculatedDataPacket) -> str:
    """情绪分析师 + 微观结构分析师：全市场涨跌停统计 + 北向资金（近10日）。"""
    sentiment_df = packet.market_sentiment_raw

    if sentiment_df is None or sentiment_df.empty:
        return _na("sentiment_tool", "市场情绪数据不可用")

    lines = ["## 市场情绪数据\n", _meta_header(packet), ""]

    recent = sentiment_df.tail(10)
    lines.append("### 近10日市场涨跌停统计")
    lines.append("| 日期 | 涨停家数 | 跌停家数 | 情绪信号 |")
    lines.append("|-----|---------|---------|---------|")
    for _, row in recent.iterrows():
        d = str(row.get("trade_date", ""))
        up = int(row.get("limit_up_count", 0))
        down = int(row.get("limit_down_count", 0))
        ratio = up / max(down, 1)
        if ratio >= 5:
            signal = "🔥极度乐观"
        elif ratio >= 2:
            signal = "📈偏多"
        elif ratio <= 0.5:
            signal = "📉偏空"
        elif ratio <= 0.2:
            signal = "❄️极度悲观"
        else:
            signal = "➡️中性"
        lines.append(f"| {d} | {up} | {down} | {signal} |")

    # 换手率信息（从 daily_basic 提取）
    if packet.daily_basic is not None and not packet.daily_basic.empty and "turnover_rate" in packet.daily_basic.columns:
        recent_basic = packet.daily_basic.tail(20)
        tr_avg = recent_basic["turnover_rate"].mean()
        tr_last = recent_basic.iloc[-1].get("turnover_rate")
        if pd.notna(tr_last) and pd.notna(tr_avg):
            vs = "高于" if float(tr_last) > float(tr_avg) else "低于"
            lines.append(f"\n**个股换手率（今日）**：{float(tr_last):.2f}%（20日均值：{float(tr_avg):.2f}%，{vs}均值）")

    # RSI 情绪参考
    rsi_val = packet.rsi.get("rsi_14")
    if rsi_val is not None:
        lines.append(f"**个股RSI14**：{float(rsi_val):.1f}（信号：{packet.rsi.get('rsi_signal', 'N/A')}）")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 8. sector_tool
# ──────────────────────────────────────────────────────────────────────────────

def sector_tool(packet: CalculatedDataPacket) -> str:
    """板块轮动分析师：概念/行业分类 + 动量因子。"""
    sector = packet.sector_raw
    mom = packet.momentum

    if not sector and not mom:
        return _na("sector_tool", "板块分类数据和动量数据均不可用")

    lines = ["## 板块轮动数据\n", _meta_header(packet), ""]

    # 概念/行业分类
    if sector:
        concepts = sector.get("concepts", [])
        if concepts:
            lines.append(f"**所属概念板块（{len(concepts)}个）**：")
            lines.append(", ".join(concepts[:20]))  # 最多显示20个
            if len(concepts) > 20:
                lines.append(f"*（另有{len(concepts)-20}个概念，已截断）*")
        else:
            lines.append("*板块分类数据为空*")

    # 动量因子
    if mom:
        lines.append("\n### 动量因子")
        lines.append(f"| 1个月动量 | 3个月动量 | 6个月动量 | 3M跳过1M | 动量评分 |")
        lines.append(f"|---------|---------|---------|---------|---------|")

        def _m(k) -> str:
            v = mom.get(k)
            return f"{v:.2f}%" if v is not None and pd.notna(v) else "N/A"

        lines.append(
            f"| {_m('mom_1m')} | {_m('mom_3m')} | {_m('mom_6m')} "
            f"| {_m('mom_3m_skip1m')} | {mom.get('momentum_score', 'N/A')}/100 |"
        )

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 9. news_tool
# ──────────────────────────────────────────────────────────────────────────────

def news_tool(packet: CalculatedDataPacket) -> str:
    """资讯事件分析师：近7日新闻 + 近30日公告标题 + 停牌信息。"""
    news = packet.news_raw
    if not news:
        return _na("news_tool", "公告/新闻数据不可用（数据源不支持或近期无公告）")

    lines = ["## 资讯事件数据\n", _meta_header(packet), ""]

    # 按日期分组：近7日 / 近30日
    from datetime import datetime, timedelta
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    recent_week = [n for n in news if str(n.get("ann_date", "")) >= week_ago]
    recent_month = [n for n in news if month_ago <= str(n.get("ann_date", "")) < week_ago]

    if recent_week:
        lines.append("### 近7日重要公告")
        for item in recent_week[:10]:
            lines.append(f"- **{item.get('ann_date', '')}** — {item.get('title', '')}")
    else:
        lines.append("### 近7日公告\n*近7日无公告*")

    if recent_month:
        lines.append("\n### 近30日公告（7日前）")
        for item in recent_month[:20]:
            lines.append(f"- {item.get('ann_date', '')} — {item.get('title', '')}")

    # 停牌记录提示
    if packet.is_suspended:
        lines.append("\n⚠️ **当前状态：停牌中**")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 10. snapshot_tool
# ──────────────────────────────────────────────────────────────────────────────

def snapshot_tool(packet: CalculatedDataPacket) -> str:
    """交易计划师 + 投资顾问：最新行情快照 + 关键技术支撑阻力位。"""
    if packet.price_series is None or packet.price_series.empty:
        return _na("snapshot_tool", "行情数据不可用，无法生成价格快照")

    df = packet.price_series
    last = df.iloc[-1]
    ma = packet.ma_system
    bb = packet.bollinger
    vol = packet.volume_indicators

    lines = ["## 行情快照\n", _meta_header(packet), ""]

    # 最新价格
    close = last.get("close_adj") or last.get("close", 0)
    pct = last.get("pct_chg", 0) or 0
    sign = "+" if float(pct) >= 0 else ""
    lines.append(f"### 当前价格")
    lines.append(f"- **最新收盘价**：{float(close):.2f} 元")
    lines.append(f"- **今日涨跌幅**：{sign}{float(pct):.2f}%")
    lines.append(f"- **日期**：{last.get('trade_date', 'N/A')}")

    # 近5日高低点
    recent_5 = df.tail(5)
    hi_5 = recent_5["high_adj"].max() if "high_adj" in recent_5.columns else recent_5.get("high", recent_5.get("close_adj", 0)).max()
    lo_5 = recent_5["low_adj"].min() if "low_adj" in recent_5.columns else recent_5.get("low", recent_5.get("close_adj", 0)).min()
    lines.append(f"- **近5日最高**：{float(hi_5):.2f} 元")
    lines.append(f"- **近5日最低**：{float(lo_5):.2f} 元")

    # 近20日日均振幅
    if "amplitude" in vol:
        lines.append(f"- **日均振幅（今日）**：{float(vol['amplitude']):.2f}%")

    # 关键技术支撑阻力位
    lines.append("\n### 关键技术位")

    # 支撑：MA20、MA60、布林下轨
    supports = []
    if ma.get("ma_20") and float(ma["ma_20"]) < float(close):
        supports.append(f"MA20={float(ma['ma_20']):.2f}")
    if ma.get("ma_60") and float(ma["ma_60"]) < float(close):
        supports.append(f"MA60={float(ma['ma_60']):.2f}")
    if bb.get("bb_lower") and float(bb["bb_lower"]) < float(close):
        supports.append(f"布林下轨={float(bb['bb_lower']):.2f}")

    # 阻力：MA20/60（若价格在均线下方）、布林上轨
    resistances = []
    if ma.get("ma_20") and float(ma["ma_20"]) > float(close):
        resistances.append(f"MA20={float(ma['ma_20']):.2f}")
    if ma.get("ma_60") and float(ma["ma_60"]) > float(close):
        resistances.append(f"MA60={float(ma['ma_60']):.2f}")
    if bb.get("bb_upper") and float(bb["bb_upper"]) > float(close):
        resistances.append(f"布林上轨={float(bb['bb_upper']):.2f}")

    lines.append(f"- **支撑位**：{', '.join(supports) if supports else 'N/A'}")
    lines.append(f"- **阻力位**：{', '.join(resistances) if resistances else 'N/A'}")
    lines.append(f"- **布林带中轨**：{float(bb['bb_middle']):.2f}" if bb.get("bb_middle") else "- **布林带中轨**：N/A")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 11. shareholder_tool（新增）
# ──────────────────────────────────────────────────────────────────────────────

def shareholder_tool(packet: CalculatedDataPacket) -> str:
    """
    基本面分析师 + 微观结构分析师：
    股东人数趋势（筹码集中度信号）+ 股权质押情况 + 近期回购记录。
    仅 A 股可用，港股/美股返回 N/A。
    """
    sh = packet.shareholder_raw
    if not sh:
        return _na("shareholder_tool", "股东结构数据不可用（非 A 股或数据源未返回）")

    lines = ["## 股东结构数据\n", _meta_header(packet), ""]

    # 1. 股东人数趋势
    holder_list = sh.get("holder_num", [])
    if holder_list:
        lines.append("### 股东人数趋势（筹码集中度信号）")
        lines.append(
            "*股东人数↓ → 筹码向少数人集中（机构建仓信号）；"
            "股东人数↑ → 筹码分散（散户追入或机构减仓信号）*\n"
        )
        lines.append("| 报告期 | 股东人数（户） | 较上期变化 |")
        lines.append("|-------|------------|---------|")
        for i, rec in enumerate(holder_list[:8]):
            ed = rec.get("end_date", "")
            num = rec.get("holder_num")
            num_s = f"{int(num):,}" if num is not None else "N/A"
            if i < len(holder_list) - 1:
                prev = holder_list[i + 1].get("holder_num")
                if num is not None and prev is not None and prev != 0:
                    chg_pct = (float(num) - float(prev)) / float(prev) * 100
                    chg_s = f"{'▲' if chg_pct > 0 else '▼'}{abs(chg_pct):.1f}%"
                else:
                    chg_s = "N/A"
            else:
                chg_s = "—（基准期）"
            lines.append(f"| {ed} | {num_s} | {chg_s} |")

    # 2. 股权质押情况
    pledge_list = sh.get("pledge", [])
    if pledge_list:
        lines.append("\n### 股权质押情况（最新期）")
        p = pledge_list[0]
        ratio = p.get("pledge_ratio")
        if ratio is not None:
            try:
                r_f = float(ratio)
                if r_f > 30:
                    flag = " ⚠️ **质押比例过高（>30%），存在强平风险**"
                elif r_f > 15:
                    flag = " ⚠️ 质押比例偏高，需持续关注"
                else:
                    flag = ""
                ratio_s = f"{r_f:.2f}%{flag}"
            except (TypeError, ValueError):
                ratio_s = str(ratio)
        else:
            ratio_s = "N/A"

        lines.append(f"- **报告期**：{p.get('end_date', 'N/A')}")
        lines.append(f"- **质押比例**：{ratio_s}")
        lines.append(f"- **质押笔数**：{p.get('pledge_count', 'N/A')}")
        unrest = p.get("unrest_pledge")
        rest = p.get("rest_pledge")
        if unrest is not None:
            try:
                lines.append(f"- **无限售股质押数量**：{float(unrest)/1e8:.4f} 亿股")
            except (TypeError, ValueError):
                pass
        if rest is not None:
            try:
                lines.append(f"- **限售股质押数量**：{float(rest)/1e8:.4f} 亿股")
            except (TypeError, ValueError):
                pass

        # 历史质押趋势（如有多期）
        if len(pledge_list) > 1:
            lines.append("\n**质押比例历史趋势**：")
            for pp in pledge_list[:4]:
                r = pp.get("pledge_ratio")
                r_s = f"{float(r):.2f}%" if r is not None else "N/A"
                lines.append(f"  - {pp.get('end_date', '')}: {r_s}")
    else:
        lines.append("\n*质押数据暂不可用*")

    # 3. 回购记录
    repurchase_list = sh.get("repurchase", [])
    if repurchase_list:
        lines.append("\n### 近期回购记录（最多5条）")
        lines.append("| 公告日 | 截止日 | 状态 | 成交量（万股） | 成交金额（万元） |")
        lines.append("|-------|-------|------|------------|-------------|")
        for rep in repurchase_list[:5]:
            ann = rep.get("ann_date", "")
            end = rep.get("end_date", "")
            proc = rep.get("proc", "N/A")
            vol = rep.get("vol")
            amt = rep.get("amount")
            vol_s = f"{float(vol)/10000:.2f}" if vol else "N/A"
            amt_s = f"{float(amt)/10000:.2f}" if amt else "N/A"
            lines.append(f"| {ann} | {end} | {proc} | {vol_s} | {amt_s} |")
    else:
        lines.append("\n*近期无回购记录*")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 12. risk_metric_tool
# ──────────────────────────────────────────────────────────────────────────────

def risk_metric_tool(risk_results: dict) -> str:
    """
    Stage 3 风控智能体专用工具。
    入参为 format_risk_results() 已生成的 Markdown 字符串（直接传入）。
    或传入 {"formatted": str} dict。
    """
    if not risk_results:
        return _na("risk_metric_tool", "风控计算结果不可用")

    if isinstance(risk_results, str):
        return risk_results

    formatted = risk_results.get("formatted", "")
    if not formatted:
        return _na("risk_metric_tool", "风控计算结果格式错误")

    return formatted
