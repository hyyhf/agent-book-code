from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from funharness.src.core.schedule import ScheduleManager


class ScheduleManagerTests(unittest.TestCase):
    def test_due_schedule_starts_runtime_callback_and_records_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedules.json"
            runtime_ids: list[str] = []

            def on_fire(record) -> str:
                runtime_id = f"schedule_{record.schedule_id}"
                runtime_ids.append(runtime_id)
                return runtime_id

            manager = ScheduleManager(path=path, on_fire=on_fire)
            record = manager.create(
                "review",
                datetime.fromtimestamp(time.time() - 1).isoformat(timespec="seconds"),
                "Review the report.",
            )

            fired = manager.check_due()
            notifications = manager.drain_notifications()
            restored = ScheduleManager(path=path)
            restored_record = restored.list()[0]

        self.assertEqual([item.schedule_id for item in fired], [record.schedule_id])
        self.assertEqual(runtime_ids, [f"schedule_{record.schedule_id}"])
        self.assertFalse(restored_record.enabled)
        self.assertEqual(restored_record.last_runtime_id, f"schedule_{record.schedule_id}")
        self.assertEqual(notifications[0]["runtime_id"], f"schedule_{record.schedule_id}")

    def test_trigger_runs_existing_schedule_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedules.json"

            def on_fire(record) -> str:
                return f"manual_{record.schedule_id}"

            manager = ScheduleManager(path=path, on_fire=on_fire)
            record = manager.create("review", "in 1h", "Review the report.")

            triggered = manager.trigger(record.schedule_id)
            notifications = manager.drain_notifications()
            restored = ScheduleManager(path=path)
            restored_record = restored.list()[0]

        self.assertIsNotNone(triggered)
        self.assertEqual(restored_record.last_runtime_id, f"manual_{record.schedule_id}")
        self.assertEqual(notifications[0]["runtime_id"], f"manual_{record.schedule_id}")


if __name__ == "__main__":
    unittest.main()
