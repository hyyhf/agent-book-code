from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from openai import OpenAI

ENV_PROFILE_ID = "__env__"


def mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}{'*' * 6}{value[-4:]}"


def make_client(profile: dict[str, Any]) -> OpenAI:
    kwargs: dict[str, str] = {"api_key": str(profile.get("api_key") or "")}
    base_url = str(profile.get("base_url") or "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


class ModelConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def config(self) -> dict[str, Any]:
        data = self._read()
        profiles = [self._clean_profile(item) for item in data.get("profiles", [])]
        profiles = [item for item in profiles if item is not None]
        default_id = data.get("default_profile_id") or ENV_PROFILE_ID
        if default_id != ENV_PROFILE_ID and not any(item["id"] == default_id for item in profiles):
            default_id = ENV_PROFILE_ID
        return {"default_profile_id": default_id, "profiles": profiles}

    def public_config(self, active_profile_id: str | None = None) -> dict[str, Any]:
        config = self.config()
        profiles = [self._public_env_profile()]
        profiles.extend(self._public_profile(item) for item in config["profiles"])
        active_id = active_profile_id or config["default_profile_id"]
        if not any(item["id"] == active_id and item["enabled"] for item in profiles):
            active_id = config["default_profile_id"]
        return {
            "config_path": str(self.path),
            "default_profile_id": config["default_profile_id"],
            "active_profile_id": active_id,
            "profiles": profiles,
        }

    def resolve(self, profile_id: str | None) -> dict[str, Any]:
        config = self.config()
        wanted = profile_id or config["default_profile_id"]
        if wanted == ENV_PROFILE_ID:
            return self.env_profile()
        for item in config["profiles"]:
            if item["id"] == wanted and item.get("enabled", True):
                return item
        return self.env_profile()

    def save(self, profiles: list[dict[str, Any]], default_profile_id: str | None) -> dict[str, Any]:
        existing = {item["id"]: item for item in self.config()["profiles"]}
        cleaned: list[dict[str, Any]] = []
        for item in profiles:
            profile = self._clean_profile(item, existing=existing)
            if profile is not None:
                cleaned.append(profile)
        wanted_default = default_profile_id or ENV_PROFILE_ID
        if wanted_default != ENV_PROFILE_ID and not any(item["id"] == wanted_default for item in cleaned):
            wanted_default = ENV_PROFILE_ID
        payload = {"default_profile_id": wanted_default, "profiles": cleaned}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.public_config(active_profile_id=wanted_default)

    def test(self, profile: dict[str, Any]) -> dict[str, Any]:
        started = time.time()
        resolved = self._resolve_draft(profile)
        if not resolved.get("api_key"):
            return {"ok": False, "message": "API Key 未配置", "latency_ms": 0}
        if not resolved.get("model"):
            return {"ok": False, "message": "模型名称未配置", "latency_ms": 0}
        try:
            response = make_client(resolved).chat.completions.create(
                model=resolved["model"],
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=4,
                temperature=0,
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:
            return {
                "ok": False,
                "message": self._friendly_error(exc),
                "latency_ms": round((time.time() - started) * 1000),
            }
        return {
            "ok": True,
            "message": "连接成功",
            "latency_ms": round((time.time() - started) * 1000),
            "model": resolved["model"],
            "preview": content[:80],
        }

    def env_profile(self) -> dict[str, Any]:
        return {
            "id": ENV_PROFILE_ID,
            "name": "环境默认",
            "base_url": os.getenv("OPENAI_BASE_URL", ""),
            "api_key": os.getenv("OPENAI_API_KEY", ""),
            "model": os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash"),
            "enabled": True,
            "source": "env",
        }

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"default_profile_id": ENV_PROFILE_ID, "profiles": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"default_profile_id": ENV_PROFILE_ID, "profiles": []}
        return data if isinstance(data, dict) else {"default_profile_id": ENV_PROFILE_ID, "profiles": []}

    def _clean_profile(
        self,
        item: dict[str, Any],
        existing: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        profile_id = str(item.get("id") or "").strip()
        if not profile_id or profile_id == ENV_PROFILE_ID:
            profile_id = f"model_{uuid.uuid4().hex[:10]}"
        old = existing.get(profile_id) if existing else None
        api_key = str(item.get("api_key") or "")
        if not api_key and old:
            api_key = str(old.get("api_key") or "")
        name = str(item.get("name") or "").strip()
        model = str(item.get("model") or "").strip()
        if not name and model:
            name = model
        if not name:
            name = "未命名模型"
        return {
            "id": profile_id,
            "name": name,
            "base_url": str(item.get("base_url") or "").strip(),
            "api_key": api_key,
            "model": model,
            "enabled": bool(item.get("enabled", True)),
            "source": "user",
        }

    def _public_env_profile(self) -> dict[str, Any]:
        return self._public_profile(self.env_profile())

    def _public_profile(self, item: dict[str, Any]) -> dict[str, Any]:
        api_key = str(item.get("api_key") or "")
        return {
            "id": item["id"],
            "name": item.get("name") or item.get("model") or "未命名模型",
            "base_url": item.get("base_url") or "",
            "model": item.get("model") or "",
            "enabled": bool(item.get("enabled", True)),
            "source": item.get("source") or "user",
            "has_api_key": bool(api_key),
            "api_key_masked": mask_key(api_key),
        }

    def _resolve_draft(self, draft: dict[str, Any]) -> dict[str, Any]:
        profile_id = str(draft.get("id") or "").strip()
        if profile_id and profile_id != ENV_PROFILE_ID:
            base = self.resolve(profile_id)
        else:
            base = self.env_profile() if profile_id == ENV_PROFILE_ID else {}
        merged = {**base, **draft}
        if not str(draft.get("api_key") or "") and base.get("api_key"):
            merged["api_key"] = base["api_key"]
        return merged

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        text = str(exc)
        lowered = text.lower()
        if "401" in text or "unauthorized" in lowered or "api key" in lowered:
            return "鉴权失败，请检查 API Key"
        if "404" in text or "model" in lowered and "not" in lowered:
            return "模型不存在或当前 Key 无权访问"
        if "connection" in lowered or "resolve" in lowered or "timeout" in lowered:
            return "无法连接到 Base URL"
        return text[:240]
