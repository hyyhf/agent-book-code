"""
FunHarness - Skill Loader

Discovers and loads skills from .funharness/skills/ directory.
Each skill is a subdirectory containing a SKILL.md file with
YAML frontmatter (name, description) and markdown body.
"""
import os
from pathlib import Path

_SKILLS_DIR = ".funharness/skills"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file.

    Handles simple YAML frontmatter delimited by '---', including folded
    metadata values that continue on indented lines.
    No external YAML dependency required.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    closing_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        return {}, text

    meta = {}
    current_key = None
    current_value: list[str] = []
    block_style = None

    def commit_current():
        nonlocal current_key, current_value, block_style
        if current_key is None:
            return
        if block_style == "|":
            value = "\n".join(current_value).strip()
        else:
            value = " ".join(part.strip() for part in current_value).strip()
        if value.startswith("[") and value.endswith("]"):
            value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        meta[current_key] = value
        current_key = None
        current_value = []
        block_style = None

    for raw_line in lines[1:closing_index]:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            if current_key is not None and block_style in {">", "|"}:
                current_value.append("")
            continue

        is_continuation = raw_line[:1].isspace()
        if not is_continuation and ":" in raw_line:
            commit_current()
            key, val = raw_line.split(":", 1)
            current_key = key.strip()
            value = val.strip()
            if value in {">", "|"}:
                block_style = value
                current_value = []
            else:
                current_value = [value]
            continue

        if current_key is not None:
            current_value.append(raw_line.strip())

    commit_current()

    body = "\n".join(lines[closing_index + 1:]).strip()
    return meta, body


class Skill:
    """A loadable skill with metadata and content."""

    def __init__(self, name: str, description: str, content: str, path: str):
        self.name = name
        self.description = description
        self.content = content
        self.path = path

    def __repr__(self):
        return f"Skill({self.name!r})"


class SkillLoader:
    """Discover and load skills from .funharness/skills/<name>/SKILL.md."""

    def __init__(self, project_dir: str | None = None):
        root = Path(project_dir or os.getcwd())
        self.skills_dir = root / _SKILLS_DIR
        self._skills: dict[str, Skill] = {}
        self._scanned = False

    def _scan(self):
        """Scan skill subdirectories for SKILL.md files."""
        self._skills.clear()

        if not self.skills_dir.exists():
            self._scanned = True
            return

        for skill_md in sorted(self.skills_dir.glob("*/SKILL.md")):
            try:
                text = skill_md.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue

            meta, body = _parse_frontmatter(text)
            name = meta.get("name", skill_md.parent.name)
            desc = meta.get("description", "")

            self._skills[name] = Skill(
                name=name,
                description=desc,
                content=body,
                path=str(skill_md),
            )

        self._scanned = True

    def list_skills(self) -> list[dict]:
        """List all available skills with name and description."""
        if not self._scanned:
            self._scan()
        return [
            {"name": s.name, "description": s.description, "path": s.path}
            for s in self._skills.values()
        ]

    def load(self, name: str) -> str | None:
        """Load a skill's content by name. Returns None if not found."""
        if not self._scanned:
            self._scan()
        skill = self._skills.get(name)
        if skill:
            return skill.content
        return None

    def find(self, query: str) -> list[Skill]:
        """Search skills by keyword in name and description."""
        if not self._scanned:
            self._scan()

        query_lower = query.lower()
        scored = []

        for skill in self._skills.values():
            score = 0
            if query_lower in skill.name.lower():
                score += 10
            if query_lower in skill.description.lower():
                score += 3
            if query_lower in skill.content.lower():
                score += 1
            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored]

    def skills_summary(self) -> str:
        """Build a summary string of all available skills for system prompt."""
        if not self._scanned:
            self._scan()
        if not self._skills:
            return ""
        lines = [f"Available skills ({len(self._skills)}):"]
        for s in self._skills.values():
            lines.append(f"  - {s.name}: {s.description}")
            lines.append(f"    Path: {s.path}")
        return "\n".join(lines)
