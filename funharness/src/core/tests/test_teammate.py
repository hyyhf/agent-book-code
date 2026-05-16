from __future__ import annotations

import threading
import time
import unittest
from dataclasses import dataclass

from funharness.src.core.teammate import TeammateCallbacks, TeammateRegistry, TeammateQueueTask, TeammateState


@dataclass
class Member:
    name: str = "writer"
    role: str = "writer"
    instructions: str = ""


class EchoRunner:
    calls: list[tuple[str, str]]

    def __init__(self, *args, **kwargs):
        self.calls = []

    def run(self, task: str, context: str = "", cancel_event: threading.Event | None = None) -> str:
        self.calls.append((task, context))
        return f"done: {task}"


class BlockingRunner:
    def __init__(self, *args, **kwargs):
        pass

    def run(self, task: str, context: str = "", cancel_event: threading.Event | None = None) -> str:
        deadline = time.time() + 2
        while cancel_event is not None and not cancel_event.is_set() and time.time() < deadline:
            time.sleep(0.01)
        return "cancel observed"


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


class TeammateRegistryTests(unittest.TestCase):
    def test_start_is_idempotent_and_tasks_reuse_worker(self) -> None:
        done: list[tuple[str, TeammateQueueTask, str, TeammateState]] = []
        registry = TeammateRegistry(TeammateCallbacks(on_done=lambda *args: done.append(args)), runner_factory=EchoRunner)
        member = Member()

        first = registry.start(member, model="test", llm_client=None, tool_registry=None)
        second = registry.start(member, model="test", llm_client=None, tool_registry=None)
        self.assertEqual(first.worker_id, second.worker_id)

        registry.submit_task(member, "one", context="", run_id="run", team_task_id="TR1", model="test", llm_client=None, tool_registry=None)
        registry.submit_task(member, "two", context="", run_id="run", team_task_id="TR2", model="test", llm_client=None, tool_registry=None)
        wait_until(lambda: len(done) == 2)

        self.assertEqual(done[0][2], "done: one")
        self.assertEqual(done[1][2], "done: two")
        self.assertEqual(registry.state("writer").worker_id, first.worker_id)  # type: ignore[union-attr]
        registry.shutdown("writer", force=True)

    def test_cancel_current_task_keeps_worker_alive(self) -> None:
        cancelled: list[tuple[str, TeammateQueueTask | None, str, TeammateState]] = []
        registry = TeammateRegistry(TeammateCallbacks(on_cancelled=lambda *args: cancelled.append(args)), runner_factory=BlockingRunner)
        member = Member()

        registry.submit_task(member, "slow", context="", run_id="run", team_task_id="TR1", model="test", llm_client=None, tool_registry=None)
        wait_until(lambda: (registry.state("writer") is not None and registry.state("writer").status == "working"))  # type: ignore[union-attr]
        self.assertTrue(registry.cancel("writer", "stop"))
        wait_until(lambda: len(cancelled) == 1)

        state = registry.state("writer")
        self.assertIsNotNone(state)
        assert state is not None
        wait_until(lambda: registry.state("writer") is not None and registry.state("writer").status == "idle")  # type: ignore[union-attr]
        self.assertTrue(registry.state("writer").worker_id)  # type: ignore[union-attr]
        registry.shutdown("writer", force=True)


if __name__ == "__main__":
    unittest.main()
