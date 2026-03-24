"""
宏观数据工具模块（重构版）。

提供统一的宏观经济分析工具，整合中国宏观、利率、汇率和市场宏观数据。
原 macro_china_tool + macro_interest_tool + macro_fx_tool + macro_market_tool → macro_tool
删除 macro_global_tool（纯占位符，需外部API）
"""

from __future__ import annotations

from typing import Optional

from src.data.calculator import CalculatedDataPacket
from src.tools.base import _na, _fmt_float


def macro_tool(packet: CalculatedDataPacket) -> str:
    """
    宏观分析师：完整宏观经济分析工具。

    整合以下原工具功能：
    - macro_china_tool: 中国宏观经济数据（GDP、CPI、PPI、PMI、M2、社融）
    - macro_interest_tool: 利率与货币政策数据（LPR、美联储利率、SHIBOR）
    - macro_fx_tool: 汇率数据（美元兑人民币）
    - macro_market_tool: 市场层面宏观指标（北向资金、融资余额、市场估值）

    删除原 macro_global_tool（油价/美债/VIX等需要外部API，当前为占位符）
    """
    macro_data = getattr(packet, "macro_data", None)
    if macro_data is None:
        return _na("macro_tool", "宏观数据未加载")

    sections = ["# 宏观经济数据（整合）\n"]
    sections.append(f"**数据日期**：{macro_data.get('metadata', {}).get('date', 'N/A')}")
    sections.append(f"**数据来源**：AKShare（国家统计局/央行/外汇交易中心）\n")

    has_any_data = False

    # ========== Section 1: 中国宏观经济 ==========
    china = macro_data.get("china_macro")
    if china:
        has_any_data = True
        sections.append("## 1. 中国宏观经济\n")

        # CPI
        cpi = china.get("cpi")
        if cpi:
            sections.append("### CPI（消费者价格指数）")
            sections.append(f"- **最新值**：{_fmt_float(cpi.get('latest'), '{:.2f}', '%')}")
            sections.append(f"- **前值**：{_fmt_float(cpi.get('previous'), '{:.2f}', '%')}")
            sections.append(f"- **同比变化**：{_fmt_float(cpi.get('yoy_change'), '{:.2f}', '%')}")
            sections.append("- **解读**：CPI反映通胀水平，>3%为通胀警戒，<1%有通缩风险\n")

        # PPI
        ppi = china.get("ppi")
        if ppi:
            sections.append("### PPI（生产者价格指数）")
            sections.append(f"- **最新值**：{_fmt_float(ppi.get('latest'), '{:.2f}', '%')}")
            sections.append(f"- **同比变化**：{_fmt_float(ppi.get('yoy_change'), '{:.2f}', '%')}")
            sections.append("- **解读**：PPI反映工业企业生产成本，负值表示上游通缩压力\n")

        # PMI
        pmi = china.get("pmi")
        if pmi:
            sections.append("### PMI（采购经理指数）")
            sections.append(f"- **最新值**：{_fmt_float(pmi.get('latest'), '{:.2f}')}")
            pmi_val = pmi.get("latest", 50)
            status = "扩张" if pmi_val > 50 else "收缩"
            sections.append(f"- **经济状态**：{status}（荣枯线=50）")
            sections.append("- **解读**：PMI>50表示经济扩张，<50表示收缩\n")

        # GDP
        gdp = china.get("gdp")
        if gdp:
            sections.append("### GDP（国内生产总值）")
            sections.append(f"- **最新季度**：{gdp.get('latest_quarter', 'N/A')}")
            sections.append(f"- **同比增速**：{_fmt_float(gdp.get('latest_yoy'), '{:.2f}', '%')}\n")

        # M2
        m2 = china.get("m2")
        if m2:
            sections.append("### M2货币供应量")
            sections.append(f"- **最新值**：{_fmt_float(m2.get('latest'), '{:.2f}', '%')}")
            sections.append(f"- **同比变化**：{_fmt_float(m2.get('yoy_change'), '{:.2f}', '%')}")
            sections.append("- **解读**：M2增速反映流动性宽松程度，高增速利好资产价格\n")

        # 社融
        sf = china.get("social_finance")
        if sf:
            sections.append("### 社会融资规模")
            sections.append(f"- **最新值**：{_fmt_float(sf.get('latest'), '{:.0f}')} 亿元")
            sections.append(f"- **同比变化**：{_fmt_float(sf.get('yoy_change'), '{:.2f}', '%')}")
            sections.append("- **解读**：社融反映实体经济融资需求，是经济前瞻指标\n")

    # ========== Section 2: 利率与货币政策 ==========
    rates = macro_data.get("interest_rates")
    if rates:
        has_any_data = True
        sections.append("## 2. 利率与货币政策\n")

        # LPR
        lpr = rates.get("lpr")
        if lpr:
            sections.append("### 中国LPR（贷款市场报价利率）")
            sections.append(f"- **1年期LPR**：{_fmt_float(lpr.get('1y'), '{:.2f}', '%')}")
            sections.append(f"- **5年期以上LPR**：{_fmt_float(lpr.get('5y'), '{:.2f}', '%')}")
            sections.append(f"- **最新调整日期**：{lpr.get('date', 'N/A')}")
            if lpr.get("trend") and len(lpr["trend"]) >= 2:
                trend = lpr["trend"]
                try:
                    latest_val = float(trend[-1].get("1年期LPR", 0))
                    prev_val = float(trend[-2].get("1年期LPR", 0))
                    change = latest_val - prev_val
                    if change != 0:
                        direction = "下调" if change < 0 else "上调"
                        sections.append(f"- **最近调整**：{direction} {abs(change):.2f}%")
                except (ValueError, TypeError):
                    pass
            sections.append("- **解读**：LPR下调代表货币政策宽松，利好股市流动性\n")

        # 美联储利率
        fed = rates.get("fed_rate")
        if fed:
            sections.append("### 美联储联邦基金利率")
            sections.append(f"- **最新利率**：{_fmt_float(fed.get('latest'), '{:.2f}', '%')}")
            sections.append(f"- **前次利率**：{_fmt_float(fed.get('previous'), '{:.2f}', '%')}")
            fed_val = fed.get("latest", 0)
            if fed.get("previous"):
                try:
                    change = float(fed_val) - float(fed.get("previous"))
                    if change > 0:
                        sections.append(f"- **政策方向**：加息周期（+{change:.2f}%）")
                    elif change < 0:
                        sections.append(f"- **政策方向**：降息周期（{change:.2f}%）")
                    else:
                        sections.append("- **政策方向**：维持不变")
                except (ValueError, TypeError):
                    pass
            sections.append("- **解读**：美联储加息收紧全球流动性，对新兴市场构成压力\n")

        # SHIBOR
        shibor = rates.get("shibor")
        if shibor:
            sections.append("### SHIBOR（上海银行间同业拆放利率）")
            sections.append(f"- **隔夜利率**：{_fmt_float(shibor.get('overnight'), '{:.4f}', '%')}")
            sections.append(f"- **1周期利率**：{_fmt_float(shibor.get('1w'), '{:.4f}', '%')}")
            sections.append(f"- **1月期利率**：{_fmt_float(shibor.get('1m'), '{:.4f}', '%')}")
            sections.append("- **解读**：SHIBOR反映银行间资金成本，走高表示流动性紧张\n")

    # ========== Section 3: 汇率数据 ==========
    fx = macro_data.get("fx_data")
    if fx:
        has_any_data = True
        sections.append("## 3. 汇率数据\n")

        usdcny = fx.get("usd_cny")
        if usdcny:
            sections.append("### 美元兑人民币（在岸）")
            sections.append(f"- **最新汇率**：{_fmt_float(usdcny.get('latest'), '{:.4f}')}")
            sections.append(f"- **日变动**：{_fmt_float(usdcny.get('change'), '{:.4f}')}")
            sections.append(f"- **近期趋势**：{usdcny.get('trend', 'N/A')}")
            sections.append("- **解读**：人民币贬值利好出口，但可能引发资本外流压力\n")

    # ========== Section 4: 市场层面宏观指标 ==========
    market = macro_data.get("market_macro")
    if market:
        has_any_data = True
        sections.append("## 4. 市场层面宏观指标\n")

        # 北向资金
        north = market.get("northbound")
        if north:
            sections.append("### 北向资金（沪深港通）")
            sections.append(f"- **最新单日净流入**：{_fmt_float(north.get('latest_daily'), '{:.2f}')} 亿元")
            sections.append(f"- **近5日累计净流入**：{_fmt_float(north.get('net_inflow_5d'), '{:.2f}')} 亿元")
            sections.append(f"- **近20日累计净流入**：{_fmt_float(north.get('net_inflow_20d'), '{:.2f}')} 亿元")
            sections.append(f"- **近期趋势**：{north.get('trend', 'N/A')}")
            sections.append("- **解读**：北向资金持续流入代表外资看好A股，流出需警惕\n")

        # 市场融资余额
        margin = market.get("market_margin")
        if margin:
            sections.append("### 全市场融资余额")
            sections.append(f"- **最新余额**：{_fmt_float(margin.get('latest_balance'), '{:.0f}')} 亿元")
            sections.append(f"- **单日变动**：{_fmt_float(margin.get('change_1d'), '{:.2f}')} 亿元")
            sections.append(f"- **近20日趋势**：{margin.get('trend_20d', 'N/A')}")
            sections.append("- **解读**：融资余额持续上升代表杠杆情绪高涨，需警惕回调风险\n")

        # 市场估值
        val = market.get("market_valuation")
        if val:
            sections.append("### 市场估值水平（沪深300）")
            sections.append(f"- **市盈率PE**：{_fmt_float(val.get('hs300_pe'), '{:.2f}')}")
            sections.append(f"- **市净率PB**：{_fmt_float(val.get('hs300_pb'), '{:.2f}')}")
            sections.append("- **解读**：PE/PB处于历史低位时市场具备安全边际\n")

    if not has_any_data:
        sections.append("*所有宏观数据维度均不可用*")

    return "\n".join(sections)


# 保留向后兼容的别名
def macro_china_tool(packet: CalculatedDataPacket) -> str:
    """【向后兼容】中国宏观经济工具。"""
    return macro_tool(packet)


def macro_interest_tool(packet: CalculatedDataPacket) -> str:
    """【向后兼容】利率与货币政策工具。"""
    return macro_tool(packet)


def macro_fx_tool(packet: CalculatedDataPacket) -> str:
    """【向后兼容】汇率工具。"""
    return macro_tool(packet)


def macro_market_tool(packet: CalculatedDataPacket) -> str:
    """【向后兼容】市场宏观工具。"""
    return macro_tool(packet)
