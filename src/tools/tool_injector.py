"""
工具注入器。
给定智能体名称和数据包，返回该智能体的完整数据上下文字符串（Markdown格式）。

逻辑：
  agent_config['tools'] ∩ available_tools → 注入实际数据
  差集（agent 需要但数据源无法提供）  → 注入占位符

特殊返回值：
  当智能体拥有工具配置、但全部工具均无数据时，返回以 DATA_MISSING_MARKER 开头的字符串。
  调用方（BaseAgent）检测到此标记后将跳过 LLM 调用，直接输出数据缺失通知。
"""
from __future__ import annotations

from typing import Optional

from src.data.calculator import CalculatedDataPacket
from src.agents.config_loader import get_agent_config
from src.tools.data_tools import (
    price_tool, indicator_tool, fundamental_tool,
    capital_flow_tool, margin_tool, dragon_tiger_tool,
    sentiment_tool, sector_tool, news_tool,
    snapshot_tool, shareholder_tool, risk_metric_tool,
)

_NA_PLACEHOLDER = "[{tool}未激活：{reason}，本维度数据不可用，分析时请标注N/A]"

# 当智能体的全部工具均无有效数据时，inject_tools 返回值以此标记开头。
# BaseAgent.run() 检测到此标记后直接返回数据缺失通知，不调用 LLM。
DATA_MISSING_MARKER = "###ALL_TOOLS_UNAVAILABLE###\n"

# 工具名 → 对应函数（除 risk_metric_tool 外都接受 packet 参数）
_TOOL_FUNCTIONS = {
    "price_tool": price_tool,
    "indicator_tool": indicator_tool,
    "fundamental_tool": fundamental_tool,
    "capital_flow_tool": capital_flow_tool,
    "margin_tool": margin_tool,
    "dragon_tiger_tool": dragon_tiger_tool,
    "sentiment_tool": sentiment_tool,
    "sector_tool": sector_tool,
    "news_tool": news_tool,
    "snapshot_tool": snapshot_tool,
    "shareholder_tool": shareholder_tool,
    # risk_metric_tool 单独处理（入参不同）
}


def inject_tools(
    agent_name: str,
    packet: CalculatedDataPacket,
    available_tools: set[str],
    risk_results: Optional[dict] = None,
) -> str:
    """
    为指定智能体构建完整数据上下文字符串。

    参数：
        agent_name:      智能体键名（对应 AGENT_CONFIGS 的 key）
        packet:          计算后的数据包
        available_tools: 数据适配器实际可用的工具集合
        risk_results:    风控计算结果（仅 Stage 3 智能体需要）

    返回：
        拼接后的 Markdown 字符串，作为 LLM 的 user_context 数据部分。
        若该智能体有工具配置但全部工具均无数据，返回以 DATA_MISSING_MARKER 开头的字符串，
        BaseAgent 检测到此标记后将跳过 LLM 调用。
    """
    config = get_agent_config(agent_name)
    agent_tools: list[str] = config["tools"]

    if not agent_tools:
        return ""  # Stage 2 部分智能体无数据工具，上游报告直接由调用方传入

    sections: list[str] = []
    real_data_count = 0  # 实际获取到有效数据的工具数量

    for tool_name in agent_tools:
        if tool_name == "risk_metric_tool":
            # 风控工具：入参为 risk_results dict
            if risk_results is not None and tool_name in available_tools:
                output = risk_metric_tool(risk_results)
                real_data_count += 1
            else:
                output = _NA_PLACEHOLDER.format(
                    tool=tool_name,
                    reason="风控计算结果未传入" if risk_results is None else "风控数据不可用",
                )
        elif tool_name in _TOOL_FUNCTIONS:
            if tool_name in available_tools:
                # 调用对应工具函数
                fn = _TOOL_FUNCTIONS[tool_name]
                try:
                    output = fn(packet)
                    real_data_count += 1
                except Exception as e:
                    output = _NA_PLACEHOLDER.format(
                        tool=tool_name,
                        reason=f"工具执行异常：{e}",
                    )
            else:
                # 工具在智能体配置中但数据源不可用 → 占位符
                output = _NA_PLACEHOLDER.format(
                    tool=tool_name,
                    reason="数据源当前不支持此工具（API积分不足或数据不存在）",
                )
        else:
            output = _NA_PLACEHOLDER.format(
                tool=tool_name,
                reason=f"未知工具：{tool_name}",
            )

        sections.append(output)

    # 全部工具无数据：返回带标记的字符串，BaseAgent 将跳过 LLM 调用
    if real_data_count == 0:
        missing_tools = ", ".join(agent_tools)
        na_detail = "\n\n---\n\n".join(sections)
        return (
            DATA_MISSING_MARKER
            + f"所需工具（共 {len(agent_tools)} 个）全部无数据：{missing_tools}\n\n"
            + na_detail
        )

    return "\n\n---\n\n".join(sections)
