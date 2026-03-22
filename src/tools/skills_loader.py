from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"
_skills_list_cache: Optional[str] = None


def load_skills_list() -> str:
    """
    扫描 skills/*.md（排除 README.md），提取适用场景描述，返回格式化列表字符串。

    从每个文件中尝试提取以下任一格式的适用场景：
    1. YAML front matter 的 applicable_scenarios / description 字段
    2. ## 适用场景 段落的第一行内容
    3. 文件名本身（降级方案）

    返回格式：
    --- 可用技能列表 ---
    [filename.md] — [适用场景描述]
    ...
    """
    global _skills_list_cache
    if _skills_list_cache is not None:
        return _skills_list_cache

    if not _SKILLS_DIR.exists():
        _skills_list_cache = "--- 可用技能列表 ---\n（无可用技能）"
        return _skills_list_cache

    skill_files = sorted(
        f for f in _SKILLS_DIR.glob("*.md")
        if f.name.lower() != "readme.md"
    )

    if not skill_files:
        _skills_list_cache = "--- 可用技能列表 ---\n（无可用技能）"
        return _skills_list_cache

    lines = ["--- 可用技能列表 ---"]
    for skill_file in skill_files:
        description = _extract_description(skill_file)
        lines.append(f"[{skill_file.name}] — {description}")

    _skills_list_cache = "\n".join(lines)
    return _skills_list_cache


def load_skill_content(skill_filename: str) -> str:
    """返回 skills/{skill_filename} 的完整内容。"""
    path = _SKILLS_DIR / skill_filename
    if not path.exists():
        logger.warning(f"[SkillsLoader] Skill file not found: {path}")
        return f"[技能文件 {skill_filename} 未找到]"
    return path.read_text(encoding="utf-8")


def _extract_description(skill_file: Path) -> str:
    """从技能文件中提取适用场景描述。"""
    try:
        content = skill_file.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"[SkillsLoader] Failed to read {skill_file}: {e}")
        return skill_file.stem

    # 尝试 YAML front matter（以 --- 开头）
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            front_matter = content[3:end]
            for key in ("applicable_scenarios", "description", "适用场景"):
                m = re.search(rf"^{key}\s*:\s*(.+)$", front_matter, re.MULTILINE)
                if m:
                    return m.group(1).strip().strip('"\'')

    # 尝试 ## 适用场景 段落
    m = re.search(r"##\s*适用场景\s*\n+(.+)", content)
    if m:
        line = m.group(1).strip()
        # 去掉 markdown 列表符号
        line = re.sub(r"^[-*]\s*", "", line)
        if line:
            return line

    # 尝试文件第一个非空非标题行
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("---"):
            return line[:80]  # 截断过长描述

    return skill_file.stem


def invalidate_cache() -> None:
    """清除缓存（技能文件更新后调用）。"""
    global _skills_list_cache
    _skills_list_cache = None
