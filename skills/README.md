# Skills 目录说明

`skills/` 目录存放供内部 AI 智能体调用的**领域知识技能**。

遵循 [Agent Skills 开放标准](https://agentskills.io/specification)，支持渐进式加载。

---

## 目录结构

每个技能是一个**子目录**，内含必须的 `SKILL.md` 文件：

```
skills/
  semiconductor-analysis/      # 技能目录（名称须与 SKILL.md 中 name 一致）
    SKILL.md                   # 必须：元数据 + 指令内容
    references/                # 可选：补充文档
    scripts/                   # 可选：可执行脚本
    assets/                    # 可选：静态资源
  quant-factors/
    SKILL.md
    references/
      value-factors.md
```

---

## SKILL.md 格式

```markdown
---
name: semiconductor-analysis
description: 半导体行业分析框架。分析半导体/芯片板块个股时使用。
---

# 半导体行业分析框架

## 行业特征
...（完整领域知识内容）

## 关键指标
...
```

### Frontmatter 字段

| 字段 | 必须 | 说明 |
|------|------|------|
| `name` | 是 | 1-64 字符，小写字母+数字+连字符，须与目录名一致 |
| `description` | 是 | 1-1024 字符，描述技能功能和适用场景 |
| `license` | 否 | 许可证信息 |
| `compatibility` | 否 | 环境要求说明 |
| `metadata` | 否 | 自定义键值对 |

---

## 渐进式加载机制

```
第 1 层 — 元数据（~100 tokens/技能）：
  服务启动时扫描 skills/*/SKILL.md → 提取 name + description
  → 构建 function calling 工具定义
  → AI 智能体只看到技能名称和描述

第 2 层 — 指令内容（<5000 tokens 推荐）：
  仅当 AI 决定调用时 → 加载 SKILL.md body 全文
  → 作为工具调用结果返回给 AI

第 3 层 — 资源文件（按需）：
  references/ scripts/ assets/ 中的文件
  → 仅在 AI 明确需要时加载
```

---

## 智能体配置

通过 `config/agents/*.yaml` 中的 `use_skills` 字段控制：

```yaml
# 启用技能：AI 能感知到所有技能，自主决定是否调用
use_skills: true

# 关闭技能：AI 完全不知道技能存在
use_skills: false
```

技能与信息工具（`tools` 列表）完全独立，互不影响。

---

## 如何新增技能

1. 在 `skills/` 下创建以技能名命名的目录
2. 在目录中创建 `SKILL.md`，写入 frontmatter 和指令内容
3. 重启服务生效（技能列表在启动时缓存）

验证：启动日志会显示 `[SkillsLoader] 扫描完成：发现 N 个技能`
