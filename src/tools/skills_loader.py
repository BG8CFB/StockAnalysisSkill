"""
技能加载器。按 Agent Skills 开放标准 (agentskills.io/specification) 实现渐进式加载。

目录结构：
  skills/
    skill-name/          # 技能目录（名称须与 SKILL.md 中 name 字段一致）
      SKILL.md           # 必须：YAML frontmatter（name + description）+ 指令内容
      references/        # 可选：补充文档（按需加载）
      scripts/           # 可选：可执行脚本
      assets/            # 可选：静态资源

渐进式加载（Progressive Disclosure）：
  第 1 层 - 元数据（~100 tokens/技能）：启动时扫描，提取 name + description
  第 2 层 - 指令内容（<5000 tokens）：AI 决定调用时按需加载 SKILL.md body
  第 3 层 - 资源文件（按需）：references/ scripts/ assets/ 中的文件按需加载
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"


@dataclass
class SkillMeta:
    """技能元数据（第 1 层：启动时加载）。"""

    name: str
    description: str
    skill_dir: Path


_skills_cache: Optional[list[SkillMeta]] = None


# --------------------------------------------------------------------------- #
# 内部工具                                                                      #
# --------------------------------------------------------------------------- #


def _parse_frontmatter(content: str) -> dict:
    """从 SKILL.md 解析 YAML frontmatter。"""
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end < 0:
        return {}
    try:
        return yaml.safe_load(content[3:end]) or {}
    except yaml.YAMLError as e:
        logger.warning(f"[SkillsLoader] YAML frontmatter 解析失败: {e}")
        return {}


def _extract_body(content: str) -> str:
    """从 SKILL.md 提取 body 内容（去除 frontmatter）。"""
    if not content.startswith("---"):
        return content
    end = content.find("---", 3)
    if end < 0:
        return content
    return content[end + 3 :].strip()


# --------------------------------------------------------------------------- #
# 公开接口                                                                      #
# --------------------------------------------------------------------------- #


def scan_skills() -> list[SkillMeta]:
    """
    扫描 skills/*/SKILL.md，提取元数据（第 1 层）。结果缓存。

    按规范要求：
    - 只识别包含 SKILL.md 文件的子目录
    - name 字段须与目录名一致
    - name 和 description 均为必填
    """
    global _skills_cache
    if _skills_cache is not None:
        return _skills_cache

    if not _SKILLS_DIR.exists():
        _skills_cache = []
        return _skills_cache

    skills: list[SkillMeta] = []
    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
            fm = _parse_frontmatter(content)
            name = fm.get("name", "")
            description = fm.get("description", "")

            if not name or not description:
                logger.warning(
                    f"[SkillsLoader] {skill_md}: frontmatter 缺少 name 或 description，跳过"
                )
                continue

            # 规范要求 name 须与目录名一致
            if name != skill_dir.name:
                logger.warning(
                    f"[SkillsLoader] {skill_md}: name '{name}' 与目录名 '{skill_dir.name}' 不一致"
                )

            skills.append(
                SkillMeta(name=name, description=description, skill_dir=skill_dir)
            )
        except Exception as e:
            logger.warning(f"[SkillsLoader] 读取 {skill_md} 失败: {e}")

    _skills_cache = skills
    if skills:
        logger.info(f"[SkillsLoader] 扫描完成：发现 {len(skills)} 个技能")
    else:
        logger.debug("[SkillsLoader] 扫描完成：未发现任何技能")
    return skills


def get_skill_tool_definitions() -> list[dict]:
    """
    构建 OpenAI function calling 格式的技能工具定义。

    AI 只能看到 name 和 description，无法看到完整指令内容。
    仅当 AI 决定调用时，才通过 execute_skill_call() 加载完整内容。
    """
    skills = scan_skills()
    if not skills:
        return []

    return [
        {
            "type": "function",
            "function": {
                "name": skill.name,
                "description": skill.description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        }
        for skill in skills
    ]


def execute_skill_call(tool_name: str, _arguments: str = "") -> str:
    """
    执行技能调用：加载 SKILL.md body 完整内容（第 2 层）。

    由 LLMClient 多轮工具调用循环中的 tool_executor 回调调用。
    """
    skills = scan_skills()
    for skill in skills:
        if skill.name == tool_name:
            skill_md = skill.skill_dir / "SKILL.md"
            try:
                content = skill_md.read_text(encoding="utf-8")
                body = _extract_body(content)
                logger.info(
                    f"[SkillsLoader] 技能 '{tool_name}' 已激活（{len(body)} 字）"
                )
                return body
            except Exception as e:
                logger.error(f"[SkillsLoader] 加载技能 '{tool_name}' 失败: {e}")
                return f"[技能 '{tool_name}' 加载失败: {e}]"

    return f"[技能 '{tool_name}' 未找到]"


def invalidate_cache() -> None:
    """清除缓存（技能文件更新后调用）。"""
    global _skills_cache
    _skills_cache = None
