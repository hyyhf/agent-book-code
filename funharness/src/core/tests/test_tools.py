from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from funharness.src.core.system_prompt import build_system_prompt
from funharness.src.core.tools import registry, tool_replace_in_file


class ReplaceInFileToolTests(unittest.TestCase):
    def test_replaces_multiple_specs_in_one_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sample.txt"
            target.write_text("alpha\nbeta\nalpha\n", encoding="utf-8")

            result = tool_replace_in_file(
                str(target),
                replacements=[
                    {"old_text": "alpha", "new_text": "A"},
                    {"old_text": "beta", "new_text": "B"},
                ],
            )

            self.assertIn("Replaced 3 occurrence(s)", result)
            self.assertEqual(target.read_text(encoding="utf-8"), "A\nB\nA\n")

    def test_replacements_are_checked_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sample.txt"
            original = "hello\nworld\n"
            target.write_text(original, encoding="utf-8")

            result = tool_replace_in_file(
                str(target),
                replacements=[
                    {"old_text": "hello", "new_text": "hi"},
                    {"old_text": "missing", "new_text": "found"},
                ],
            )

            self.assertIn("Error: replacement 2", result)
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_replacements_do_not_cascade_into_new_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sample.txt"
            target.write_text("a b", encoding="utf-8")

            tool_replace_in_file(
                str(target),
                replacements=[
                    {"old_text": "a", "new_text": "b"},
                    {"old_text": "b", "new_text": "c"},
                ],
            )

            self.assertEqual(target.read_text(encoding="utf-8"), "b c")

    def test_single_replacement_keeps_existing_call_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sample.txt"
            target.write_text("x x", encoding="utf-8")

            result = tool_replace_in_file(str(target), "x", "y")

            self.assertIn("Replaced 2 occurrence(s)", result)
            self.assertEqual(target.read_text(encoding="utf-8"), "y y")

    def test_schema_describes_batch_replacements(self) -> None:
        schema = registry.get_schema("tool_replace_in_file")
        params = schema["function"]["parameters"]
        replacements = params["properties"]["replacements"]

        self.assertEqual(params["required"], ["path"])
        self.assertEqual(replacements["type"], "array")
        self.assertEqual(replacements["items"]["type"], "object")
        self.assertEqual(replacements["items"]["properties"]["old_text"]["type"], "string")
        self.assertEqual(replacements["items"]["properties"]["new_text"]["type"], "string")
        self.assertEqual(set(replacements["items"]["required"]), {"old_text", "new_text"})

    def test_system_prompt_describes_team_leader_workflow(self) -> None:
        prompt = build_system_prompt(registry)

        self.assertIn("tool_team_tasks", prompt)
        self.assertIn("tool_team_task_update", prompt)
        self.assertIn("tool_team_rename", prompt)
        self.assertIn("propose a teammate lineup first", prompt)
        self.assertIn("Dependent work must be dispatched sequentially", prompt)


if __name__ == "__main__":
    unittest.main()
