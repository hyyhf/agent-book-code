import sys
import tempfile
import time
import unittest
from pathlib import Path

from funharness.src.core.runtime import RuntimeStatus, RuntimeTaskManager


def wait_until(predicate, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out waiting for condition")


class RuntimeTaskManagerTests(unittest.TestCase):
    def test_cancel_running_command_marks_task_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RuntimeTaskManager(root=Path(tmp) / "runtime", work_dir=tmp)
            command = f'"{sys.executable}" -c "import time; time.sleep(10)"'
            runtime_id = manager.submit_command(command, timeout=30)

            wait_until(
                lambda: manager.get(runtime_id).status == RuntimeStatus.RUNNING,
            )
            message = manager.cancel(runtime_id)
            task = wait_until(
                lambda: manager.get(runtime_id)
                if manager.get(runtime_id).status == RuntimeStatus.CANCELLED
                else None,
            )
            output = manager.output(runtime_id)

        self.assertIn("Cancellation requested", message)
        self.assertEqual(task.status, RuntimeStatus.CANCELLED)
        self.assertIn("Interrupted", output)


if __name__ == "__main__":
    unittest.main()
