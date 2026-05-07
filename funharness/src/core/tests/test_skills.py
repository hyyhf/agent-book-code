from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
