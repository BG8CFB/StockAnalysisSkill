"""
基本面分析工具模块。
提供财务报表、估值指标和股东结构相关工具。
"""

from __future__ import annotations

import pandas as pd

from src.data.calculator import CalculatedDataPacket
from src.tools.base import _na, _meta_header


def _fmt_float(val, fmt="{:.2f}", suffix="") -> str:
    """安全格式化数值，None/NaN 返回 N/A。"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "N/A"
    try:
        return fmt.format(float(val)) + suffix
    except (TypeError, ValueError):
        return "N/A"


def _fmt_yuan(v):
    """格式化为亿元单位。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "N/A"
    try:
        return f"{float(v) / 1e8:.2f}亿"
    except (TypeError, ValueError):
        return "N/A"


def fundamental_tool(packet: CalculatedDataPacket) -> str:
    """
    基本面分析师：
    - 估值指标（PE/PB/PS/股息率/市值）
    - 财务核心指标（ROE/毛利率/净利率/负债率/流动比率/增速/现金流/FCF）
    - 资产负债表摘要
    - 利润表明细
    - 现金流量表摘要
    - 分红历史
    - 近60日PE/PB估值趋势
    """
    if packet.daily_basic is None or packet.daily_basic.empty:
        return _na("fundamental_tool", "每日基本面数据不可用")

    df = packet.daily_basic
    lines = ["## 基本面数据\n", _meta_header(packet), ""]

    # ── 1. 最新估值指标 ─────────────────────────────────────────────────────────
    last = df.iloc[-1]
    lines.append("### 最新估值指标")
    lines.append(
        "| PE(TTM) | PB(MRQ) | PS(TTM) | 股息率 | 换手率 | 流通市值(亿) | 总市值(亿) |"
    )
    lines.append(
        "|---------|---------|---------|-------|-------|-----------|---------|"
    )
    pe = _fmt_float(last.get("pe_ttm"))
    pb = _fmt_float(last.get("pb_mrq"))
    ps = _fmt_float(last.get("ps_ttm"))
    dv = _fmt_float(last.get("dividend_yield"), suffix="%")
    tr = _fmt_float(last.get("turnover_rate"), suffix="%")
    cm = (
        f"{float(last['circ_mv']) / 10000:.2f}"
        if pd.notna(last.get("circ_mv"))
        else "N/A"
    )
    tm = (
        f"{float(last['total_mv']) / 10000:.2f}"
        if pd.notna(last.get("total_mv"))
        else "N/A"
    )
    lines.append(f"| {pe} | {pb} | {ps} | {dv} | {tr} | {cm} | {tm} |")

    val = packet.value
    if val:
        lines.append(f"\n**价值因子评分**：{val.get('value_score', 'N/A')}/100")

    # ── 2. 财务核心指标 ─────────────────────────────────────────────────────────
    fi = packet.financial_indicators
    if fi:
        end_date_str = fi.get("end_date", "最新期")
        lines.append(f"\n### 财务核心指标（报告期：{end_date_str}）")
        lines.append("| 指标 | 数值 | 参考标准 |")
        lines.append("|-----|------|---------|")

        def _fi(key, fmt="{:.2f}", suffix=""):
            v = fi.get(key)
            return _fmt_float(v, fmt, suffix)

        lines.append(
            f"| ROE（净资产收益率） | {_fi('roe', suffix='%')} | >15% 优秀，>20% 卓越 |"
        )
        lines.append(f"| ROA（总资产收益率） | {_fi('roa', suffix='%')} | >5% 良好 |")
        lines.append(
            f"| 毛利率 | {_fi('grossprofit_margin', suffix='%')} | 越高竞争壁垒越强 |"
        )
        lines.append(
            f"| 净利率 | {_fi('netprofit_margin', suffix='%')} | 反映费用控制水平 |"
        )
        lines.append(
            f"| 资产负债率 | {_fi('debt_to_assets', suffix='%')} | <40% 稳健，>70% 高风险 |"
        )
        lines.append(f"| 流动比率 | {_fi('current_ratio')} | >2 健康，<1 警惕 |")
        lines.append(f"| 速动比率 | {_fi('quick_ratio')} | >1 安全 |")
        lines.append(
            f"| 总资产周转率 | {_fi('assets_turn', '{:.3f}')} | 越高运营效率越高 |"
        )
        lines.append(
            f"| 应收账款周转天数 | {_fi('arturn_days', '{:.1f}', '天')} | 越短回款越快 |"
        )
        lines.append(
            f"| 存货周转天数 | {_fi('invturn_days', '{:.1f}', '天')} | 越短越好 |"
        )

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
                    quality = "[OK] 优质（利润含金量高）"
                elif ocf_f >= 0.5:
                    quality = "[!] 一般"
                else:
                    quality = "[X] 偏低（利润含金量存疑）"
            except (TypeError, ValueError):
                quality = ""
        else:
            quality = ""
        lines.append(
            f"- **经营现金流/净利润（含金量）**：{_fi('ocf_to_profit')} {quality}"
        )

        fcff_v = fi.get("fcff")
        if fcff_v is not None:
            try:
                lines.append(
                    f"- **企业自由现金流（FCF）**：{float(fcff_v) / 1e8:.2f} 亿元"
                )
            except (TypeError, ValueError):
                lines.append("- **企业自由现金流（FCF）**：N/A")
        else:
            lines.append("- **企业自由现金流（FCF）**：N/A")

        ebitda_v = fi.get("ebitda")
        if ebitda_v is not None:
            try:
                lines.append(f"- **EBITDA**：{float(ebitda_v) / 1e8:.2f} 亿元")
            except (TypeError, ValueError):
                pass

        lines.append(f"\n- **每股净资产（BPS）**：{_fi('bps')} 元")
        lines.append(f"- **每股经营现金流（OCFPS）**：{_fi('ocfps')} 元")
    else:
        lines.append(
            "\n*财务核心指标暂不可用（fina_indicator 接口未返回数据，本地计算亦失败）*"
        )

    # ── 3. 资产负债表摘要 ─────────────────────────────────────────────────────
    fin_raw = packet.financial_raw or {}
    bs_list = fin_raw.get("balancesheet", [])
    if bs_list:
        bs = bs_list[0]
        lines.append(f"\n### 资产负债表摘要（{bs.get('end_date', '最新期')}）")
        total_assets = bs.get("total_assets")
        net_equity = bs.get("total_hldr_eqy_exc_min_int")
        total_liab = bs.get("total_liab")
        goodwill = bs.get("goodwill")
        money_cap = bs.get("money_cap")
        st_borr = bs.get("st_borr")

        lines.append(f"- **总资产**：{_fmt_yuan(total_assets)}")
        lines.append(f"- **总负债**：{_fmt_yuan(total_liab)}")
        lines.append(f"- **净资产（归母）**：{_fmt_yuan(net_equity)}")
        lines.append(f"- **货币资金**：{_fmt_yuan(money_cap)}")
        lines.append(f"- **短期借款**：{_fmt_yuan(st_borr)}")

        if goodwill and net_equity:
            try:
                g_f, e_f = float(goodwill), float(net_equity)
                if e_f > 0:
                    ratio = g_f / e_f * 100
                    flag = " [!] 商誉占比过高（>30%），减值风险大" if ratio > 30 else ""
                    lines.append(
                        f"- **商誉**：{_fmt_yuan(goodwill)}（占净资产 {ratio:.1f}%）{flag}"
                    )
                else:
                    lines.append(f"- **商誉**：{_fmt_yuan(goodwill)}")
            except (TypeError, ValueError):
                lines.append(f"- **商誉**：{_fmt_yuan(goodwill)}")
        elif goodwill:
            lines.append(f"- **商誉**：{_fmt_yuan(goodwill)}")

    # ── 4. 利润表明细 ─────────────────────────────────────────────────────────
    income_list = fin_raw.get("income", [])
    if income_list:
        lines.append("\n### 利润表明细（近2期）")
        lines.append(
            "| 报告期 | 营收(亿) | 净利润(亿) | 毛利率 | 三费合计(亿) | 营业利润(亿) |"
        )
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

            rev_s = f"{float(rev) / 1e8:.2f}" if rev else "N/A"
            ni_s = f"{float(ni_a) / 1e8:.2f}" if ni_a else "N/A"
            try:
                gm_s = (
                    f"{(float(rev) - float(oc)) / float(rev) * 100:.1f}%"
                    if rev and oc and float(rev) > 0
                    else "N/A"
                )
            except (TypeError, ValueError):
                gm_s = "N/A"
            try:
                fee_s = f"{(float(s_exp) + float(a_exp) + float(f_exp)) / 1e8:.2f}"
            except (TypeError, ValueError):
                fee_s = "N/A"
            op_s = f"{float(op) / 1e8:.2f}" if op else "N/A"
            lines.append(f"| {ed} | {rev_s} | {ni_s} | {gm_s} | {fee_s} | {op_s} |")

    # ── 5. 现金流量表摘要 ─────────────────────────────────────────────────────
    cf_list = fin_raw.get("cashflow", [])
    if cf_list:
        lines.append("\n### 现金流量表摘要（近2期）")
        lines.append(
            "| 报告期 | 经营活动净现金流(亿) | 自由现金流(亿) | 资本支出(亿) | 期末现金(亿) |"
        )
        lines.append("|-------|-----------------|------------|-----------|-----------|")
        for rec in cf_list[:2]:
            ed = rec.get("end_date", "")
            ocf_v = rec.get("n_cashflow_act")
            fcf_v = rec.get("free_cashflow")
            capex_v = rec.get("c_pay_acq_const_fiolta")
            end_cash_v = rec.get("c_cash_equ_end_period")

            def _cf_fmt(v):
                return f"{float(v) / 1e8:.2f}" if v else "N/A"

            lines.append(
                f"| {ed} | {_cf_fmt(ocf_v)} | {_cf_fmt(fcf_v)} | {_cf_fmt(capex_v)} | {_cf_fmt(end_cash_v)} |"
            )

    # ── 6. 分红历史 ───────────────────────────────────────────────────────────
    div_list = packet.dividend_raw or []
    if div_list:
        lines.append("\n### 分红历史（近5次）")
        lines.append(
            "| 报告年度 | 每股现金分红(税后/元) | 送股比例 | 权益登记日 | 除权除息日 |"
        )
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
            pe_v = (
                f"{float(row.get('pe_ttm', 0)):.1f}"
                if pd.notna(row.get("pe_ttm"))
                else "N/A"
            )
            pb_v = (
                f"{float(row.get('pb_mrq', 0)):.2f}"
                if pd.notna(row.get("pb_mrq"))
                else "N/A"
            )
            lines.append(f"| {d_str} | {pe_v} | {pb_v} |")

    return "\n".join(lines)


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
                    chg_s = f"{'+' if chg_pct > 0 else ''}{chg_pct:.1f}%"
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
                    flag = " [!] **质押比例过高（>30%），存在强平风险**"
                elif r_f > 15:
                    flag = " [!] 质押比例偏高，需持续关注"
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
                lines.append(f"- **无限售股质押数量**：{float(unrest) / 1e8:.4f} 亿股")
            except (TypeError, ValueError):
                pass
        if rest is not None:
            try:
                lines.append(f"- **限售股质押数量**：{float(rest) / 1e8:.4f} 亿股")
            except (TypeError, ValueError):
                pass

        # 历史质押趋势
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
            vol_s = f"{float(vol) / 10000:.2f}" if vol else "N/A"
            amt_s = f"{float(amt) / 10000:.2f}" if amt else "N/A"
            lines.append(f"| {ann} | {end} | {proc} | {vol_s} | {amt_s} |")
    else:
        lines.append("\n*近期无回购记录*")

    return "\n".join(lines)
