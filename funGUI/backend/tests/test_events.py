from __future__ import annotations

import unittest

from funGUI.backend.events import EventBus


class EventBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_and_publish_event(self) -> None:
        bus = EventBus()
        queue = await bus.connect("client-a")

        connected = await queue.get()
        self.assertEqual(connected.type, "connected")
        self.assertEqual(connected.payload["client_id"], "client-a")

        await bus.publish("status", {"message": "ready"})
        event = await queue.get()
        self.assertEqual(event.type, "status")
        self.assertEqual(event.payload["message"], "ready")

        await bus.disconnect("client-a")


if __name__ == "__main__":
    unittest.main()
