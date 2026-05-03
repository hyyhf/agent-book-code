from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from funharness.src.core.permissions import SandboxExecutor


class SandboxExecutorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
