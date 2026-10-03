from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from funharness.src.core.hooks import LoopDetectionMiddleware
from funharness.src.core.permissions import _windows_pid_exists
from funharness.src.core.runtime import ACTIVE, RuntimeStatus, RuntimeTaskManager, command_scope
from funharness.src.core.tools import tool_run_command, tool_runtime_wait, tool_runtime_cancel


def wait_until(fn, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(.02)
    raise AssertionError("condition did not become true")


class CommandRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.manager = RuntimeTaskManager(root=self.root / "runtime", work_dir=self.root)

    def tearDown(self):
        self.manager.shutdown()
        self.tmp.cleanup()

    def script(self, source, name="command.py"):
        target = self.root / name
        target.write_text(source, encoding="utf-8")
        return f'"{sys.executable}" "{target}"'

    def finished(self, rid):
        return wait_until(lambda: self.manager.get(rid) if self.manager.get(rid).status not in ACTIVE else None)

    def test_yield_does_not_restart_command_and_live_output_is_readable(self):
        command = self.script("import time\nfrom pathlib import Path\n"
                              "p=Path('count.txt')\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
                              "print('READY', flush=True)\ntime.sleep(.7)\nprint('FINISHED')\n")
        with command_scope(self.manager):
            start = time.monotonic()
            result = tool_run_command(command, timeout=10, yield_time_ms=0)
            self.assertLess(time.monotonic() - start, .5)
            self.assertIn("runtime_id=", result)
            rid = self.manager.list()[0].runtime_id
            wait_until(lambda: "READY" in self.manager.output(rid))
            self.assertIn(self.manager.get(rid).status, ACTIVE)
            finished = tool_runtime_wait(rid, 3000)
        self.assertIn("status=done", finished)
        self.assertIn("FINISHED", finished)
        self.assertEqual((self.root / "count.txt").read_text(), "x")

    def test_timeout_keeps_partial_output_and_waits_never_extend_deadline(self):
        command = self.script("import time\nprint('BEFORE TIMEOUT', flush=True)\ntime.sleep(30)\n")
        rid = self.manager.submit_command(command, timeout=.4, background=False)
        start = time.monotonic()
        while self.manager.get(rid).status in ACTIVE:
            self.manager.wait(rid, 100)
        task = self.manager.get(rid)
        self.assertEqual(task.status, RuntimeStatus.TIMED_OUT)
        self.assertLess(time.monotonic() - start, 3)
        self.assertIn("BEFORE TIMEOUT", self.manager.output(rid))
        self.assertIn("timed out", self.manager.output(rid))

    def test_stdin_is_eof_instead_of_hanging_on_input(self):
        command = self.script("import sys\nprint(repr(sys.stdin.read()))\n")
        rid = self.manager.submit_command(command, timeout=3)
        self.assertEqual(self.finished(rid).status, RuntimeStatus.DONE)
        self.assertIn("''", self.manager.output(rid))

    def test_flooding_both_streams_keeps_tail_and_bounded_output(self):
        command = self.script("import sys\nsys.stdout.write('x'*2000000+'\\nSTDOUT TAIL\\n')\n"
                              "sys.stderr.write('y'*2000000+'\\nSTDERR TAIL\\n')\n")
        rid = self.manager.submit_command(command, timeout=5)
        self.assertEqual(self.finished(rid).status, RuntimeStatus.DONE)
        output = self.manager.output(rid)
        self.assertIn("STDOUT TAIL", output)
        self.assertIn("STDERR TAIL", output)
        self.assertIn("truncated", output)
        self.assertLess(len(output), 51000)
        self.assertGreater(self.manager.get(rid).output_bytes, 3900000)

    def test_output_flood_cannot_starve_timeout(self):
        command = self.script("import os\nwhile True: os.write(1, b'x'*65536)\n")
        start = time.monotonic()
        rid = self.manager.submit_command(command, timeout=.3)
        self.assertEqual(self.finished(rid).status, RuntimeStatus.TIMED_OUT)
        self.assertLess(time.monotonic() - start, 3)

    def test_explicit_service_survives_turn_interrupt_but_is_cancellable(self):
        command = self.script("import time\nprint('SERVICE READY', flush=True)\ntime.sleep(30)\n")
        stop = threading.Event()
        with command_scope(self.manager, stop.is_set):
            result = tool_run_command(command, timeout=0, background=True)
            self.assertIn("runtime_id=", result)
            rid = self.manager.list()[0].runtime_id
            wait_until(lambda: "SERVICE READY" in self.manager.output(rid))
            stop.set()
            self.manager.cancel_commands(include_background=False)
            self.assertIn(self.manager.get(rid).status, ACTIVE)
            self.assertIn("Cancellation requested", tool_runtime_cancel(rid))
        self.assertEqual(self.finished(rid).status, RuntimeStatus.CANCELLED)

    def test_cancel_before_spawn_never_runs_command(self):
        command = self.script("from pathlib import Path\nPath('should-not-exist').touch()\n")
        rid = self.manager.submit_command(command, should_interrupt=lambda: True)
        self.assertEqual(self.finished(rid).status, RuntimeStatus.CANCELLED)
        self.assertFalse((self.root / "should-not-exist").exists())

    def test_foreground_command_cancelled_even_after_yield(self):
        command = self.script("import time\ntime.sleep(30)\n")
        with command_scope(self.manager):
            tool_run_command(command, yield_time_ms=0)
        rid = self.manager.list()[0].runtime_id
        self.manager.cancel_commands(include_background=False)
        self.assertEqual(self.finished(rid).status, RuntimeStatus.CANCELLED)

    def test_shutdown_stops_explicit_service(self):
        command = self.script("import time\nprint('READY', flush=True)\ntime.sleep(30)\n")
        rid = self.manager.submit_command(command, timeout=0)
        wait_until(lambda: "READY" in self.manager.output(rid))
        self.manager.shutdown()
        self.assertEqual(self.manager.get(rid).status, RuntimeStatus.CANCELLED)
        with self.assertRaisesRegex(RuntimeError, "shut down"):
            self.manager.submit_command(command)

    def test_nonzero_exit_and_missing_workdir_are_terminal_failures(self):
        command = self.script("raise SystemExit(7)\n")
        rid = self.manager.submit_command(command)
        self.assertEqual(self.finished(rid).exit_code, 7)
        self.assertEqual(self.manager.get(rid).status, RuntimeStatus.FAILED)
        rid = self.manager.submit_command(command, work_dir=self.root / "missing")
        self.assertEqual(self.finished(rid).status, RuntimeStatus.FAILED)

    def test_invalid_timeout_or_wait_does_not_spawn(self):
        command = self.script("raise AssertionError('should not execute')\n")
        with command_scope(self.manager):
            for kwargs in ({"timeout": 0}, {"timeout": -1}, {"timeout": float('nan')},
                           {"timeout": True}, {"timeout": 86401}, {"yield_time_ms": -1},
                           {"yield_time_ms": 10001}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    tool_run_command(command, **kwargs)
        self.assertFalse(self.manager.list())

    def test_command_capacity_prevents_unbounded_spawning(self):
        self.manager.max_commands = 1
        command = self.script("import time\ntime.sleep(30)\n")
        self.manager.submit_command(command)
        with self.assertRaisesRegex(RuntimeError, "At most"):
            self.manager.submit_command(command)

    def test_legacy_running_records_are_marked_lost_on_restart(self):
        path = self.manager.root / "old.json"
        path.write_text(json.dumps({"runtime_id": "old", "status": "running"}), encoding="utf-8")
        restored = RuntimeTaskManager(root=self.manager.root, work_dir=self.root)
        try:
            self.assertEqual(restored.get("old").status, RuntimeStatus.LOST)
            self.assertIn("not resumed", restored.get("old").error)
        finally:
            restored.shutdown()

    def test_new_session_can_cancel_live_owned_task_but_shutdown_is_scoped(self):
        command = self.script("import time\ntime.sleep(30)\n")
        restored = RuntimeTaskManager(root=self.manager.root, work_dir=self.root)
        rid = self.manager.submit_command(command)
        try:
            self.assertIn(restored.get(rid).status, ACTIVE)
            restored.shutdown()
            self.assertIn(self.manager.get(rid).status, ACTIVE)
            self.assertIn("Cancellation requested", restored.cancel(rid))
            self.assertEqual(self.finished(rid).status, RuntimeStatus.CANCELLED)
        finally:
            restored.shutdown()

    def test_persistence_failure_does_not_leave_running_task(self):
        command = self.script("print('DONE')\n")
        with patch.object(self.manager, '_store_output', side_effect=OSError('disk unavailable')):
            rid = self.manager.submit_command(command)
            self.assertEqual(self.finished(rid).status, RuntimeStatus.FAILED)
            self.assertIn("persistence failed", self.manager.get(rid).error)

    def test_concurrent_session_polling_does_not_break_state_persistence(self):
        reader = RuntimeTaskManager(root=self.manager.root, work_dir=self.root)
        command = self.script("import time\nprint('LIVE', flush=True)\ntime.sleep(.1)\n")
        try:
            for _ in range(10):
                rid = self.manager.submit_command(command)
                deadline = time.monotonic() + 4
                while reader.get(rid).status in ACTIVE and time.monotonic() < deadline:
                    time.sleep(.002)
                self.assertEqual(reader.get(rid).status, RuntimeStatus.DONE, self.manager.output(rid))
        finally:
            reader.shutdown()

    def test_healthy_waits_do_not_trigger_loop_warning(self):
        history = [{"tool": "tool_runtime_wait", "args": {"runtime_id": "command_1"},
                    "result": "[runtime_id=command_1 status=running]"} for _ in range(8)]
        context = LoopDetectionMiddleware().process({"tool_calls_history": history})
        self.assertFalse(context.get("injections"))
        self.assertFalse(context.get("should_stop"))

    def test_subagent_deadline_interrupts_command_with_longer_timeout(self):
        from funharness.src.core.subagent import SubAgent
        command = self.script("import time\ntime.sleep(30)\n")
        subagent = SubAgent("test", llm_client=object())
        with patch("funharness.src.core.subagent.RuntimeTaskManager", return_value=self.manager), \
                patch.object(subagent, "_run", side_effect=lambda *args: tool_run_command(
                    command, timeout=900, yield_time_ms=10000)):
            start = time.monotonic()
            result = subagent.run("execute", timeout_seconds=.3)
        self.assertLess(time.monotonic() - start, 3)
        self.assertIn("Interrupted", result)
        self.assertEqual(self.manager.list()[0].status, RuntimeStatus.CANCELLED)

    @unittest.skipUnless(os.name == "nt", "Windows hidden console")
    def test_command_has_no_console_window(self):
        command = self.script("import ctypes\nprint('WINDOW',ctypes.windll.kernel32.GetConsoleWindow())\n")
        rid = self.manager.submit_command(command)
        self.assertEqual(self.finished(rid).status, RuntimeStatus.DONE)
        self.assertIn("WINDOW 0", self.manager.output(rid))

    @unittest.skipUnless(os.name == "nt", "Windows process ownership")
    def test_shell_exits_with_inherited_pipe_child_without_hanging_or_orphaning(self):
        child = self.root / "child.py"
        child.write_text("import time\nprint('CHILD OUTPUT', flush=True)\ntime.sleep(30)\n", encoding="utf-8")
        command = self.script("import subprocess,sys,time\nfrom pathlib import Path\n"
                              "p=subprocess.Popen([sys.executable, 'child.py'])\n"
                              "Path('child.pid').write_text(str(p.pid))\ntime.sleep(.2)\n")
        start = time.monotonic()
        rid = self.manager.submit_command(command, timeout=5)
        self.assertEqual(self.finished(rid).status, RuntimeStatus.DONE)
        self.assertLess(time.monotonic() - start, 3)
        pid = int((self.root / "child.pid").read_text())
        self.assertFalse(_windows_pid_exists(pid))

    @unittest.skipUnless(os.name == "nt", "Windows pipeline/job ownership")
    def test_timeout_kills_pipeline_and_grandchildren_with_partial_output(self):
        child = self.root / "child.py"
        child.write_text("import time\nprint('CHILD READY', flush=True)\ntime.sleep(30)\n", encoding="utf-8")
        command = self.script("import subprocess,sys,time\nfrom pathlib import Path\n"
                              "p=subprocess.Popen([sys.executable, 'child.py'])\n"
                              "Path('child.pid').write_text(str(p.pid))\nprint('PARENT READY',flush=True)\ntime.sleep(30)\n")
        command += ' 2>&1 | findstr READY'
        start = time.monotonic()
        rid = self.manager.submit_command(command, timeout=.7)
        self.assertEqual(self.finished(rid).status, RuntimeStatus.TIMED_OUT)
        self.assertLess(time.monotonic() - start, 3)
        self.assertFalse(_windows_pid_exists(int((self.root / "child.pid").read_text())))

    @unittest.skipUnless(os.name == "nt", "Windows backend crash containment")
    def test_backend_process_exit_kills_its_service_job(self):
        pid_file = self.root / "service.pid"
        service = self.root / "service.py"
        service.write_text(f"import os,time\nfrom pathlib import Path\nPath({str(pid_file)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n", encoding="utf-8")
        host = self.root / "host.py"
        repo = str(Path(__file__).resolve().parents[4])
        service_command = f'"{sys.executable}" "{service}"'
        host.write_text(f"import sys,time\nsys.path.insert(0,{repo!r})\n"
                        "from funharness.src.core.runtime import RuntimeTaskManager\n"
                        f"r=RuntimeTaskManager(root={str(self.root / 'host-runtime')!r},work_dir={str(self.root)!r})\n"
                        f"r.submit_command({service_command!r},timeout=0)\n"
                        "time.sleep(30)\n", encoding="utf-8")
        host_proc = subprocess.Popen([sys.executable, str(host)], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            wait_until(pid_file.exists)
            service_pid = int(pid_file.read_text())
            self.assertTrue(_windows_pid_exists(service_pid))
            host_proc.kill()
            host_proc.wait(timeout=3)
            wait_until(lambda: not _windows_pid_exists(service_pid))
        finally:
            if host_proc.poll() is None:
                host_proc.kill()
            host_proc.wait(timeout=3)
            host_proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
