from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from funharness.src.core.command_shells import resolve_shell
from funharness.src.core.permissions import PermissionManager, PermissionMode, SandboxExecutor, _windows_pid_exists
from funharness.src.core.runtime import RuntimeTaskManager, command_scope
from funharness.src.core.tools import registry, tool_run_command, tool_runtime_run, tool_runtime_wait


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def wait_until(fn, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return
        time.sleep(.02)
    raise AssertionError("condition not met")


class ShellSelectionTests(unittest.TestCase):
    def test_tool_schemas_expose_shell_choices(self):
        for name in ("tool_run_command", "tool_runtime_run"):
            params = registry.get_schema(name)["function"]["parameters"]
            shell = params["properties"]["shell"]
            self.assertEqual(shell["default"], "default")
            self.assertEqual(shell["type"], "string")
            self.assertIn("powershell", shell["enum"])
            self.assertIn("pwsh", shell["enum"])
            self.assertNotIn("shell", params["required"])

    def test_invalid_shell_is_rejected_before_starting_a_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = RuntimeTaskManager(root=Path(tmp) / "runtime", work_dir=tmp)
            try:
                with command_scope(manager), self.assertRaisesRegex(ValueError, "Unsupported shell"):
                    tool_run_command("echo must-not-run", shell="cmd & echo injected")
                self.assertFalse(manager.list())
            finally:
                manager.shutdown()

    def test_missing_pwsh_reports_error_instead_of_silently_switching_shells(self):
        with patch("funharness.src.core.command_shells.shutil.which", return_value=None), \
                patch("funharness.src.core.command_shells.Path.is_file", return_value=False):
            with self.assertRaisesRegex(ValueError, "not installed"):
                resolve_shell("pwsh")

    def test_powershell_formatters_do_not_trigger_disk_format_block(self):
        manager = PermissionManager(mode=PermissionMode.AUTO)
        for script in ("Get-Process | Format-Table", "Get-Item . | Format-List *", "'abc' | Format-Hex"):
            self.assertEqual(manager.check_tool_call("tool_run_command", {"command": script, "shell": "powershell"})[0], "allow")
        for script in ("format C:", "format.exe D:", '& "format.exe" E:'):
            self.assertEqual(manager.check_tool_call("tool_run_command", {"command": script, "shell": "powershell"})[0], "deny")

    @unittest.skipUnless(os.name == "nt", "Windows shell defaults")
    def test_default_remains_cmd(self):
        self.assertEqual(resolve_shell().name, "cmd")
        result = SandboxExecutor(timeout=10, shell="cmd").execute("echo CMD-FIRST && echo CMD-SECOND")
        self.assertIn("[exit=0]", result)
        self.assertIn("CMD-FIRST", result)
        self.assertIn("CMD-SECOND", result)


class PowerShellBehaviorMixin:
    shell_name = "powershell"

    def setUp(self):
        try:
            resolve_shell(self.shell_name)
        except ValueError as exc:
            self.skipTest(str(exc))
        self.tmp = tempfile.TemporaryDirectory(prefix="fh ps 中文 ")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def execute(self, script, **kwargs):
        executor = SandboxExecutor(work_dir=self.root, shell=self.shell_name, timeout=15, **kwargs)
        output = executor.execute(script)
        self.assertIsNone(executor._script_path)
        return executor, output

    def test_unicode_multiline_quotes_variables_and_pipeline(self):
        script = """
$literal = '中文 $HOME %PATH% & | < > "double" ''single'''
$text = @'
第一行
第二行 $literal %PATH%
'@
$numbers = 1..3 | ForEach-Object { $_ * 2 }
[pscustomobject]@{literal=$literal; text=$text; numbers=@($numbers); cwd=(Get-Location).Path} | ConvertTo-Json -Compress
"""
        executor, output = self.execute(script)
        self.assertEqual(executor.exit_code, 0, output)
        payload = json.loads(output.split("\n", 1)[1])
        self.assertEqual(payload["literal"], '中文 $HOME %PATH% & | < > "double" \'single\'')
        self.assertIn("第二行 $literal %PATH%", payload["text"])
        self.assertEqual(payload["numbers"], [2, 4, 6])
        self.assertEqual(Path(payload["cwd"]), self.root)

    def test_set_location_native_program_and_unicode_arguments(self):
        target = self.root / "nested 中文 & space"
        target.mkdir()
        script_file = target / "print args.py"
        script_file.write_text("import json,sys,os\nprint(json.dumps([sys.argv[1:],os.getcwd()],ensure_ascii=True))\n", encoding="utf-8")
        value = "中文 $HOME %PATH% & | ' value"
        executor, output = self.execute(
            f"Set-Location -LiteralPath {ps_literal(target)}\n"
            f"& {ps_literal(sys.executable)} {ps_literal(script_file)} {ps_literal(value)}"
        )
        self.assertEqual(executor.exit_code, 0, output)
        args, cwd = json.loads(output.split("\n", 1)[1])
        self.assertEqual(args, [value])
        self.assertEqual(Path(cwd), target)

    def test_objects_format_table_and_utf8_file_roundtrip(self):
        executor, output = self.execute("""
$path = '文字 空格 &.txt'
'文件内容 中文' | Set-Content -LiteralPath $path -Encoding UTF8
Get-Content -LiteralPath $path -Encoding UTF8
[pscustomobject]@{Name='测试对象'; Count=3} | Format-Table -AutoSize
""")
        self.assertEqual(executor.exit_code, 0, output)
        self.assertIn("文件内容 中文", output)
        self.assertIn("测试对象", output)
        self.assertTrue((self.root / "文字 空格 &.txt").is_file())

    def test_large_script_exceeds_windows_command_line_limit(self):
        script = "$x = @'\n" + "汉字x" * 20000 + "\n'@\n$x.Length"
        executor, output = self.execute(script)
        self.assertEqual(executor.exit_code, 0, output)
        self.assertIn("60000", output)

    def test_last_native_nonzero_exit_is_preserved(self):
        script = self.root / "exit.py"
        script.write_text("raise SystemExit(17)\n", encoding="utf-8")
        executor, output = self.execute(f"& {ps_literal(sys.executable)} {ps_literal(script)}")
        self.assertEqual(executor.exit_code, 17, output)
        self.assertEqual(executor.outcome, "failed")

    def test_explicit_exit_and_expected_native_error_handling(self):
        script = self.root / "exit.py"
        script.write_text("raise SystemExit(17)\n", encoding="utf-8")
        executor, output = self.execute(f"& {ps_literal(sys.executable)} {ps_literal(script)}\nexit 0")
        self.assertEqual(executor.exit_code, 0, output)
        executor, output = self.execute("exit 9")
        self.assertEqual(executor.exit_code, 9, output)

    def test_cmdlet_error_is_plain_text_and_stops_before_side_effect(self):
        executor, output = self.execute("Get-Item -LiteralPath './missing-0db5cfd6'\nSet-Content -LiteralPath 'must-not-exist' -Value 'bad'")
        self.assertNotEqual(executor.exit_code, 0, output)
        self.assertFalse((self.root / "must-not-exist").exists())
        self.assertIn("missing-0db5cfd6", output)
        self.assertNotIn("#< CLIXML", output)

    def test_parse_error_does_not_execute_partial_script(self):
        executor, output = self.execute("'START' | Set-Content 'must-not-exist'\nif (")
        self.assertNotEqual(executor.exit_code, 0, output)
        self.assertFalse((self.root / "must-not-exist").exists())

    def test_read_host_fails_noninteractively(self):
        executor, output = self.execute("Read-Host 'Need input'")
        self.assertEqual(executor.outcome, "failed", output)

    def test_timeout_preserves_output_and_removes_script(self):
        executor = SandboxExecutor(work_dir=self.root, shell=self.shell_name, timeout=4)
        output = executor.execute("'BEFORE TIMEOUT'\nStart-Sleep -Seconds 30")
        self.assertEqual(executor.outcome, "timed_out", output)
        self.assertIn("BEFORE TIMEOUT", output)
        self.assertIsNone(executor._script_path)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object child cleanup")
    def test_cancel_kills_native_child_and_removes_script(self):
        child = self.root / "child.py"
        child.write_text("import os,time\nfrom pathlib import Path\nPath('child.pid').write_text(str(os.getpid()))\nprint('CHILD READY',flush=True)\ntime.sleep(30)\n", encoding="utf-8")
        executor = SandboxExecutor(work_dir=self.root, shell=self.shell_name, timeout=30)
        results = []
        worker = threading.Thread(target=lambda: results.append(executor.execute(
            f"& {ps_literal(sys.executable)} {ps_literal(child)}")), daemon=True)
        worker.start()
        try:
            pid_file = self.root / "child.pid"
            wait_until(pid_file.exists)
            pid = int(pid_file.read_text())
            script_path = executor._script_path
            executor.interrupt()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(executor.outcome, "cancelled", results)
            self.assertFalse(_windows_pid_exists(pid))
            self.assertFalse(script_path.exists())
        finally:
            executor.interrupt()
            worker.join(5)

    def test_runtime_entry_points_preserve_shell_and_allow_waiting(self):
        manager = RuntimeTaskManager(root=self.root / "runtime", work_dir=self.root)
        try:
            with command_scope(manager):
                for entry in (tool_run_command, tool_runtime_run):
                    entry("'SHELL READY'", shell=self.shell_name)
                    task = manager.list()[0]
                    output = tool_runtime_wait(task.runtime_id, 15000)
                    self.assertIn("status=done", output)
                    self.assertIn("SHELL READY", output)
                    self.assertEqual(task.shell, self.shell_name)
                    self.assertTrue(task.shell_executable)
        finally:
            manager.shutdown()


class WindowsPowerShellTests(PowerShellBehaviorMixin, unittest.TestCase):
    shell_name = "powershell"


class PowerShell7Tests(PowerShellBehaviorMixin, unittest.TestCase):
    shell_name = "pwsh"

    def test_powershell7_pipeline_chain_operators(self):
        executor, output = self.execute("Write-Output 'FIRST' && Write-Output 'SECOND'")
        self.assertEqual(executor.exit_code, 0, output)
        self.assertIn("SECOND", output)


if __name__ == "__main__":
    unittest.main()
