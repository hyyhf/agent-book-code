from __future__ import annotations

import unittest

from funharness.src.channels import feishu, qqbot, weixin


class _FakeAttachments:
    def summary(self) -> str:
        return "(no attachments)"


class _FakeAgent:
    def __init__(self, *args, **kwargs) -> None:
        self.messages: list[str] = []
        self.attachments = _FakeAttachments()
        self.new_sessions = 0
        self.interrupted = False

    def handle_slash_command(self, cmd: str) -> str | None:
        if cmd == "/new":
            self.messages = []
            self.attachments = _FakeAttachments()
            self.new_sessions += 1
            return "[New Session] Previous session saved."
        return None

    def run(self, text: str) -> None:
        self.messages.append(text)

    def request_interrupt(self) -> None:
        self.interrupted = True


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []

    def send_text(
        self,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> None:
        self.sent.append((chat_id, text, reply_to_message_id))


class _FakeWeixinApi:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send_text(self, user_id: str, text: str, context_token: str = "") -> None:
        self.sent.append((user_id, text, context_token))


class ChannelNewCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_feishu_agent = feishu.FunHarnessAgent
        self._orig_qq_agent = qqbot.FunHarnessAgent
        self._orig_weixin_agent = weixin.FunHarnessAgent
        feishu.FunHarnessAgent = _FakeAgent
        qqbot.FunHarnessAgent = _FakeAgent
        weixin.FunHarnessAgent = _FakeAgent

    def tearDown(self) -> None:
        feishu.FunHarnessAgent = self._orig_feishu_agent
        qqbot.FunHarnessAgent = self._orig_qq_agent
        weixin.FunHarnessAgent = self._orig_weixin_agent

    def test_feishu_new_command_resets_current_chat_session(self) -> None:
        gateway = feishu.FeishuGateway(
            feishu.FeishuConfig(app_id="app", app_secret="secret"),
        )
        fake_client = _FakeFeishuClient()
        gateway.client = fake_client

        session = gateway._session_for("chat-a")
        session.agent.messages.append("old message")

        gateway._handle_message("chat-a", "msg-1", "/new")

        self.assertEqual(session.agent.messages, [])
        self.assertEqual(session.agent.new_sessions, 1)
        self.assertIn("[New Session]", fake_client.sent[-1][1])

    def test_qq_new_command_resets_current_chat_session(self) -> None:
        gateway = qqbot.QQBotGateway(
            qqbot.QQBotConfig(app_id="app", client_secret="secret"),
        )
        sent: list[tuple[str, str, str, str | None]] = []
        gateway._send_text_sync = (
            lambda scope, chat_id, text, reply_to=None:
            sent.append((scope, chat_id, text, reply_to))
        )

        session = gateway._session_for("c2c", "chat-a", "c2c:chat-a")
        session.agent.messages.append("old message")

        gateway._handle_message("c2c", "chat-a", "msg-1", "/new", "c2c:chat-a")

        self.assertEqual(session.agent.messages, [])
        self.assertEqual(session.agent.new_sessions, 1)
        self.assertIn("[New Session]", sent[-1][2])

    def test_weixin_new_command_resets_current_user_session(self) -> None:
        gateway = weixin.WeixinGateway(
            weixin.WeixinConfig(bot_token="token", base_url="https://example.test"),
        )
        fake_api = _FakeWeixinApi()
        gateway.api = fake_api

        session = gateway._session_for("user-a")
        session.agent.messages.append("old message")

        gateway._handle_message("user-a", "/new", "ctx-1")

        self.assertEqual(session.agent.messages, [])
        self.assertEqual(session.agent.new_sessions, 1)
        self.assertIn("[New Session]", fake_api.sent[-1][1])


if __name__ == "__main__":
    unittest.main()
