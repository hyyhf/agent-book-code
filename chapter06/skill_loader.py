"""
6.3 - 技能系统与按需知识加载

实现基于 Markdown 文件的技能系统:
  - 技能的定义格式: YAML frontmatter + Markdown 正文
  - 技能目录的自动发现
  - 按关键词/标签匹配技能
  - 技能内容加载并注入上下文

运行方式:
    uv run python chapter06/skill_loader.py
"""
import os
import re
from pathlib import Path

# 技能文件存放的默认目录
_SKILLS_DIR = ".funharness/skills"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 Markdown 文件的 YAML frontmatter。

    支持以 '---' 包围的简单 YAML 格式。
    不依赖外部 YAML 库, 只处理 'key: value' 的简单键值对和列表。

    Args:
        text: Markdown 文件的完整文本

    Returns:
        (元数据字典, 正文内容) 的元组
    """
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    meta = {}
    for line in parts[1].strip().split("\n"):
        line = line.strip()
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            # 处理简单列表: "tags: [python, fastapi]"
            if val.startswith("[") and val.endswith("]"):
                val = [v.strip() for v in val[1:-1].split(",") if v.strip()]
            meta[key] = val

    body = parts[2].strip()
    return meta, body


class Skill:
    """表示一个可加载的技能。

    每个技能是一个 Markdown 文件, 包含:
    - name: 技能名称
    - description: 简短描述
    - tags: 标签列表(用于匹配)
    - content: 技能正文(知识内容)
    - path: 文件路径
    """

    def __init__(self, name: str, description: str, tags: list[str],
                 content: str, path: str):
        self.name = name
        self.description = description
        self.tags = tags
        self.content = content
        self.path = path

    def __repr__(self):
        return f"Skill({self.name!r}, tags={self.tags})"


class SkillLoader:
    """技能的发现、匹配与加载。

    扫描技能目录中的所有 .md 文件, 解析其 frontmatter 元数据,
    建立技能索引。当 Agent 需要特定领域的知识时,
    通过关键词或标签匹配找到相关技能, 将其内容注入到上下文中。

    技能文件格式示例:
        ---
        name: Python Testing
        description: pytest testing patterns and best practices
        tags: [python, testing, pytest]
        ---
        # Python Testing Patterns
        ## Fixtures
        Use fixtures for shared test setup ...
    """

    def __init__(self, project_dir: str | None = None):
        root = Path(project_dir or os.getcwd())
        self.skills_dir = root / _SKILLS_DIR
        self._skills: dict[str, Skill] = {}
        self._scanned = False

    def _scan(self):
        """扫描技能目录, 建立索引。"""
        self._skills.clear()

        if not self.skills_dir.exists():
            self._scanned = True
            return

        for path in sorted(self.skills_dir.glob("*/SKILL.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue

            meta, body = _parse_frontmatter(text)
            name = meta.get("name", path.parent.name)
            desc = meta.get("description", "")
            tags = meta.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",")]

            self._skills[name] = Skill(
                name=name,
                description=desc,
                tags=tags,
                content=body,
                path=str(path),
            )

        self._scanned = True

    def list_skills(self) -> list[dict]:
        """列出所有可用技能的元信息。

        Returns:
            包含 name, description, tags 的字典列表
        """
        if not self._scanned:
            self._scan()
        return [
            {"name": s.name, "description": s.description, "tags": s.tags}
            for s in self._skills.values()
        ]

    def find(self, query: str) -> list[Skill]:
        """按关键词搜索匹配的技能。

        在技能名称、描述和标签中搜索, 返回所有匹配的技能(按相关度排序)。

        Args:
            query: 搜索关键词

        Returns:
            匹配的 Skill 列表
        """
        if not self._scanned:
            self._scan()

        query_lower = query.lower()
        scored = []

        for skill in self._skills.values():
            score = 0
            # 名称匹配权重最高
            if query_lower in skill.name.lower():
                score += 10
            # 标签精确匹配
            for tag in skill.tags:
                if query_lower == tag.lower():
                    score += 8
                elif query_lower in tag.lower():
                    score += 4
            # 描述匹配
            if query_lower in skill.description.lower():
                score += 3
            # 正文匹配(较低权重)
            if query_lower in skill.content.lower():
                score += 1

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored]

    def load(self, name: str) -> str | None:
        """加载指定名称的技能内容。

        Args:
            name: 技能名称

        Returns:
            技能正文内容, 不存在则返回 None
        """
        if not self._scanned:
            self._scan()
        skill = self._skills.get(name)
        if skill:
            return skill.content
        return None

    def load_matching(self, query: str, max_skills: int = 2) -> str:
        """查找并加载与查询匹配的技能内容。

        这是 Agent 调用的主要入口: 给定一个任务描述,
        自动找到最相关的技能并返回其内容。

        Args:
            query: 任务描述或关键词
            max_skills: 最多加载几个技能(防止上下文过大)

        Returns:
            合并后的技能内容, 或未找到的提示
        """
        matches = self.find(query)
        if not matches:
            return f"No skills matching '{query}'"

        results = []
        for skill in matches[:max_skills]:
            results.append(
                f"## Skill: {skill.name}\n"
                f"_{skill.description}_\n\n"
                f"{skill.content}"
            )
        return "\n\n---\n\n".join(results)


# =============================================================
#  Agent 工具函数
# =============================================================

_loader: SkillLoader | None = None


def init_skills(project_dir: str | None = None):
    """初始化全局技能加载器。"""
    global _loader
    _loader = SkillLoader(project_dir)


def _get_loader() -> SkillLoader:
    global _loader
    if _loader is None:
        _loader = SkillLoader()
    return _loader


def list_available_skills() -> str:
    """列出所有可用的技能及其描述。当需要了解有哪些知识可用时使用。"""
    skills = _get_loader().list_skills()
    if not skills:
        return "No skills available"
    lines = [f"Available skills ({len(skills)}):"]
    for s in skills:
        tags = ", ".join(s["tags"]) if s["tags"] else "none"
        lines.append(f"  - {s['name']}: {s['description']} [tags: {tags}]")
    return "\n".join(lines)


def load_skill(query: str) -> str:
    """根据关键词或任务描述加载相关技能的知识内容。当需要特定领域的知识来完成任务时使用。

    Args:
        query: 关键词或任务描述
    """
    return _get_loader().load_matching(query)


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    import tempfile

    print("=== 技能系统演示 ===\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建技能目录和示例技能文件
        skills_dir = Path(tmpdir) / _SKILLS_DIR
        skills_dir.mkdir(parents=True)

        # 技能 1: Python 测试
        (skills_dir / "python-testing").mkdir()
        (skills_dir / "python-testing" / "SKILL.md").write_text("""\
---
name: Python Testing
description: pytest testing patterns and best practices
tags: [python, testing, pytest]
---
# Python Testing Patterns

## Basic Test Structure
```python
def test_addition():
    assert 1 + 1 == 2
```

## Fixtures
Use fixtures for shared setup:
```python
@pytest.fixture
def sample_data():
    return {"key": "value"}
```

## Parametrize
```python
@pytest.mark.parametrize("x,y,expected", [(1,2,3), (0,0,0)])
def test_add(x, y, expected):
    assert add(x, y) == expected
```
""", encoding="utf-8")

        # 技能 2: Git 工作流
        (skills_dir / "git-workflow").mkdir()
        (skills_dir / "git-workflow" / "SKILL.md").write_text("""\
---
name: Git Workflow
description: Common git commands and branching strategies
tags: [git, version-control]
---
# Git Workflow Guide

## Branch Naming
- feature/xxx for new features
- fix/xxx for bug fixes
- release/xxx for releases

## Common Commands
```bash
git checkout -b feature/new-thing
git add -p  # interactive staging
git commit -m "feat: add new thing"
git rebase -i HEAD~3  # squash commits
```
""", encoding="utf-8")

        # 技能 3: API 设计
        (skills_dir / "api-design").mkdir()
        (skills_dir / "api-design" / "SKILL.md").write_text("""\
---
name: REST API Design
description: RESTful API design conventions and patterns
tags: [api, rest, http]
---
# REST API Design

## URL Patterns
- GET /items - list
- GET /items/:id - read
- POST /items - create
- PUT /items/:id - update
- DELETE /items/:id - delete

## Status Codes
- 200 OK
- 201 Created
- 400 Bad Request
- 404 Not Found
- 500 Internal Server Error
""", encoding="utf-8")

        loader = SkillLoader(tmpdir)

        # 1. 列出技能
        print("--- 可用技能 ---")
        for s in loader.list_skills():
            print(f"  {s['name']}: {s['description']} {s['tags']}")

        # 2. 搜索
        print("\n--- 搜索 'python' ---")
        matches = loader.find("python")
        for m in matches:
            print(f"  {m.name} (tags: {m.tags})")

        print("\n--- 搜索 'git' ---")
        matches = loader.find("git")
        for m in matches:
            print(f"  {m.name} (tags: {m.tags})")

        # 3. 加载技能
        print("\n--- 加载 'Python Testing' ---")
        content = loader.load("Python Testing")
        if content:
            print(content[:200] + "...")

        # 4. 按任务描述匹配加载
        print("\n--- 加载匹配 'write pytest tests' ---")
        result = loader.load_matching("write pytest tests")
        print(result[:300] + "...")

        # 5. 工具函数测试
        print("\n--- 工具函数: list_available_skills ---")
        init_skills(tmpdir)
        print(list_available_skills())

        print("\n--- 工具函数: load_skill('api') ---")
        print(load_skill("api")[:200] + "...")

    # 6. 扫描真实项目目录中的技能
    real_dir = Path(__file__).parent
    real_loader = SkillLoader(str(real_dir))
    real_skills = real_loader.list_skills()
    print(f"\n--- 真实项目技能 ({real_dir / _SKILLS_DIR}) ---")
    if real_skills:
        for s in real_skills:
            print(f"  {s['name']}: {s['description'][:60]}")
    else:
        print("  (none found)")
