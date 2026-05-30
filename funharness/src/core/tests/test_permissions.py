from __future__ import annotations

import platform
import sys
import tempfile
import unittest
from pathlib import Path

from funharness.src.core.permissions import PermissionManager, PermissionMode, SandboxExecutor


class SandboxExecutorTests(unittest.TestCase):
    def test_runtime_command_uses_same_dangerous_command_policy(self) -> None:
        manager = PermissionManager(mode=PermissionMode.AUTO)

        decision, reason = manager.check_tool_call(
            "tool_runtime_run",
            {"command": "rm -rf /"},
        )

        self.assertEqual(decision, "deny")
        self.assertIn("dangerous pattern", reason)

    def test_large_output_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            command = (
                f'"{sys.executable}" -c '
                '"import sys; sys.stdout.write(\'x\' * 200000)"'
            )
            result = SandboxExecutor(work_dir=work_dir, max_output=1000).execute(command)

        self.assertIn("[exit=0]", result)
        self.assertIn("...(truncated", result)
        self.assertLess(len(result), 1300)

    def test_captures_utf8_output_on_windows_locale_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            stdout_text = "\u9500\u552e\u8f93\u51fa"
            stderr_text = "\u9519\u8bef\u8f93\u51fa"
            command = (
                f'"{sys.executable}" -c '
                '"import sys; '
                f"sys.stdout.buffer.write({stdout_text!r}.encode('utf-8')); "
                f"sys.stderr.buffer.write({stderr_text!r}.encode('utf-8'))\""
            )
            result = SandboxExecutor(work_dir=work_dir).execute(command)

        self.assertIn("[exit=0]", result)
        self.assertIn(stdout_text, result)
        self.assertIn(stderr_text, result)
        self.assertNotIn("(no output)", result)

    def test_includes_redirected_stdout_preview_when_capture_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            script = work_dir / "emit_output.py"
            script.write_text("print('hello from redirected stdout')\n", encoding="utf-8")

            command = f'"{sys.executable}" "{script.name}" > redirected.txt'
            result = SandboxExecutor(work_dir=work_dir).execute(command)

        self.assertIn("[exit=0]", result)
        self.assertIn("redirected.txt", result)
        self.assertIn("hello from redirected stdout", result)
        self.assertNotIn("[exit=0]\n(no output)", result)

    @unittest.skipUnless(platform.system() == "Windows", "Windows mkdir behavior")
    def test_windows_mkdir_accepts_unix_parents_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            result = SandboxExecutor(work_dir=work_dir).execute(
                "mkdir -p stats_gov_data"
            )

            self.assertTrue((work_dir / "stats_gov_data").is_dir())
            self.assertFalse((work_dir / "-p").exists())

        self.assertIn("[exit=0]", result)

    @unittest.skipUnless(platform.system() == "Windows", "Windows mkdir behavior")
    def test_windows_mkdir_accepts_powershell_force_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            result = SandboxExecutor(work_dir=work_dir).execute(
                "mkdir stats_gov_data -Force"
            )

            self.assertTrue((work_dir / "stats_gov_data").is_dir())
            self.assertFalse((work_dir / "-Force").exists())

        self.assertIn("[exit=0]", result)

    @unittest.skipUnless(platform.system() == "Windows", "Windows mkdir behavior")
    def test_windows_mkdir_honors_leading_cd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "start"
            target_dir = Path(tmp) / "target"
            work_dir.mkdir()
            target_dir.mkdir()

            result = SandboxExecutor(work_dir=work_dir).execute(
                f'cd "{target_dir}" && mkdir -p nested'
            )

            self.assertTrue((target_dir / "nested").is_dir())
            self.assertFalse((work_dir / "nested").exists())
            self.assertFalse((target_dir / "-p").exists())

        self.assertIn("[exit=0]", result)

    @unittest.skipUnless(platform.system() == "Windows", "Windows shell behavior")
    def test_windows_cd_and_multiline_python_c_capture_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "start"
            target_dir = Path(tmp) / "target dir"
            work_dir.mkdir()
            target_dir.mkdir()
            (target_dir / "heroes_data.json").write_text(
                '[{"order": 1, "name": "捷风", "skill": [1, 2]}]',
                encoding="utf-8",
            )
            script = (
                "\n"
                "import json\n"
                "with open('heroes_data.json', 'r', encoding='utf-8') as f:\n"
                "    data = json.load(f)\n"
                "print(f'英雄总数: {len(data)}')\n"
                "for a in data:\n"
                "    print(f'{a[\"order\"]}. {a[\"name\"]} - {len(a[\"skill\"])}个技能')\n"
            )
            escaped_script = script.replace("\\", "\\\\").replace('"', '\\"')
            command = f'cd "{target_dir}" && "{sys.executable}" -c "{escaped_script}"'
            result = SandboxExecutor(work_dir=work_dir).execute(command)

        self.assertIn("[exit=0]", result)
        self.assertIn("英雄总数: 1", result)
        self.assertIn("1. 捷风 - 2个技能", result)
        self.assertNotIn("(no output)", result)


if __name__ == "__main__":
    unittest.main()
