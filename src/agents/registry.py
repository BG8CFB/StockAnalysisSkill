from __future__ import annotations

"""
智能体注册表。
定义所有 15 个智能体的配置：提示词文件名 + 所需数据工具列表。
工具列表用于 tool_injector 做交集过滤（available_tools ∩ agent_tools）。
"""

AGENT_CONFIGS: dict[str, dict] = {
    # ── Stage 1：六位专项分析师（并行）──────────────────────────────────────
    "technical_analyst": {
        "prompt_file": "stage1_01_技术分析师.md",
        "tools": ["price_tool", "indicator_tool", "sentiment_tool"],
    },
    "fundamental_analyst": {
        "prompt_file": "stage1_02_基本面分析师.md",
        "tools": ["fundamental_tool", "snapshot_tool"],
    },
    "microstructure_analyst": {
        "prompt_file": "stage1_03_市场微观结构分析师.md",
        "tools": ["capital_flow_tool", "dragon_tiger_tool", "margin_tool", "sentiment_tool"],
    },
    "sentiment_analyst": {
        "prompt_file": "stage1_04_市场情绪分析师.md",
        "tools": ["sentiment_tool", "snapshot_tool"],
    },
    "sector_analyst": {
        "prompt_file": "stage1_05_板块轮动分析师.md",
        "tools": ["sector_tool", "snapshot_tool"],
    },
    "news_analyst": {
        "prompt_file": "stage1_06_资讯事件分析师.md",
        "tools": ["news_tool"],
    },

    # ── Stage 2：多空辩论 + 研究主管 + 交易计划师 ────────────────────────────
    "bull_researcher": {
        "prompt_file": "stage2_01_看涨分析师.md",
        "tools": [],  # 上游 Stage1 报告直接传入，无需额外数据工具
    },
    "bear_researcher": {
        "prompt_file": "stage2_02_看跌分析师.md",
        "tools": [],
    },
    "research_director": {
        "prompt_file": "stage2_03_研究主管.md",
        "tools": [],
    },
    "trading_planner": {
        "prompt_file": "stage2_04_交易计划师.md",
        "tools": ["snapshot_tool"],
    },

    # ── Stage 3：三位风控师（并行）+ 首席风控官 ──────────────────────────────
    "aggressive_risk_manager": {
        "prompt_file": "stage3_03_激进风控师.md",
        "tools": ["risk_metric_tool"],
    },
    "conservative_risk_manager": {
        "prompt_file": "stage3_04_保守风控师.md",
        "tools": ["risk_metric_tool"],
    },
    "quant_risk_manager": {
        "prompt_file": "stage3_05_量化风控师.md",
        "tools": ["risk_metric_tool"],
    },
    "chief_risk_officer": {
        "prompt_file": "stage3_06_首席风控官.md",
        "tools": ["risk_metric_tool"],
    },

    # ── Stage 4：投资顾问（综合报告）────────────────────────────────────────
    "investment_advisor": {
        "prompt_file": "stage4_01_投资顾问.md",
        "tools": ["snapshot_tool"],
    },
}
