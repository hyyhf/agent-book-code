"""
QQ Bot channel gateway for FunHarness.

This module connects to the QQ Bot WebSocket gateway via ``qqbot-agent-sdk``,
receives C2C / group / guild messages, runs FunHarness locally, and sends
progress/final messages back through the QQ Bot REST API.

Supports:
- Text messaging (send / receive)
- File receiving: images, documents, audio, video from QQ users are downloaded
  to a local cache and registered with the FunHarness AttachmentManager so the
  agent can read and process them.
- File sending: when the agent produces files (via tool results), they can be
  uploaded and sent back to the user as QQ rich media messages.

Environment variables
---------------------
- QQ_APP_ID        (required)  Bot application ID
- QQ_CLIENT_SECRET (required)  Bot client secret
- QQ_PERMISSION_MODE           "suggest" | "auto" (default: suggest)
- QQ_WORKSPACE                 Working directory for the agent
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import mimetypes
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from funharness.src.agent import FunHarnessAgent

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 3500

# File extensions considered "sendable" when detected in tool output
_SENDABLE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
    ".pdf", ".docx", ".xlsx", ".pptx", ".zip", ".tar", ".gz",
    ".txt", ".csv", ".json", ".xml", ".html", ".md",
    ".mp3", ".wav", ".ogg", ".mp4", ".avi", ".mov",
}

# Regex to detect file paths in tool results (absolute or relative paths)
_FILE_PATH_RE = re.compile(
    r"(?:^|[\s:=])("
    r"(?:[A-Za-z]:\\|/)[^\s\n\"'<>|*?]+"  # absolute paths
    r"|\.{1,2}/[^\s\n\"'<>|*?]+"          # relative paths
    r")",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# .env loading (same strategy as feishu.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class QQBotConfig:
    app_id: str
    client_secret: str
    permission_mode: str = "suggest"
    workspace: str = ""

    @classmethod
    def from_env(cls) -> "QQBotConfig":
        _load_env()
        app_id = os.getenv("QQ_APP_ID", "")
        client_secret = os.getenv("QQ_CLIENT_SECRET", "")
        if not app_id or not client_secret:
            raise RuntimeError(
                "QQ_APP_ID and QQ_CLIENT_SECRET are required. "
                "Set them in .env or as environment variables."
            )
        return cls(
            app_id=app_id,
            client_secret=client_secret,
            permission_mode=os.getenv("QQ_PERMISSION_MODE", "suggest"),
            workspace=os.getenv("QQ_WORKSPACE", ""),
        )


# ---------------------------------------------------------------------------
# Async helper -- run a coroutine from a sync context
# ---------------------------------------------------------------------------

def _run_async(loop: asyncio.AbstractEventLoop, coro):
    """Submit a coroutine to *loop* from any thread and wait for the result."""
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=120)


# ---------------------------------------------------------------------------
# File type detection
# ---------------------------------------------------------------------------

def _guess_media_type(file_path: str) -> int:
    """Map a local file path to a QQ media type constant.

    Returns:
        1 = image, 2 = video, 3 = voice, 4 = file (generic)
    """
    from qqbot_agent_sdk import (
        MEDIA_TYPE_IMAGE,
        MEDIA_TYPE_VIDEO,
        MEDIA_TYPE_VOICE,
        MEDIA_TYPE_FILE,
    )
    mime = mimetypes.guess_type(file_path)[0] or ""
    if mime.startswith("image/"):
        return MEDIA_TYPE_IMAGE
    if mime.startswith("video/"):
        return MEDIA_TYPE_VIDEO
    if mime.startswith("audio/"):
        return MEDIA_TYPE_VOICE
    return MEDIA_TYPE_FILE


def _extract_file_paths(text: str) -> List[str]:
    """Extract plausible file paths from a string, returning only those that
    exist on disk and have sendable extensions."""
    paths = []
    for match in _FILE_PATH_RE.finditer(text):
        candidate = match.group(1).strip().rstrip(".,;:)")
        p = Path(candidate)
        if p.suffix.lower() in _SENDABLE_EXTENSIONS and p.is_file():
            paths.append(str(p.resolve()))
    return paths


# ---------------------------------------------------------------------------
# Agent session (one per chat_id, same pattern as feishu.py)
# ---------------------------------------------------------------------------

class QQBotAgentSession:
    """Manages a FunHarnessAgent instance for one chat conversation."""

    def __init__(
        self,
        api_client: Any,  # QQApiClient
        http_client: Any,  # httpx.AsyncClient for media uploads
        chat_scope: str,
        chat_id: str,
        mode: str,
        loop: asyncio.AbstractEventLoop,
    ):
        self.api = api_client
        self.http_client = http_client
        self.chat_scope = chat_scope
        self.chat_id = chat_id
        self.loop = loop
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
        self._pending_files: list[str] = []

    # -- public API (called from worker threads) ---------------------------

    def run(self, text: str, reply_to: str | None = None) -> None:
        if not self.lock.acquire(blocking=False):
            self._send_text(
                "FunHarness is still working on the previous request. "
                "Send /interrupt to stop it.",
                reply_to,
            )
            return

        try:
            self._final_buffer = []
            self._pending_files = []
            self._send_text(
                "FunHarness received it. Working locally...", reply_to,
            )
            self.agent.run(text)
            final = "".join(self._final_buffer).strip()
            if final:
                self._send_text(final, reply_to)
            else:
                self._send_text("Done.", reply_to)

            # Send any files that were produced during the agent run
            self._flush_pending_files(reply_to)
        except InterruptedError:
            self._send_text("Interrupted.", reply_to)
        except Exception as e:
            self._send_text(f"FunHarness error: {e}", reply_to)
        finally:
            self.lock.release()

    def send_file(self, file_path: str, reply_to: str | None = None) -> None:
        """Upload and send a file to the current chat."""
        if self.chat_scope == "guild":
            self._send_text(
                f"[file output] {Path(file_path).name}\n"
                "(Guild channels do not support rich media upload via bot API. "
                "The file is available in the workspace.)",
                reply_to,
            )
            return

        try:
            media_type = _guess_media_type(file_path)
            file_info = _run_async(
                self.loop,
                self._upload_file(file_path, media_type),
            )
            _run_async(
                self.loop,
                self._send_rich_media(file_info, reply_to),
            )
            logger.info(
                "[QQBot] File sent: %s -> %s:%s",
                Path(file_path).name, self.chat_scope, self.chat_id,
            )
        except Exception as exc:
            logger.error("[QQBot] Failed to send file %s: %s", file_path, exc)
            self._send_text(
                f"[file] {Path(file_path).name} - upload failed: {exc}",
                reply_to,
            )

    def interrupt(self) -> None:
        self.agent.request_interrupt()

    # -- file upload helpers -----------------------------------------------

    async def _upload_file(self, file_path: str, media_type: int) -> str:
        """Upload a local file and return the file_info token."""
        from qqbot_agent_sdk import MediaUploader

        uploader = MediaUploader(
            api_client=self.api,
            http_client=self.http_client,
            log_tag="FunHarness-QQ",
        )
        return await uploader.upload(
            chat_type=self.chat_scope,
            chat_id=self.chat_id,
            source=file_path,
            file_type=media_type,
        )

    async def _send_rich_media(
        self, file_info: str, reply_to: str | None = None,
    ) -> None:
        """Send a rich media message using an uploaded file_info token."""
        from qqbot_agent_sdk.dto import (
            MessageToCreate,
            MediaInfo,
            QQMessageType,
        )

        msg = MessageToCreate(
            msg_type=QQMessageType.RICH_MEDIA,
            msg_seq=self.api.next_msg_seq(),
            msg_id=reply_to or "",
            media=MediaInfo(file_info=file_info),
        )
        if self.chat_scope == "c2c":
            await self.api.post_c2c_message(self.chat_id, msg)
        elif self.chat_scope == "group":
            await self.api.post_group_message(self.chat_id, msg)

    # -- callbacks ---------------------------------------------------------

    def _on_token(self, token: str) -> None:
        self._final_buffer.append(token)

    def _on_status(self, msg: str) -> None:
        now = time.time()
        if now - self._last_status_at < 1.5:
            return
        self._last_status_at = now
        self._send_text(f"[status] {msg}")

    def _on_tool_call(self, name: str, preview: str, risk: str) -> None:
        if len(preview) > 280:
            preview = preview[:280] + "..."
        self._send_text(f"[tool] {name} ({risk})\nargs: {preview}")

    def _on_tool_result(
        self, name: str, result: str, hook_feedback: str, display=None,
    ) -> None:
        text = (
            result if len(result) <= 500
            else result[:500] + "...(truncated)"
        )
        if hook_feedback:
            text += f"\n[hook] {hook_feedback}"
        self._send_text(f"[tool result] {name}\n{text}")

        # Detect file paths in tool results for later sending
        file_paths = _extract_file_paths(result)
        for fp in file_paths:
            if fp not in self._pending_files:
                self._pending_files.append(fp)

    def _on_approval(
        self, tool_name: str, arguments: dict[str, Any], reason: str,
    ):
        self._send_text(
            f"[approval required] {tool_name}\n"
            f"{reason}\n"
            "Remote approval is not interactive yet. Set QQ_PERMISSION_MODE=auto "
            "in a trusted workspace if you want this channel to execute "
            "write/shell tools.",
        )
        return False, ""

    def _flush_pending_files(self, reply_to: str | None = None) -> None:
        """Send all files that were collected during the agent run."""
        if not self._pending_files:
            return
        for fp in self._pending_files:
            self.send_file(fp, reply_to)
        self._pending_files = []

    # -- send helper -------------------------------------------------------

    def _send_text(
        self, text: str, reply_to: str | None = None,
    ) -> None:
        """Send text message via QQ Bot API.

        Handles long messages by chunking, and bridges async send_text
        into the main asyncio loop from the agent worker thread.
        """
        text = text or "(empty)"
        chunks = [
            text[i:i + MAX_TEXT_CHARS]
            for i in range(0, len(text), MAX_TEXT_CHARS)
        ] or ["(empty)"]

        for index, chunk in enumerate(chunks):
            if index > 0:
                chunk = f"(continued)\n{chunk}"
            try:
                _run_async(
                    self.loop,
                    self.api.send_text(
                        self.chat_scope,
                        self.chat_id,
                        chunk,
                        reply_to=reply_to,
                        markdown=True,
                    ),
                )
            except Exception as exc:
                logger.error(
                    "[QQBot] Failed to send message: %s", exc,
                )


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------

class QQBotGateway:
    """Manages the WebSocket lifecycle and dispatches messages to agent sessions."""

    def __init__(self, config: QQBotConfig):
        self.config = config
        self._sessions: Dict[str, QQBotAgentSession] = {}
        self._sessions_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # SDK objects (created in serve_forever)
        self._api_client: Any = None
        self._ws_client: Any = None
        self._http_client: Any = None

        # Attachment pipeline
        self._attachment_downloader: Any = None
        self._attachment_processor: Any = None
        self._cache_dir = Path(".funharness/qq_attachments")

        # WebSocket session state
        self._session_id: Optional[str] = None
        self._last_seq: Optional[int] = None
        self._heartbeat_interval: float = 30.0

    def serve_forever(self) -> None:
        """Start the gateway. Blocks until interrupted."""
        self._prepare_workspace()
        asyncio.run(self._run_async())

    def shutdown(self) -> None:
        """Stop the gateway gracefully."""
        if self._ws_client is not None:
            try:
                loop = self._loop
                if loop and not loop.is_closed():
                    future = asyncio.run_coroutine_threadsafe(
                        self._ws_client.async_stop(), loop,
                    )
                    future.result(timeout=10)
            except Exception:
                pass
            self._ws_client = None

    def _prepare_workspace(self) -> None:
        if self.config.workspace:
            Path(self.config.workspace).mkdir(parents=True, exist_ok=True)
            os.chdir(self.config.workspace)

    # -- main async loop ---------------------------------------------------

    async def _run_async(self) -> None:
        from qqbot_agent_sdk import (
            QQApiClient,
            QQWebSocket,
            WSCallbacks,
            EventParser,
            InboundEvent,
        )
        from qqbot_agent_sdk.attachment import (
            AttachmentDownloader,
            AttachmentProcessor,
        )
        import httpx

        self._loop = asyncio.get_running_loop()

        # 1. Create the API client and obtain token + gateway URL
        api = QQApiClient(
            app_id=self.config.app_id,
            client_secret=self.config.client_secret,
            log_tag="FunHarness-QQ",
        )
        self._api_client = api

        # Setup httpx async client for API requests and file downloads
        self._http_client = httpx.AsyncClient(timeout=60.0)
        api.setup(self._http_client)

        # 2. Setup attachment download pipeline
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._attachment_downloader = AttachmentDownloader(
            http_client=self._http_client,
            cache_dir=str(self._cache_dir),
            log_tag="FunHarness-QQ",
        )
        self._attachment_processor = AttachmentProcessor(
            downloader=self._attachment_downloader,
        )

        await api.ensure_token()
        gateway_url = await api.get_gateway_url()
        logger.info("[QQBot] Gateway URL: %s", gateway_url)

        # 3. Build WSCallbacks -- bridge between SDK and our handler
        callbacks = WSCallbacks(
            on_message_event=self._on_message_event,
            on_connected=self._on_connected,
            on_disconnected=self._on_disconnected,
            on_fatal_error=self._on_fatal_error,
            get_token=api.ensure_token_sync,
            get_session=self._get_session,
            set_session=self._set_session,
            set_heartbeat_interval=self._set_heartbeat_interval,
            clear_token=api.clear_token,
            fail_pending=self._fail_pending,
            get_gateway_url=api.get_gateway_url_sync,
        )

        # 4. Create and start the WebSocket
        ws = QQWebSocket(callbacks=callbacks, log_tag="FunHarness-QQ")
        self._ws_client = ws
        ws.start(gateway_url, self._loop)

        print("FunHarness QQ Bot gateway started.")
        print(f"  app_id     = {self.config.app_id}")
        print(f"  mode       = {self.config.permission_mode}")
        print(f"  workspace  = {self.config.workspace or '(cwd)'}")
        print(f"  cache      = {self._cache_dir}")
        print("Keep this process running. Send messages to the bot in QQ.")
        print("Supported: text, images, documents, audio, video.")

        # Keep the main loop alive
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await ws.async_stop()
            await self._http_client.aclose()

    # -- WSCallbacks implementation ----------------------------------------

    async def _on_message_event(
        self, event_type: str, data: Dict[str, Any],
    ) -> None:
        from qqbot_agent_sdk import EventParser

        event = EventParser.parse(event_type, data)
        if event is None:
            logger.debug("[QQBot] Unsupported event type: %s", event_type)
            return

        chat_key = f"{event.chat_scope}:{event.chat_id}"
        text = event.content.strip()

        # Process attachments asynchronously (download to local cache)
        attachment_descriptions: list[str] = []
        local_file_paths: list[str] = []
        if event.attachments:
            try:
                processed = await self._attachment_processor.process(
                    event.attachments,
                )
                for pa in processed:
                    if pa.description:
                        attachment_descriptions.append(pa.description)
                    if pa.local_path:
                        local_file_paths.append(pa.local_path)
                logger.info(
                    "[QQBot] Processed %d attachment(s): %s",
                    len(processed),
                    ", ".join(pa.kind for pa in processed),
                )
            except Exception as exc:
                logger.error(
                    "[QQBot] Attachment processing failed: %s", exc,
                )

        logger.info(
            "[QQBot] [%s] %s: %s (attachments=%d)",
            event.chat_scope, event.user_id, text[:100],
            len(event.attachments),
        )

        threading.Thread(
            target=self._handle_message,
            args=(
                event.chat_scope,
                event.chat_id,
                event.message_id,
                text,
                chat_key,
                attachment_descriptions,
                local_file_paths,
            ),
            daemon=True,
        ).start()

    def _on_connected(self) -> None:
        logger.info("[QQBot] WebSocket connected")

    def _on_disconnected(self) -> None:
        logger.warning("[QQBot] WebSocket disconnected")

    def _on_fatal_error(self, error_code: str, message: str) -> None:
        logger.error("[QQBot] Fatal error: %s - %s", error_code, message)

    def _get_session(self) -> Tuple[Optional[str], Optional[int]]:
        return self._session_id, self._last_seq

    def _set_session(
        self, session_id: Optional[str], last_seq: Optional[int],
    ) -> None:
        self._session_id = session_id
        self._last_seq = last_seq

    def _set_heartbeat_interval(self, interval: float) -> None:
        self._heartbeat_interval = interval

    def _fail_pending(self, reason: str) -> None:
        logger.debug("[QQBot] fail_pending: %s", reason)

    # -- message handling --------------------------------------------------

    def _handle_message(
        self,
        chat_scope: str,
        chat_id: str,
        message_id: str,
        text: str,
        chat_key: str,
        attachment_descriptions: list[str] | None = None,
        local_file_paths: list[str] | None = None,
    ) -> None:
        """Handle a single inbound message (runs in a worker thread)."""
        attachment_descriptions = attachment_descriptions or []
        local_file_paths = local_file_paths or []

        # Build the full prompt: text + attachment context
        has_attachments = bool(attachment_descriptions)
        if not text and not has_attachments:
            self._send_text_sync(
                chat_scope, chat_id,
                "Send text or files for FunHarness to work on.",
                message_id,
            )
            return

        cmd = text.strip().lower()

        if cmd in {"/help", "help"}:
            self._send_text_sync(
                chat_scope, chat_id,
                "FunHarness QQ Bot commands:\n"
                "/help - show this message\n"
                "/interrupt - stop the current local agent run\n"
                "/files - list attached files in this session\n\n"
                "Supported inputs:\n"
                "- Text messages\n"
                "- Images (will be saved and described)\n"
                "- Documents (PDF, DOCX, XLSX, TXT, etc.)\n"
                "- Audio / video files\n\n"
                "Send any text (with optional files) to run FunHarness.",
                message_id,
            )
            return

        session = self._session_for(chat_scope, chat_id, chat_key)

        if cmd in {"/interrupt", "interrupt", "stop"}:
            session.interrupt()
            self._send_text_sync(
                chat_scope, chat_id,
                "Interrupt requested.",
                message_id,
            )
            return

        if cmd == "/files":
            summary = session.agent.attachments.summary()
            self._send_text_sync(
                chat_scope, chat_id,
                summary,
                message_id,
            )
            return

        # Register received files with the agent's AttachmentManager
        registered_names = []
        for fp in local_file_paths:
            try:
                record = session.agent.attachments.add(fp)
                registered_names.append(
                    f"  - {record.original_name} (id={record.id})"
                )
            except Exception as exc:
                logger.error("[QQBot] Failed to register attachment %s: %s", fp, exc)

        # Build augmented prompt with attachment info
        prompt_parts = []
        if text:
            prompt_parts.append(text)
        if attachment_descriptions:
            prompt_parts.append(
                "\n[Attached files from user]\n"
                + "\n".join(attachment_descriptions)
            )
        if registered_names:
            prompt_parts.append(
                "\n[Files registered in session - use tool_read_attachment to read]\n"
                + "\n".join(registered_names)
            )

        full_prompt = "\n".join(prompt_parts)
        session.run(full_prompt, message_id)

    def _session_for(
        self, chat_scope: str, chat_id: str, chat_key: str,
    ) -> QQBotAgentSession:
        with self._sessions_lock:
            session = self._sessions.get(chat_key)
            if session is None:
                session = QQBotAgentSession(
                    api_client=self._api_client,
                    http_client=self._http_client,
                    chat_scope=chat_scope,
                    chat_id=chat_id,
                    mode=self.config.permission_mode,
                    loop=self._loop,
                )
                self._sessions[chat_key] = session
            return session

    def _send_text_sync(
        self,
        chat_scope: str,
        chat_id: str,
        text: str,
        reply_to: str | None = None,
    ) -> None:
        """Send a message from a worker thread via the main async loop."""
        try:
            _run_async(
                self._loop,
                self._api_client.send_text(
                    chat_scope, chat_id, text,
                    reply_to=reply_to, markdown=True,
                ),
            )
        except Exception as exc:
            logger.error("[QQBot] Failed to send message: %s", exc)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run the FunHarness QQ Bot gateway",
    )
    parser.add_argument("--workspace", help="Override QQ_WORKSPACE")
    args = parser.parse_args(argv)

    config = QQBotConfig.from_env()
    if args.workspace:
        config.workspace = args.workspace

    QQBotGateway(config).serve_forever()


if __name__ == "__main__":
    main()
