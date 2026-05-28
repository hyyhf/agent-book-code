from __future__ import annotations

import json
from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path

from funharness.src.core.skills import SkillLoader

_TEST_TMP_DIR = Path(__file__).resolve().parents[4]


class SkillLoaderTests(unittest.TestCase):
    def test_reads_folded_frontmatter_description(self) -> None:
        with tempfile.TemporaryDirectory(dir=_TEST_TMP_DIR) as tmp:
            skill_dir = Path(tmp) / "skills" / "nature-data"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: nature-data
description: Prepare, audit, or revise Nature-ready Data Availability statements, data repository plans,
  dataset citations, and FAIR metadata checklists for manuscripts. Use when the user asks about
  Nature data availability, research data sharing, repository selection, accession numbers.
---

# Nature Data Availability Skill
""",
                encoding="utf-8",
            )

            loader = SkillLoader(tmp)
            loader.skills_dir = Path(tmp) / "skills"
            skills = loader.list_skills()

        self.assertEqual(skills[0]["name"], "nature-data")
        self.assertIn("data repository plans, dataset citations", skills[0]["description"])
        self.assertIn("accession numbers.", skills[0]["description"])

    def test_reads_block_scalar_description(self) -> None:
        with tempfile.TemporaryDirectory(dir=_TEST_TMP_DIR) as tmp:
            skill_dir = Path(tmp) / "skills" / "blocky"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: blocky
description: >
  First line
  second line
---

Body
""",
                encoding="utf-8",
            )

            loader = SkillLoader(tmp)
            loader.skills_dir = Path(tmp) / "skills"
            skills = loader.list_skills()

        self.assertEqual(skills[0]["description"], "First line second line")

    def test_discovers_recursive_project_skills_and_configured_single_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=_TEST_TMP_DIR) as tmp:
            root = Path(tmp)
            project_skill = root / ".funharness" / "skills" / "group" / "deep-skill"
            project_skill.mkdir(parents=True)
            (project_skill / "SKILL.md").write_text(
                """---
name: deep-skill
description: Handles nested project skills.
---

# Deep Skill
""",
                encoding="utf-8",
            )
            extra = root / "extra-skills"
            extra.mkdir()
            (extra / "single.md").write_text(
                """---
name: single-skill
description: Handles single-file skills from settings.
---

# Single Skill
""",
                encoding="utf-8",
            )
            settings_dir = root / ".funharness"
            settings_dir.mkdir(exist_ok=True)
            (settings_dir / "settings.json").write_text(
                json.dumps({"skills": ["extra-skills"]}),
                encoding="utf-8",
            )

            skills = SkillLoader(tmp).list_skills()

        names = {skill["name"] for skill in skills}
        self.assertEqual(names, {"deep-skill", "single-skill"})

    def test_disabled_skills_are_hidden_and_collisions_are_reported(self) -> None:
        with tempfile.TemporaryDirectory(dir=_TEST_TMP_DIR) as tmp:
            root = Path(tmp)
            first = root / ".funharness" / "skills" / "shared"
            first.mkdir(parents=True)
            (first / "SKILL.md").write_text(
                """---
name: shared-skill
description: First copy wins.
---

# First
""",
                encoding="utf-8",
            )
            disabled = root / ".funharness" / "skills" / "disabled"
            disabled.mkdir()
            (disabled / "SKILL.md").write_text(
                """---
name: disabled-skill
description: Hidden by settings.
---

# Hidden
""",
                encoding="utf-8",
            )
            extra = root / "extra" / "shared-again"
            extra.mkdir(parents=True)
            (extra / "SKILL.md").write_text(
                """---
name: shared-skill
description: Duplicate copy loses.
---

# Second
""",
                encoding="utf-8",
            )
            (root / ".funharness" / "settings.json").write_text(
                json.dumps({
                    "skills": ["extra"],
                    "disabledSkills": ["disabled-skill"],
                }),
                encoding="utf-8",
            )

            loader = SkillLoader(tmp)
            skills = loader.list_skills()
            diagnostics = loader.diagnostics()
            loaded = loader.load("shared-skill")
            disabled_loaded = loader.load("disabled-skill")

        self.assertEqual([skill["name"] for skill in skills], ["shared-skill"])
        self.assertIsNone(disabled_loaded)
        self.assertIn("# First", loaded or "")
        self.assertTrue(any(d.get("type") == "collision" for d in diagnostics))

    def test_skill_tools_load_by_name(self) -> None:
        from funharness.src.agent import _current_agent, tool_list_skills, tool_load_skill

        with tempfile.TemporaryDirectory(dir=_TEST_TMP_DIR) as tmp:
            skill_dir = Path(tmp) / ".funharness" / "skills" / "named"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: named-skill
description: Loaded through a tool.
---

# Named Skill
""",
                encoding="utf-8",
            )
            loader = SkillLoader(tmp)
            token = _current_agent.set(SimpleNamespace(skill_loader=loader))
            try:
                listed = json.loads(tool_list_skills())
                loaded = tool_load_skill("named-skill")
            finally:
                _current_agent.reset(token)

        self.assertEqual(listed["skills"][0]["name"], "named-skill")
        self.assertIn("Path:", loaded)
        self.assertIn("# Named Skill", loaded)


if __name__ == "__main__":
    unittest.main()
