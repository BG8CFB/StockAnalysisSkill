"""
智能体配置加载器。

从 config/agents/ 目录读取 YAML 配置文件，替代旧的 registry.py 硬编码方式。

目录结构：
  config/agents/
    stage1.yaml           Stage 1 分析师（单文件列表，用户可增删）
    stage2.yaml           Stage 2 固定智能体
    stage3.yaml           Stage 3 固定智能体
    stage4.yaml           Stage 4 固定智能体
  config/global.yaml      全局规则（注入所有智能体系统提示词头部）
  config/market_rules.yaml 市场规则（按市场类型注入）

每个 YAML 文件字段说明：
  agent_id:      智能体唯一标识符（与代码中使用的键名一致）
  display_name:  中文显示名（日志/报告中使用）
  prompt:        提示词内容（直接嵌入 YAML，不再引用外部文件）
  tools:         该智能体使用的内置工具列表（见 config/tools.yaml）
  use_skills:    是否向该智能体注入 skills/ 目录中的外置技能（true/false）
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

import yaml

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_AGENTS_DIR = _CONFIG_DIR / "agents"

# --------------------------------------------------------------------------- #
# 类型定义                                                                      #
# --------------------------------------------------------------------------- #

class AgentConfig(TypedDict):
    agent_id: str
    display_name: str
    prompt: str
    tools: list[str]
    use_skills: bool


# --------------------------------------------------------------------------- #
# 内部缓存                                                                      #
# --------------------------------------------------------------------------- #

_all_configs: dict[str, AgentConfig] = {}
_stage1_order: list[str] = []   # Stage 1 agent_id 列表，按文件中顺序
_loaded: bool = False

_global_rules: str = ""
_global_rules_loaded: bool = False

_market_rules_data: dict[str, str] = {}
_market_rules_loaded: bool = False


def _load_all() -> None:
    global _all_configs, _stage1_order, _loaded
    if _loaded:
        return

    configs: dict[str, AgentConfig] = {}
    stage1_ids: list[str] = []

    # ── Stage 1：单 YAML 文件，agents 为列表 ────────────────────────────────
    stage1_path = _AGENTS_DIR / "stage1.yaml"
    if stage1_path.exists():
        try:
            with stage1_path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            for entry in data.get("agents", []):
                agent_id = entry["agent_id"]
                configs[agent_id] = AgentConfig(
                    agent_id=agent_id,
                    display_name=entry.get("display_name", agent_id),
                    prompt=entry.get("prompt", ""),
                    tools=entry.get("tools", []),
                    use_skills=bool(entry.get("use_skills", True)),
                )
                stage1_ids.append(agent_id)
        except Exception as e:
            logger.error(f"[ConfigLoader] 加载 {stage1_path} 失败: {e}")
    else:
        logger.warning(f"[ConfigLoader] Stage1 配置文件不存在: {stage1_path}")

    # ── Stage 2 / 3 / 4：固定配置，agents 为字典 ────────────────────────────
    for stage_file in ("stage2.yaml", "stage3.yaml", "stage4.yaml"):
        path = _AGENTS_DIR / stage_file
        if not path.exists():
            logger.warning(f"[ConfigLoader] 配置文件不存在: {path}")
            continue
        try:
            with path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            for agent_id, cfg in data.get("agents", {}).items():
                configs[agent_id] = AgentConfig(
                    agent_id=agent_id,
                    display_name=cfg.get("display_name", agent_id),
                    prompt=cfg.get("prompt", ""),
                    tools=cfg.get("tools", []),
                    use_skills=bool(cfg.get("use_skills", True)),
                )
        except Exception as e:
            logger.error(f"[ConfigLoader] 加载 {path} 失败: {e}")

    _all_configs = configs
    _stage1_order = stage1_ids
    _loaded = True
    logger.info(
        f"[ConfigLoader] 加载完成：{len(configs)} 个智能体"
        f"（Stage1: {len(stage1_ids)} 个）"
    )


# --------------------------------------------------------------------------- #
# 公开接口                                                                      #
# --------------------------------------------------------------------------- #

def get_agent_config(agent_id: str) -> AgentConfig:
    """返回指定智能体的配置。若不存在返回空配置（带警告日志）。"""
    _load_all()
    if agent_id not in _all_configs:
        logger.warning(f"[ConfigLoader] 未找到智能体配置: '{agent_id}'，使用默认空配置")
        return AgentConfig(
            agent_id=agent_id,
            display_name=agent_id,
            prompt="",
            tools=[],
            use_skills=True,
        )
    return _all_configs[agent_id]


def get_stage1_agents() -> list[tuple[str, str]]:
    """
    返回 Stage 1 全部分析师列表，格式为 [(agent_id, display_name), ...]，
    按 config/agents/stage1.yaml 中的列表顺序排列。
    """
    _load_all()
    return [(_id, _all_configs[_id]["display_name"]) for _id in _stage1_order]


def get_all_configs() -> dict[str, AgentConfig]:
    """返回所有已加载智能体的配置字典（只读）。"""
    _load_all()
    return dict(_all_configs)


def get_global_rules() -> str:
    """返回全局规则提示词（从 config/global.yaml 加载，缓存）。"""
    global _global_rules, _global_rules_loaded
    if _global_rules_loaded:
        return _global_rules
    path = _CONFIG_DIR / "global.yaml"
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _global_rules = data.get("prompt", "")
    except Exception as e:
        logger.error(f"[ConfigLoader] 加载全局规则失败: {e}")
        _global_rules = ""
    _global_rules_loaded = True
    return _global_rules


def get_market_rules(stock_code: str) -> str:
    """
    根据股票代码后缀返回对应市场规则（从 config/market_rules.yaml 加载，缓存）。

    后缀映射：
      .SZ / .SH  → A 股规则（key: a）
      .HK        → 港股规则（key: hk）
      其他        → 美股规则（key: us）
    """
    global _market_rules_data, _market_rules_loaded
    if not _market_rules_loaded:
        path = _CONFIG_DIR / "market_rules.yaml"
        try:
            with path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            _market_rules_data = {
                "a": data.get("a", ""),
                "hk": data.get("hk", ""),
                "us": data.get("us", ""),
            }
        except Exception as e:
            logger.error(f"[ConfigLoader] 加载市场规则失败: {e}")
            _market_rules_data = {"a": "", "hk": "", "us": ""}
        _market_rules_loaded = True

    code_upper = stock_code.upper()
    if code_upper.endswith(".SZ") or code_upper.endswith(".SH"):
        return _market_rules_data["a"]
    elif code_upper.endswith(".HK"):
        return _market_rules_data["hk"]
    else:
        return _market_rules_data["us"]


def invalidate_cache() -> None:
    """清除缓存，下次调用时重新从 YAML 加载（用于热更新场景）。"""
    global _loaded, _global_rules_loaded, _market_rules_loaded
    _loaded = False
    _global_rules_loaded = False
    _market_rules_loaded = False
