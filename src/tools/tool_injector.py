from __future__ import annotations

"""
工具注入器。
给定智能体名称和数据包，返回该智能体的完整数据上下文字符串（Markdown格式）。

逻辑：
  agent_config['tools'] ∩ available_tools → 注入实际数据
  差集（agent 需要但数据源无法提供）  → 注入占位符
"""

from typing import Optional

from src.data.calculator import CalculatedDataPacket
from src.agents.registry import AGENT_CONFIGS
from src.tools.data_tools import (
    price_tool, indicator_tool, fundamental_tool,
    capital_flow_tool, margin_tool, dragon_tiger_tool,
    sentiment_tool, sector_tool, news_tool,
    snapshot_tool, risk_metric_tool,
)

_NA_PLACEHOLDER = "[{tool}未激活：{reason}，本维度数据不可用，分析时请标注N/A]"

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
        拼接后的 Markdown 字符串，作为 LLM 的 user_context 数据部分
    """
    config = AGENT_CONFIGS.get(agent_name, {})
    agent_tools: list[str] = config.get("tools", [])

    if not agent_tools:
        return ""  # Stage 2 部分智能体无数据工具，上游报告直接由调用方传入

    sections: list[str] = []

    for tool_name in agent_tools:
        if tool_name == "risk_metric_tool":
            # 风控工具：入参为 risk_results dict
            if risk_results is not None and tool_name in available_tools:
                output = risk_metric_tool(risk_results)
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

    return "\n\n---\n\n".join(sections)
