"""
WeChat (Weixin) channel gateway for FunHarness.

This module implements the WeChat bot backend HTTP JSON API protocol
(extracted from Tencent/openclaw-weixin) to receive and send messages.

Supports:
- Text messaging (send / receive)
- Media receiving: images, files, voice, video from WeChat users are
  downloaded (with AES-128-ECB decryption) and saved to a local cache.
- Media sending: files can be encrypted and uploaded to the WeChat CDN,
  then sent as image/file/video messages.

Authentication is done via QR code scanning in the terminal.
Message receiving uses getUpdates long-polling.
Message sending uses the sendMessage REST endpoint.

Environment variables
---------------------
- WEIXIN_PERMISSION_MODE   "suggest" | "auto" (default: suggest)
- WEIXIN_WORKSPACE         Working directory for the agent

Run:
  fh weixin-login   # First time: scan QR to authenticate
  fh weixin         # Start the gateway (after login)
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import random
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from funharness.src.agent import FunHarnessAgent

logger = logging.getLogger(__name__)

# -- constants ----------------------------------------------------------------

FIXED_LOGIN_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_BOT_TYPE = "3"
CHANNEL_VERSION = "funharness-weixin/1.0.0"
MAX_TEXT_CHARS = 3500
NEW_SESSION_COMMANDS = {"/new", "/restart", "/reset", "/重新开始", "重新开始"}
LONG_POLL_TIMEOUT_S = 35
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY_S = 30
RETRY_DELAY_S = 2
CREDENTIALS_DIR = ".funharness"
CREDENTIALS_FILE = "weixin_credentials.json"

# message type / state constants (from openclaw-weixin types.ts)
MSG_TYPE_USER = 1
MSG_TYPE_BOT = 2
MSG_STATE_NEW = 0
MSG_STATE_FINISH = 2
ITEM_TYPE_TEXT = 1
ITEM_TYPE_IMAGE = 2
ITEM_TYPE_VOICE = 3
ITEM_TYPE_FILE = 4
ITEM_TYPE_VIDEO = 5
UPLOAD_MEDIA_IMAGE = 1
UPLOAD_MEDIA_VIDEO = 2
UPLOAD_MEDIA_FILE = 3
MEDIA_CACHE_DIR = ".funharness/weixin_media"


# -- .env loading (same strategy as feishu.py) --------------------------------

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


# -- credentials persistence --------------------------------------------------

def _credentials_path() -> Path:
    return Path(CREDENTIALS_DIR) / CREDENTIALS_FILE


def _save_credentials(data: dict[str, str]) -> None:
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_credentials() -> dict[str, str] | None:
    path = _credentials_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# -- configuration ------------------------------------------------------------

@dataclass
class WeixinConfig:
    bot_token: str = ""
    base_url: str = ""
    permission_mode: str = "suggest"
    workspace: str = ""

    @classmethod
    def from_env(cls) -> "WeixinConfig":
        _load_env()
        creds = _load_credentials()
        bot_token = ""
        base_url = ""
        if creds:
            bot_token = creds.get("bot_token", "")
            base_url = creds.get("base_url", "")
        return cls(
            bot_token=bot_token,
            base_url=base_url,
            permission_mode=os.getenv("WEIXIN_PERMISSION_MODE", "suggest"),
            workspace=os.getenv("WEIXIN_WORKSPACE", ""),
        )


# -- AES-128-ECB crypto (matches openclaw-weixin cdn/aes-ecb.ts) ---------------

def _aes_ecb_padded_size(plaintext_size: int) -> int:
    """Compute AES-128-ECB ciphertext size (PKCS7 padding to 16-byte boundary)."""
    return ((plaintext_size + 1) // 16 + 1) * 16


def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt with AES-128-ECB + PKCS7 padding."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_padding
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(padded) + enc.finalize()


def _aes_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt AES-128-ECB + PKCS7 padding."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_padding
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    dec = cipher.decryptor()
    padded = dec.update(ciphertext) + dec.finalize()
    unpadder = sym_padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _parse_aes_key(aes_key_b64: str) -> bytes:
    """Parse CDNMedia.aes_key (base64) into raw 16-byte key.

    Two encodings exist:
      - base64(raw 16 bytes) -> images
      - base64(hex string of 16 bytes) -> file/voice/video
    """
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        try:
            return bytes.fromhex(decoded.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            pass
    raise ValueError(f"aes_key must decode to 16 or 32 bytes, got {len(decoded)}")


# -- CDN download / upload helpers ---------------------------------------------

def _cdn_download(url: str) -> bytes:
    """Download raw bytes from a URL."""
    req = Request(url, method="GET")
    with urlopen(req, timeout=60) as resp:
        return resp.read()


def _cdn_download_decrypt(media: dict, cdn_base_url: str) -> bytes | None:
    """Download and AES-decrypt a CDNMedia reference. Returns plaintext bytes."""
    full_url = media.get("full_url", "")
    encrypt_param = media.get("encrypt_query_param", "")
    aes_key_b64 = media.get("aes_key", "")

    if not full_url and not encrypt_param:
        return None

    url = full_url
    if not url and encrypt_param and cdn_base_url:
        from urllib.parse import quote
        url = f"{cdn_base_url}/download?encrypted_query_param={quote(encrypt_param)}"
    if not url:
        return None

    raw = _cdn_download(url)
    if aes_key_b64:
        key = _parse_aes_key(aes_key_b64)
        return _aes_ecb_decrypt(raw, key)
    return raw  # unencrypted


def _save_media_to_cache(data: bytes, filename: str) -> str:
    """Save media bytes to local cache, return absolute path."""
    cache = Path(MEDIA_CACHE_DIR)
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / filename
    dest.write_bytes(data)
    return str(dest.resolve())


def _download_media_item(item: dict, cdn_base_url: str) -> str | None:
    """Download a single media MessageItem to local cache. Returns file path or None."""
    item_type = item.get("type", 0)
    media = None
    filename_hint = ""

    if item_type == ITEM_TYPE_IMAGE:
        img = item.get("image_item", {})
        media = img.get("media", {})
        # image_item may have hex aeskey in a separate field
        if img.get("aeskey") and media:
            hex_key = img["aeskey"]
            media = dict(media)
            media["aes_key"] = base64.b64encode(
                bytes.fromhex(hex_key)
            ).decode()
        filename_hint = f"image_{uuid.uuid4().hex[:8]}.jpg"
    elif item_type == ITEM_TYPE_VOICE:
        voice = item.get("voice_item", {})
        media = voice.get("media", {})
        filename_hint = f"voice_{uuid.uuid4().hex[:8]}.silk"
    elif item_type == ITEM_TYPE_FILE:
        fi = item.get("file_item", {})
        media = fi.get("media", {})
        filename_hint = fi.get("file_name", f"file_{uuid.uuid4().hex[:8]}.bin")
    elif item_type == ITEM_TYPE_VIDEO:
        vid = item.get("video_item", {})
        media = vid.get("media", {})
        filename_hint = f"video_{uuid.uuid4().hex[:8]}.mp4"
    else:
        return None

    if not media:
        return None

    try:
        data = _cdn_download_decrypt(media, cdn_base_url)
        if data:
            path = _save_media_to_cache(data, filename_hint)
            logger.info("[weixin] Downloaded media: %s (%d bytes)", path, len(data))
            return path
    except Exception as exc:
        logger.error("[weixin] Media download failed: %s", exc)
    return None


# -- HTTP API client ----------------------------------------------------------

def _random_wechat_uin() -> str:
    """Generate X-WECHAT-UIN header: random uint32 -> decimal -> base64."""
    val = random.randint(0, 0xFFFFFFFF)
    return base64.b64encode(str(val).encode()).decode()


def _build_headers(token: str = "") -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_wechat_uin(),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _base_info() -> dict[str, str]:
    return {"channel_version": CHANNEL_VERSION, "bot_agent": "FunHarness"}


class WeixinApiClient:
    """Thin wrapper around the WeChat backend HTTP JSON API."""

    def __init__(self, base_url: str, token: str, cdn_base_url: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.cdn_base_url = cdn_base_url.rstrip("/") if cdn_base_url else ""

    def _post(self, endpoint: str, payload: dict, timeout: float = 15.0) -> dict:
        url = f"{self.base_url}/{endpoint}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=body, method="POST", headers=_build_headers(self.token))
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def _raw_post(self, url: str, data: bytes, content_type: str, timeout: float = 30.0) -> dict[str, str]:
        """POST raw bytes; returns response headers as dict."""
        req = Request(url, data=data, method="POST")
        req.add_header("Content-Type", content_type)
        with urlopen(req, timeout=timeout) as resp:
            resp.read()  # consume body
            return dict(resp.headers)

    def get_updates(self, cursor: str = "") -> dict:
        """Long-poll for new messages."""
        return self._post(
            "ilink/bot/getupdates",
            {"get_updates_buf": cursor, "base_info": _base_info()},
            timeout=LONG_POLL_TIMEOUT_S + 5,
        )

    def send_text(self, to_user_id: str, text: str, context_token: str = "") -> None:
        """Send a text message to a user."""
        self._send_items(
            to_user_id,
            [{"type": ITEM_TYPE_TEXT, "text_item": {"text": text}}],
            context_token,
        )

    def send_media_file(self, to_user_id: str, file_path: str,
                        context_token: str = "", caption: str = "") -> None:
        """Upload a local file to CDN and send as image/file message."""
        path = Path(file_path)
        if not path.exists():
            logger.error("[weixin] File not found: %s", file_path)
            return

        plaintext = path.read_bytes()
        ext = path.suffix.lower()

        # Determine media type
        if ext in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}:
            media_type = UPLOAD_MEDIA_IMAGE
        elif ext in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
            media_type = UPLOAD_MEDIA_VIDEO
        else:
            media_type = UPLOAD_MEDIA_FILE

        # Generate AES key and file key
        aes_key = os.urandom(16)
        file_key = os.urandom(16).hex()
        raw_size = len(plaintext)
        raw_md5 = hashlib.md5(plaintext).hexdigest()
        cipher_size = _aes_ecb_padded_size(raw_size)

        # Get upload URL from server
        upload_resp = self._post(
            "ilink/bot/getuploadurl",
            {
                "filekey": file_key,
                "media_type": media_type,
                "to_user_id": to_user_id,
                "rawsize": raw_size,
                "rawfilemd5": raw_md5,
                "filesize": cipher_size,
                "no_need_thumb": True,
                "aeskey": aes_key.hex(),
                "base_info": _base_info(),
            },
        )

        upload_full_url = (upload_resp.get("upload_full_url") or "").strip()
        upload_param = upload_resp.get("upload_param", "")

        if upload_full_url:
            cdn_url = upload_full_url
        elif upload_param and self.cdn_base_url:
            from urllib.parse import quote
            cdn_url = (
                f"{self.cdn_base_url}/upload"
                f"?encrypted_query_param={quote(upload_param)}"
                f"&filekey={quote(file_key)}"
            )
        else:
            logger.error("[weixin] getUploadUrl returned no upload URL")
            return

        # Encrypt and upload
        ciphertext = _aes_ecb_encrypt(plaintext, aes_key)
        resp_headers = self._raw_post(cdn_url, ciphertext, "application/octet-stream")
        download_param = resp_headers.get("x-encrypted-param", "")
        if not download_param:
            logger.error("[weixin] CDN upload response missing x-encrypted-param")
            return

        # Build media item
        aes_key_b64 = base64.b64encode(aes_key).decode()
        cdn_media = {
            "encrypt_query_param": download_param,
            "aes_key": aes_key_b64,
            "encrypt_type": 1,
        }

        items: list[dict] = []
        if caption:
            items.append({"type": ITEM_TYPE_TEXT, "text_item": {"text": caption}})

        if media_type == UPLOAD_MEDIA_IMAGE:
            items.append({"type": ITEM_TYPE_IMAGE, "image_item": {
                "media": cdn_media, "mid_size": len(ciphertext),
            }})
        elif media_type == UPLOAD_MEDIA_VIDEO:
            items.append({"type": ITEM_TYPE_VIDEO, "video_item": {
                "media": cdn_media, "video_size": len(ciphertext),
            }})
        else:
            items.append({"type": ITEM_TYPE_FILE, "file_item": {
                "media": cdn_media, "file_name": path.name, "len": str(raw_size),
            }})

        # Send each item as separate message (matches openclaw-weixin behavior)
        for item in items:
            self._send_items(to_user_id, [item], context_token)

        logger.info("[weixin] Media sent: %s (%d bytes)", path.name, raw_size)

    def _send_items(self, to_user_id: str, item_list: list[dict],
                    context_token: str = "") -> None:
        """Send a message with the given item_list."""
        client_id = f"fh-{uuid.uuid4().hex[:12]}"
        msg: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": MSG_TYPE_BOT,
            "message_state": MSG_STATE_FINISH,
            "item_list": item_list,
        }
        if context_token:
            msg["context_token"] = context_token
        self._post(
            "ilink/bot/sendmessage",
            {"msg": msg, "base_info": _base_info()},
        )


# -- QR code login -------------------------------------------------------------

def _post_json(url: str, payload: dict, timeout: float = 15.0) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = Request(url, data=body, method="POST", headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _get_json(url: str, timeout: float = 35.0) -> dict:
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def qr_login() -> dict[str, str] | None:
    """Run the interactive QR code login flow.

    Returns {"bot_token": ..., "base_url": ..., "bot_id": ...} on success,
    or None on failure.
    """
    base = FIXED_LOGIN_BASE_URL

    # 1. Fetch QR code
    print("[weixin] Fetching login QR code...")
    qr_url = f"{base}/ilink/bot/get_bot_qrcode?bot_type={DEFAULT_BOT_TYPE}"
    qr_resp = _post_json(qr_url, {"local_token_list": []})
    qrcode_id = qr_resp.get("qrcode", "")
    qrcode_url = qr_resp.get("qrcode_img_content", "")

    if not qrcode_id or not qrcode_url:
        print("[weixin] Failed to get QR code from server.")
        return None

    # 2. Display QR in terminal
    try:
        import qrcode as qr_lib
        qr = qr_lib.QRCode(border=1)
        qr.add_data(qrcode_url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        print("[weixin] (install 'qrcode' package for terminal QR display)")

    print(f"\n[weixin] Scan this QR code with WeChat on your phone:")
    print(f"[weixin] Or open this URL: {qrcode_url}")
    print("[weixin] Waiting for scan...")

    # 3. Poll for status
    deadline = time.time() + 300  # 5 min timeout
    scanned_printed = False
    while time.time() < deadline:
        try:
            status_url = (
                f"{base}/ilink/bot/get_qrcode_status"
                f"?qrcode={qrcode_id}"
            )
            status_resp = _get_json(status_url, timeout=LONG_POLL_TIMEOUT_S + 5)
        except Exception:
            # Network timeout is normal for long-poll, just retry
            continue

        status = status_resp.get("status", "wait")

        if status == "wait":
            continue
        elif status == "scaned":
            if not scanned_printed:
                print("[weixin] QR code scanned! Please confirm on your phone...")
                scanned_printed = True
        elif status == "confirmed":
            bot_token = status_resp.get("bot_token", "")
            bot_id = status_resp.get("ilink_bot_id", "")
            base_url = status_resp.get("baseurl", base)
            if not bot_token or not bot_id:
                print("[weixin] Login confirmed but missing credentials.")
                return None
            print(f"[weixin] Login successful! bot_id={bot_id}")
            return {
                "bot_token": bot_token,
                "base_url": base_url or base,
                "bot_id": bot_id,
            }
        elif status == "expired":
            print("[weixin] QR code expired. Please try again.")
            return None
        elif status == "binded_redirect":
            print("[weixin] This bot is already connected.")
            return None
        elif status == "scaned_but_redirect":
            redirect_host = status_resp.get("redirect_host", "")
            if redirect_host:
                base = f"https://{redirect_host}"
        elif status == "need_verifycode":
            code = input("[weixin] Enter the verification code shown on your phone: ")
            # Append verify_code to next poll
            try:
                vc_url = (
                    f"{base}/ilink/bot/get_qrcode_status"
                    f"?qrcode={qrcode_id}&verify_code={code}"
                )
                vc_resp = _get_json(vc_url, timeout=LONG_POLL_TIMEOUT_S + 5)
                if vc_resp.get("status") == "confirmed":
                    bot_token = vc_resp.get("bot_token", "")
                    bot_id = vc_resp.get("ilink_bot_id", "")
                    base_url = vc_resp.get("baseurl", base)
                    if bot_token and bot_id:
                        print(f"[weixin] Login successful! bot_id={bot_id}")
                        return {
                            "bot_token": bot_token,
                            "base_url": base_url or base,
                            "bot_id": bot_id,
                        }
            except Exception as e:
                print(f"[weixin] Verification failed: {e}")
        else:
            logger.debug("[weixin] Unknown status: %s", status)

        time.sleep(1)

    print("[weixin] Login timed out.")
    return None


# -- agent session (same pattern as feishu/qqbot) -----------------------------

class WeixinAgentSession:
    """Manages a FunHarnessAgent instance for one WeChat conversation."""

    def __init__(self, api: WeixinApiClient, user_id: str, mode: str):
        self.api = api
        self.user_id = user_id
        self._context_token = ""
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

    def set_context_token(self, token: str) -> None:
        if token:
            self._context_token = token

    def run(self, text: str) -> None:
        if not self.lock.acquire(blocking=False):
            self._send(
                "FunHarness is still working on the previous request. "
                "Send /interrupt to stop it.",
            )
            return
        try:
            self._final_buffer = []
            self._send("FunHarness received it. Working locally...")
            self.agent.run(text)
            final = "".join(self._final_buffer).strip()
            self._send(final if final else "Done.")
        except InterruptedError:
            self._send("Interrupted.")
        except Exception as e:
            self._send(f"FunHarness error: {e}")
        finally:
            self.lock.release()

    def interrupt(self) -> None:
        self.agent.request_interrupt()

    def new_session(self) -> str:
        if not self.lock.acquire(blocking=False):
            return (
                "FunHarness is still working on the previous request. "
                "Send /interrupt to stop it before starting a new session."
            )
        try:
            return self.agent.handle_slash_command("/new") or "New session started."
        finally:
            self.lock.release()

    # -- callbacks -------------------------------------------------------------

    def _on_token(self, token: str) -> None:
        self._final_buffer.append(token)

    def _on_status(self, msg: str) -> None:
        now = time.time()
        if now - self._last_status_at < 1.5:
            return
        self._last_status_at = now
        self._send(f"[status] {msg}")

    def _on_tool_call(self, name: str, preview: str, risk: str) -> None:
        if len(preview) > 280:
            preview = preview[:280] + "..."
        self._send(f"[tool] {name} ({risk})\nargs: {preview}")

    def _on_tool_result(self, name: str, result: str, hook_feedback: str, display=None) -> None:
        text = result if len(result) <= 500 else result[:500] + "...(truncated)"
        if hook_feedback:
            text += f"\n[hook] {hook_feedback}"
        self._send(f"[tool result] {name}\n{text}")

    def _on_approval(self, tool_name: str, arguments: dict[str, Any], reason: str):
        self._send(
            f"[approval required] {tool_name}\n"
            f"{reason}\n"
            "Remote approval is not interactive yet. Set WEIXIN_PERMISSION_MODE=auto "
            "in a trusted workspace if you want this channel to execute write/shell tools.",
        )
        return False, ""

    # -- send helper -----------------------------------------------------------

    def _send(self, text: str) -> None:
        text = text or "(empty)"
        chunks = [
            text[i:i + MAX_TEXT_CHARS]
            for i in range(0, len(text), MAX_TEXT_CHARS)
        ] or ["(empty)"]
        for index, chunk in enumerate(chunks):
            if index > 0:
                chunk = f"(continued)\n{chunk}"
            try:
                self.api.send_text(self.user_id, chunk, self._context_token)
            except Exception as exc:
                logger.error("[weixin] Failed to send message: %s", exc)


# -- gateway -------------------------------------------------------------------

class WeixinGateway:
    """Long-poll loop: getUpdates -> dispatch to agent sessions -> reply."""

    def __init__(self, config: WeixinConfig):
        self.config = config
        self.api = WeixinApiClient(config.base_url, config.bot_token)
        self._sessions: dict[str, WeixinAgentSession] = {}
        self._sessions_lock = threading.Lock()
        self._cursor = ""

    def serve_forever(self) -> None:
        self._prepare_workspace()
        print("FunHarness WeChat gateway started.")
        print(f"  mode       = {self.config.permission_mode}")
        print(f"  workspace  = {self.config.workspace or '(cwd)'}")
        print("Keep this process running. Send messages to the bot in WeChat.")

        consecutive_failures = 0
        while True:
            try:
                resp = self.api.get_updates(self._cursor)

                ret = resp.get("ret", 0)
                errcode = resp.get("errcode", 0)
                if ret != 0 or errcode != 0:
                    consecutive_failures += 1
                    errmsg = resp.get("errmsg", "")
                    logger.error(
                        "[weixin] getUpdates failed: ret=%s errcode=%s errmsg=%s (%d/%d)",
                        ret, errcode, errmsg,
                        consecutive_failures, MAX_CONSECUTIVE_FAILURES,
                    )
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        consecutive_failures = 0
                        time.sleep(BACKOFF_DELAY_S)
                    else:
                        time.sleep(RETRY_DELAY_S)
                    continue

                consecutive_failures = 0

                # Update cursor
                new_cursor = resp.get("get_updates_buf", "")
                if new_cursor:
                    self._cursor = new_cursor

                # Process messages
                for msg in resp.get("msgs", []):
                    self._dispatch_message(msg)

            except KeyboardInterrupt:
                print("\n[weixin] Shutting down.")
                break
            except Exception as exc:
                consecutive_failures += 1
                logger.error("[weixin] getUpdates error: %s", exc)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0
                    time.sleep(BACKOFF_DELAY_S)
                else:
                    time.sleep(RETRY_DELAY_S)

    def shutdown(self) -> None:
        """Stop the gateway (called from TUI)."""
        pass  # The long-poll will naturally stop when the thread is interrupted

    def _prepare_workspace(self) -> None:
        if self.config.workspace:
            Path(self.config.workspace).mkdir(parents=True, exist_ok=True)
            os.chdir(self.config.workspace)

    def _dispatch_message(self, msg: dict) -> None:
        """Extract text and media from a WeixinMessage and dispatch."""
        msg_type = msg.get("message_type", 0)
        if msg_type != MSG_TYPE_USER:
            return  # Ignore bot's own messages

        from_user = msg.get("from_user_id", "")
        if not from_user:
            return

        context_token = msg.get("context_token", "")

        # Extract text and media from item_list
        text_parts = []
        media_paths: list[str] = []
        cdn_base = self.config.base_url  # CDN base URL (same as API base)

        for item in msg.get("item_list", []):
            itype = item.get("type", 0)
            if itype == ITEM_TYPE_TEXT:
                t = item.get("text_item", {}).get("text", "")
                if t:
                    text_parts.append(t)
            elif itype in (ITEM_TYPE_IMAGE, ITEM_TYPE_VOICE,
                           ITEM_TYPE_FILE, ITEM_TYPE_VIDEO):
                path = _download_media_item(item, cdn_base)
                if path:
                    media_paths.append(path)

        text = " ".join(text_parts).strip()

        logger.info(
            "[weixin] Message from %s: %s (media=%d)",
            from_user, text[:100] if text else "(no text)", len(media_paths),
        )

        threading.Thread(
            target=self._handle_message,
            args=(from_user, text, context_token, media_paths),
            daemon=True,
        ).start()

    def _handle_message(self, user_id: str, text: str,
                        context_token: str,
                        media_paths: list[str] | None = None) -> None:
        media_paths = media_paths or []
        has_media = bool(media_paths)

        if not text and not has_media:
            try:
                self.api.send_text(
                    user_id, "Send text or files for FunHarness to work on.",
                    context_token,
                )
            except Exception:
                pass
            return

        cmd = text.strip().lower()
        if cmd in {"/help", "help"}:
            try:
                self.api.send_text(
                    user_id,
                    "FunHarness WeChat commands:\n"
                    "/help - show this message\n"
                    "/new - start a new conversation session\n"
                    "/interrupt - stop the current local agent run\n"
                    "/files - list attached files in this session\n\n"
                    "Supported inputs:\n"
                    "- Text messages\n"
                    "- Images, documents, audio, video files\n\n"
                    "Send any text (with optional files) to run FunHarness.",
                    context_token,
                )
            except Exception:
                pass
            return

        session = self._session_for(user_id)
        session.set_context_token(context_token)

        if cmd in NEW_SESSION_COMMANDS:
            try:
                self.api.send_text(user_id, session.new_session(), context_token)
            except Exception:
                pass
            return

        if cmd in {"/interrupt", "interrupt", "stop"}:
            session.interrupt()
            try:
                self.api.send_text(user_id, "Interrupt requested.", context_token)
            except Exception:
                pass
            return

        if cmd == "/files":
            summary = session.agent.attachments.summary()
            try:
                self.api.send_text(user_id, summary, context_token)
            except Exception:
                pass
            return

        # Register received files with the agent's AttachmentManager
        registered_names = []
        for fp in media_paths:
            try:
                record = session.agent.attachments.add(fp)
                registered_names.append(
                    f"  - {record.original_name} (id={record.id})"
                )
            except Exception as exc:
                logger.error("[weixin] Failed to register attachment %s: %s", fp, exc)

        # Build augmented prompt with attachment info
        prompt_parts = []
        if text:
            prompt_parts.append(text)
        if registered_names:
            prompt_parts.append(
                "\n[Files registered in session - use tool_read_attachment to read]\n"
                + "\n".join(registered_names)
            )
        elif has_media and not text:
            prompt_parts.append("[Received media file(s), but registration failed]")

        full_prompt = "\n".join(prompt_parts)
        session.run(full_prompt)

    def _session_for(self, user_id: str) -> WeixinAgentSession:
        with self._sessions_lock:
            session = self._sessions.get(user_id)
            if session is None:
                session = WeixinAgentSession(
                    self.api, user_id, self.config.permission_mode,
                )
                self._sessions[user_id] = session
            return session


# -- CLI entry points ----------------------------------------------------------

def login_main(argv: list[str] | None = None) -> None:
    """CLI: fh weixin-login"""
    _load_env()
    print("=" * 50)
    print("  FunHarness WeChat Login")
    print("=" * 50)

    result = qr_login()
    if result is None:
        print("\n[weixin] Login failed.")
        return

    _save_credentials(result)
    print(f"\n[weixin] Credentials saved to {_credentials_path()}")
    print("[weixin] You can now run: fh weixin")


def main(argv: list[str] | None = None) -> None:
    """CLI: fh weixin"""
    parser = argparse.ArgumentParser(description="Run the FunHarness WeChat gateway")
    parser.add_argument("--workspace", help="Override WEIXIN_WORKSPACE")
    args = parser.parse_args(argv)

    config = WeixinConfig.from_env()
    if args.workspace:
        config.workspace = args.workspace

    if not config.bot_token or not config.base_url:
        print("[weixin] No credentials found. Run 'fh weixin-login' first.")
        return

    WeixinGateway(config).serve_forever()


if __name__ == "__main__":
    main()
