"""
Feishu/Lark channel gateway for FunHarness.

This module exposes an HTTP callback server that receives Feishu message
events, runs FunHarness locally, and sends progress/final messages back through
the Feishu bot API.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from funharness.src.agent import FunHarnessAgent


DEFAULT_API_BASE = "https://open.feishu.cn/open-apis"
DEFAULT_CALLBACK_PATH = "/feishu/events"
MAX_TEXT_CHARS = 3500


def _load_env() -> None:
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        load_dotenv(cwd_env)
        return
    d = Path(__file__).resolve().parent
    for _ in range(6):
        env = d / ".env"
        if env.exists():
            load_dotenv(env)
            return
        d = d.parent


@dataclass
class FeishuConfig:
    app_id: str
    app_secret: str
    verification_token: str = ""
    api_base: str = DEFAULT_API_BASE
    host: str = "0.0.0.0"
    port: int = 8787
    callback_path: str = DEFAULT_CALLBACK_PATH
    event_mode: str = "ws"
    permission_mode: str = "suggest"
    workspace: str = ""

    @classmethod
    def from_env(cls) -> "FeishuConfig":
        _load_env()
        app_id = os.getenv("FEISHU_APP_ID", "")
        app_secret = os.getenv("FEISHU_APP_SECRET", "")
        if not app_id or not app_secret:
            raise RuntimeError(
                "FEISHU_APP_ID and FEISHU_APP_SECRET are required."
            )

        return cls(
            app_id=app_id,
            app_secret=app_secret,
            verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", ""),
            api_base=os.getenv("FEISHU_API_BASE", DEFAULT_API_BASE).rstrip("/"),
            host=os.getenv("FEISHU_HOST", "0.0.0.0"),
            port=int(os.getenv("FEISHU_PORT", "8787")),
            callback_path=os.getenv("FEISHU_CALLBACK_PATH", DEFAULT_CALLBACK_PATH),
            event_mode=os.getenv("FEISHU_EVENT_MODE", "ws").lower(),
            permission_mode=os.getenv("FEISHU_PERMISSION_MODE", "suggest"),
            workspace=os.getenv("FEISHU_WORKSPACE", ""),
        )


class FeishuClient:
    def __init__(self, config: FeishuConfig):
        self.config = config
        self._tenant_token = ""
        self._tenant_token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def _request_json(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=body, method=method, headers=headers or {})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def tenant_access_token(self) -> str:
        with self._token_lock:
            if self._tenant_token and time.time() < self._tenant_token_expires_at:
                return self._tenant_token

            url = f"{self.config.api_base}/auth/v3/tenant_access_token/internal"
            data = self._request_json(
                "POST",
                url,
                {
                    "app_id": self.config.app_id,
                    "app_secret": self.config.app_secret,
                },
                {"Content-Type": "application/json"},
            )
            if data.get("code", 0) != 0:
                raise RuntimeError(f"Feishu token failed: {data}")
            token = data.get("tenant_access_token", "")
            if not token:
                raise RuntimeError("Feishu token response missing tenant_access_token")
            self._tenant_token = token
            expire = int(data.get("expire", 7200))
            self._tenant_token_expires_at = time.time() + max(60, expire - 120)
            return token

    def send_text(
        self,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> None:
        text = text or "(empty)"
        chunks = [
            text[i:i + MAX_TEXT_CHARS]
            for i in range(0, len(text), MAX_TEXT_CHARS)
        ] or ["(empty)"]

        for index, chunk in enumerate(chunks):
            if index > 0:
                chunk = f"(continued)\n{chunk}"
            self._send_text_chunk(chat_id, chunk, reply_to_message_id)

    def _send_text_chunk(
        self,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> None:
        token = self.tenant_access_token()
        content = json.dumps({"text": text}, ensure_ascii=False)
        if reply_to_message_id:
            url = f"{self.config.api_base}/im/v1/messages/{reply_to_message_id}/reply"
            payload: dict[str, Any] = {
                "msg_type": "text",
                "content": content,
                "uuid": str(uuid.uuid4()),
            }
        else:
            url = f"{self.config.api_base}/im/v1/messages?receive_id_type=chat_id"
            payload = {
                "receive_id": chat_id,
                "msg_type": "text",
                "content": content,
                "uuid": str(uuid.uuid4()),
            }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        data = self._request_json("POST", url, payload, headers)
        if data.get("code", 0) != 0:
            raise RuntimeError(f"Feishu send failed: {data}")


def _content_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        try:
            content_data = json.loads(content)
        except json.JSONDecodeError:
            return content.strip()
    elif isinstance(content, dict):
        content_data = content
    else:
        return str(content).strip()

    text = content_data.get("text")
    if text is not None:
        return str(text).strip()
    return json.dumps(content_data, ensure_ascii=False)


def _strip_mentions(text: str) -> str:
    import re

    text = re.sub(r"<at\s+[^>]*>.*?</at>", "", text)
    text = re.sub(r"@\S+\s*", "", text)
    return text.strip()


class FeishuAgentSession:
    def __init__(self, client: FeishuClient, chat_id: str, mode: str):
        self.client = client
        self.chat_id = chat_id
        self.agent = FunHarnessAgent(
            mode=mode,
            on_token=self._on_token,
            on_tool_call=self._on_tool_call,
            on_tool_result=self._on_tool_result,
            on_status=self._on_status,
            on_approval=self._on_approval,
        )
        self.lock = threading.Lock()
        self._final_buffer: list[str] = []
        self._last_status_at = 0.0

    def run(self, text: str, reply_to: str | None = None) -> None:
        if not self.lock.acquire(blocking=False):
            self.client.send_text(
                self.chat_id,
                "FunHarness is still working on the previous request. Send /interrupt to stop it.",
                reply_to,
            )
            return

        try:
            self._final_buffer = []
            self.client.send_text(self.chat_id, "FunHarness received it. Working locally...", reply_to)
            self.agent.run(text)
            final = "".join(self._final_buffer).strip()
            if final:
                self.client.send_text(self.chat_id, final, reply_to)
            else:
                self.client.send_text(self.chat_id, "Done.", reply_to)
        except InterruptedError:
            self.client.send_text(self.chat_id, "Interrupted.", reply_to)
        except Exception as e:
            self.client.send_text(self.chat_id, f"FunHarness error: {e}", reply_to)
        finally:
            self.lock.release()

    def interrupt(self) -> None:
        self.agent.request_interrupt()

    def _on_token(self, token: str) -> None:
        self._final_buffer.append(token)

    def _on_status(self, msg: str) -> None:
        now = time.time()
        if now - self._last_status_at < 1.5:
            return
        self._last_status_at = now
        self.client.send_text(self.chat_id, f"[status] {msg}")

    def _on_tool_call(self, name: str, preview: str, risk: str) -> None:
        if len(preview) > 280:
            preview = preview[:280] + "..."
        self.client.send_text(
            self.chat_id,
            f"[tool] {name} ({risk})\nargs: {preview}",
        )

    def _on_tool_result(self, name: str, result: str, hook_feedback: str, display=None) -> None:
        text = result if len(result) <= 500 else result[:500] + "...(truncated)"
        if hook_feedback:
            text += f"\n[hook] {hook_feedback}"
        self.client.send_text(self.chat_id, f"[tool result] {name}\n{text}")

    def _on_approval(self, tool_name: str, arguments: dict[str, Any], reason: str):
        self.client.send_text(
            self.chat_id,
            (
                f"[approval required] {tool_name}\n"
                f"{reason}\n"
                "Remote approval is not interactive yet. Set FEISHU_PERMISSION_MODE=auto "
                "in a trusted workspace if you want this channel to execute write/shell tools."
            ),
        )
        return False, ""


class FeishuGateway:
    def __init__(self, config: FeishuConfig):
        self.config = config
        self.client = FeishuClient(config)
        self.sessions: dict[str, FeishuAgentSession] = {}
        self._sessions_lock = threading.Lock()
        self._seen_events: dict[str, float] = {}
        self._seen_lock = threading.Lock()

    def serve_forever(self) -> None:
        if self.config.event_mode in {"ws", "websocket", "long_connection"}:
            self.serve_websocket()
        elif self.config.event_mode == "http":
            self.serve_http()
        else:
            raise RuntimeError(
                "FEISHU_EVENT_MODE must be 'ws' for long connection or 'http' for callback."
            )

    def _prepare_workspace(self) -> None:
        if self.config.workspace:
            Path(self.config.workspace).mkdir(parents=True, exist_ok=True)
            os.chdir(self.config.workspace)

    def serve_websocket(self) -> None:
        self._prepare_workspace()
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import P2ImMessageReceiveV1  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "Feishu long connection mode requires the official SDK. "
                "Install it with: uv add lark-oapi  (or pip install lark-oapi)"
            ) from e

        def on_message(data) -> None:
            try:
                raw = json.loads(lark.JSON.marshal(data))
            except Exception:
                raw = data if isinstance(data, dict) else {}
            self._handle_ws_event(raw)

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )
        client = lark.ws.Client(
            self.config.app_id,
            self.config.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )
        print("FunHarness Feishu gateway starting in long connection mode.")
        print("Keep this process running, then click Verify/Save in Feishu.")
        client.start()

    def serve_http(self) -> None:
        self._prepare_workspace()
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                gateway._handle_http(self)

            def do_GET(self):
                gateway._write_json(self, 200, {"ok": True, "channel": "feishu"})

            def log_message(self, fmt, *args):
                print(f"[feishu] {self.address_string()} - {fmt % args}")

        server = ThreadingHTTPServer((self.config.host, self.config.port), Handler)
        print(
            f"FunHarness Feishu gateway listening on "
            f"http://{self.config.host}:{self.config.port}{self.config.callback_path}"
        )
        server.serve_forever()

    def _handle_ws_event(self, payload: dict[str, Any]) -> None:
        event = payload.get("event", payload)
        message = event.get("message", {})
        chat_id = message.get("chat_id", "")
        message_id = message.get("message_id", "")
        message_type = message.get("message_type", "")
        if not chat_id or message_type != "text":
            return
        text = _strip_mentions(_content_text(message))
        threading.Thread(
            target=self._handle_message,
            args=(chat_id, message_id, text),
            daemon=True,
        ).start()

    def _handle_http(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.path.split("?", 1)[0] != self.config.callback_path:
            self._write_json(handler, 404, {"error": "not found"})
            return

        length = int(handler.headers.get("Content-Length", "0"))
        raw = handler.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._write_json(handler, 400, {"error": "invalid json"})
            return

        if "encrypt" in payload:
            self._write_json(
                handler,
                400,
                {"error": "encrypted callbacks are not supported yet"},
            )
            return

        token = payload.get("token") or payload.get("header", {}).get("token", "")
        if self.config.verification_token and token != self.config.verification_token:
            self._write_json(handler, 403, {"error": "bad verification token"})
            return

        if payload.get("type") == "url_verification":
            self._write_json(handler, 200, {"challenge": payload.get("challenge", "")})
            return

        event_id = payload.get("header", {}).get("event_id") or payload.get("uuid", "")
        if event_id and self._is_duplicate(event_id):
            self._write_json(handler, 200, {"ok": True, "duplicate": True})
            return

        event_type = payload.get("header", {}).get("event_type") or payload.get("type")
        if event_type != "im.message.receive_v1":
            self._write_json(handler, 200, {"ok": True, "ignored": event_type})
            return

        event = payload.get("event", {})
        message = event.get("message", {})
        chat_id = message.get("chat_id", "")
        message_id = message.get("message_id", "")
        message_type = message.get("message_type", "")
        if not chat_id:
            self._write_json(handler, 200, {"ok": True, "ignored": "missing chat_id"})
            return

        if message_type != "text":
            self.client.send_text(chat_id, f"Unsupported message type: {message_type}", message_id)
            self._write_json(handler, 200, {"ok": True})
            return

        text = _strip_mentions(_content_text(message))
        self._write_json(handler, 200, {"ok": True})
        threading.Thread(
            target=self._handle_message,
            args=(chat_id, message_id, text),
            daemon=True,
        ).start()

    def _is_duplicate(self, event_id: str) -> bool:
        now = time.time()
        with self._seen_lock:
            old = [eid for eid, ts in self._seen_events.items() if now - ts > 3600]
            for eid in old:
                self._seen_events.pop(eid, None)
            if event_id in self._seen_events:
                return True
            self._seen_events[event_id] = now
            return False

    def _handle_message(self, chat_id: str, message_id: str, text: str) -> None:
        if not text:
            self.client.send_text(chat_id, "Send text for FunHarness to work on.", message_id)
            return

        if text.strip().lower() in {"/help", "help"}:
            self.client.send_text(
                chat_id,
                "FunHarness Feishu commands:\n"
                "/help - show this message\n"
                "/interrupt - stop the current local agent run\n\n"
                "Send any other text to run FunHarness locally.",
                message_id,
            )
            return

        session = self._session_for(chat_id)
        if text.strip().lower() in {"/interrupt", "interrupt", "stop"}:
            session.interrupt()
            self.client.send_text(chat_id, "Interrupt requested.", message_id)
            return

        session.run(text, message_id)

    def _session_for(self, chat_id: str) -> FeishuAgentSession:
        with self._sessions_lock:
            session = self.sessions.get(chat_id)
            if session is None:
                session = FeishuAgentSession(
                    self.client,
                    chat_id,
                    self.config.permission_mode,
                )
                self.sessions[chat_id] = session
            return session

    @staticmethod
    def _write_json(
        handler: BaseHTTPRequestHandler,
        status: int,
        data: dict[str, Any],
    ) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the FunHarness Feishu gateway")
    parser.add_argument("--host", help="Override FEISHU_HOST")
    parser.add_argument("--port", type=int, help="Override FEISHU_PORT")
    parser.add_argument("--path", help="Override FEISHU_CALLBACK_PATH")
    parser.add_argument("--workspace", help="Override FEISHU_WORKSPACE")
    args = parser.parse_args(argv)

    config = FeishuConfig.from_env()
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.path:
        config.callback_path = args.path
    if args.workspace:
        config.workspace = args.workspace

    FeishuGateway(config).serve_forever()


if __name__ == "__main__":
    main()
