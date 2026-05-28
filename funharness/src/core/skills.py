"""
FunHarness - Skill Loader

Discovers Agent Skills from the current workspace. By default it scans
.funharness/skills, and .funharness/settings.json can add more local paths
or disable skills by name.
"""
import json
import os
from pathlib import Path

_SKILLS_DIR = ".funharness/skills"
_SETTINGS_FILE = ".funharness/settings.json"
_MAX_DESCRIPTION_LENGTH = 1024


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

    def __init__(
        self,
        name: str,
        description: str,
        content: str,
        path: str,
        *,
        raw_content: str = "",
        source: str = "project",
        disabled: bool = False,
    ):
        self.name = name
        self.description = description
        self.content = content
        self.path = path
        self.raw_content = raw_content or content
        self.source = source
        self.disabled = disabled

    def __repr__(self):
        return f"Skill({self.name!r})"


class SkillLoader:
    """Discover and load skills for a workspace."""

    def __init__(self, project_dir: str | None = None):
        self.project_dir = Path(project_dir or os.getcwd()).resolve()
        self.skills_dir = self.project_dir / _SKILLS_DIR
        self.settings_path = self.project_dir / _SETTINGS_FILE
        self._skills: dict[str, Skill] = {}
        self._diagnostics: list[dict] = []
        self._disabled_names: set[str] = set()
        self._scanned = False

    def _read_settings(self) -> dict:
        """Read project skill settings from .funharness/settings.json."""
        self._disabled_names = set()
        if not self.settings_path.exists():
            return {"skills": [], "disabledSkills": []}

        try:
            raw = self.settings_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            self._diagnostics.append({
                "type": "warning",
                "message": f"failed to read settings: {exc}",
                "path": str(self.settings_path),
            })
            return {"skills": [], "disabledSkills": []}

        if not isinstance(data, dict):
            self._diagnostics.append({
                "type": "warning",
                "message": "settings file must contain a JSON object",
                "path": str(self.settings_path),
            })
            return {"skills": [], "disabledSkills": []}

        skills = data.get("skills", [])
        disabled = data.get("disabledSkills", [])
        if not isinstance(skills, list):
            self._diagnostics.append({
                "type": "warning",
                "message": "settings.skills must be an array",
                "path": str(self.settings_path),
            })
            skills = []
        if not isinstance(disabled, list):
            self._diagnostics.append({
                "type": "warning",
                "message": "settings.disabledSkills must be an array",
                "path": str(self.settings_path),
            })
            disabled = []

        self._disabled_names = {str(name) for name in disabled}
        return {"skills": skills, "disabledSkills": disabled}

    def _resolve_configured_path(self, raw_path: str) -> Path:
        expanded = os.path.expandvars(os.path.expanduser(raw_path))
        path = Path(expanded)
        if not path.is_absolute():
            path = self.project_dir / path
        return path.resolve()

    def _load_skill_file(self, skill_md: Path, source: str) -> Skill | None:
        try:
            text = skill_md.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError) as exc:
            self._diagnostics.append({
                "type": "warning",
                "message": f"failed to read skill file: {exc}",
                "path": str(skill_md),
            })
            return None

        meta, body = _parse_frontmatter(text)
        name = str(meta.get("name", skill_md.stem if skill_md.name != "SKILL.md" else skill_md.parent.name)).strip()
        desc = str(meta.get("description", "")).strip()

        if not desc:
            self._diagnostics.append({
                "type": "warning",
                "message": "description is required",
                "path": str(skill_md),
            })
            return None
        if len(desc) > _MAX_DESCRIPTION_LENGTH:
            self._diagnostics.append({
                "type": "warning",
                "message": f"description exceeds {_MAX_DESCRIPTION_LENGTH} characters",
                "path": str(skill_md),
            })

        disabled = name in self._disabled_names
        return Skill(
            name=name,
            description=desc,
            content=body,
            path=str(skill_md),
            raw_content=text,
            source=source,
            disabled=disabled,
        )

    def _add_skill(self, skill: Skill):
        existing = self._skills.get(skill.name)
        if existing is not None:
            self._diagnostics.append({
                "type": "collision",
                "message": f'name "{skill.name}" collision; keeping first skill',
                "name": skill.name,
                "winnerPath": existing.path,
                "loserPath": skill.path,
            })
            return
        self._skills[skill.name] = skill

    def _scan_directory(self, directory: Path, source: str, include_root_files: bool = True):
        if not directory.exists():
            self._diagnostics.append({
                "type": "warning",
                "message": "skill path does not exist",
                "path": str(directory),
            })
            return
        if not directory.is_dir():
            self._diagnostics.append({
                "type": "warning",
                "message": "skill path is not a directory",
                "path": str(directory),
            })
            return

        skill_file = directory / "SKILL.md"
        if skill_file.is_file():
            skill = self._load_skill_file(skill_file, source)
            if skill is not None:
                self._add_skill(skill)
            return

        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            self._diagnostics.append({
                "type": "warning",
                "message": f"failed to scan skill directory: {exc}",
                "path": str(directory),
            })
            return

        if include_root_files:
            for entry in entries:
                if entry.is_file() and entry.suffix.lower() == ".md":
                    skill = self._load_skill_file(entry, source)
                    if skill is not None:
                        self._add_skill(skill)

        for entry in entries:
            if entry.name.startswith(".") or entry.name == "node_modules":
                continue
            if entry.is_dir():
                self._scan_directory(entry, source, include_root_files=False)

    def _scan_path(self, path: Path, source: str):
        if not path.exists():
            self._diagnostics.append({
                "type": "warning",
                "message": "skill path does not exist",
                "path": str(path),
            })
            return
        if path.is_dir():
            self._scan_directory(path, source, include_root_files=True)
            return
        if path.is_file() and path.suffix.lower() == ".md":
            skill = self._load_skill_file(path, source)
            if skill is not None:
                self._add_skill(skill)
            return
        self._diagnostics.append({
            "type": "warning",
            "message": "skill path is not a markdown file or directory",
            "path": str(path),
        })

    def _scan(self):
        """Scan configured skill locations."""
        self._skills.clear()
        self._diagnostics.clear()

        settings = self._read_settings()
        if self.skills_dir.exists():
            self._scan_directory(self.skills_dir, "project", include_root_files=True)

        for raw_path in settings["skills"]:
            if not isinstance(raw_path, str):
                self._diagnostics.append({
                    "type": "warning",
                    "message": "settings.skills entries must be strings",
                    "path": str(self.settings_path),
                })
                continue
            self._scan_path(self._resolve_configured_path(raw_path), "settings")

        self._scanned = True

    def list_skills(self, include_disabled: bool = False) -> list[dict]:
        """List all available skills with name and description.

        Always re-scans the filesystem so that newly added or removed
        skills are reflected immediately without a backend restart.
        """
        self._scan()
        return [
            {
                "name": s.name,
                "description": s.description,
                "path": s.path,
                "source": s.source,
                "disabled": s.disabled,
            }
            for s in self._skills.values()
            if include_disabled or not s.disabled
        ]

    def load(self, name: str) -> str | None:
        """Load a skill's full SKILL.md content by name. Returns None if not found."""
        self._scan()
        skill = self._skills.get(name)
        if skill and not skill.disabled:
            return skill.raw_content
        return None

    def get(self, name: str) -> Skill | None:
        """Return a skill object by name, excluding disabled skills."""
        self._scan()
        skill = self._skills.get(name)
        if skill and not skill.disabled:
            return skill
        return None

    def diagnostics(self) -> list[dict]:
        """Return discovery warnings and collisions from the last scan."""
        if not self._scanned:
            self._scan()
        return list(self._diagnostics)

    def find(self, query: str) -> list[Skill]:
        """Search skills by keyword in name and description."""
        if not self._scanned:
            self._scan()

        query_lower = query.lower()
        scored = []

        for skill in self._skills.values():
            if skill.disabled:
                continue
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
        visible = [s for s in self._skills.values() if not s.disabled]
        if not visible:
            return ""
        lines = [
            f"Available skills ({len(visible)}):",
            "When a task matches a listed skill, call tool_load_skill with the skill name before following it.",
        ]
        for s in visible:
            lines.append(f"  - {s.name}: {s.description}")
            lines.append(f"    Path: {s.path}")
            lines.append(f"    Source: {s.source}")
        if self._diagnostics:
            lines.append("Skill diagnostics:")
            for diagnostic in self._diagnostics:
                dtype = diagnostic.get("type", "warning")
                message = diagnostic.get("message", "")
                path = diagnostic.get("path") or diagnostic.get("loserPath", "")
                suffix = f" ({path})" if path else ""
                lines.append(f"  - {dtype}: {message}{suffix}")
        return "\n".join(lines)
