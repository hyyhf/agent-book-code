"""
FunHarness - Persona Management

Switchable system prompt presets for different agent identities.
Stored as JSON in .funharness/personas.json.
"""
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


_PERSONA_DIR = ".funharness"
_PERSONA_FILE = "personas.json"


@dataclass
class Persona:
    persona_id: str
    name: str
    system_prompt: str
    begin_dialogs: list[dict[str, str]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PersonaStore:
    """Manages persona presets stored in a JSON file."""

    def __init__(self, project_dir: str | None = None):
        root = Path(project_dir or os.getcwd())
        self._dir = root / _PERSONA_DIR
        self._path = self._dir / _PERSONA_FILE
        self._personas: dict[str, Persona] = {}
        self._active_id: str | None = None
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for item in data.get("personas", []):
                persona = Persona(**{
                    k: v for k, v in item.items()
                    if k in Persona.__dataclass_fields__
                })
                self._personas[persona.persona_id] = persona
            stored_active = data.get("active_id")
            if stored_active and stored_active in self._personas:
                self._active_id = stored_active
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    def _save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "personas": [p.to_dict() for p in self._personas.values()],
        }
        if self._active_id:
            data["active_id"] = self._active_id
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_active_id(self) -> str | None:
        """Return the persisted active persona ID, if any."""
        return self._active_id

    def set_active_id(self, persona_id: str | None) -> None:
        """Persist (or clear) the active persona ID."""
        if persona_id and persona_id not in self._personas:
            return
        self._active_id = persona_id
        self._save()

    def list_all(self) -> list[Persona]:
        return list(self._personas.values())

    def get(self, persona_id: str) -> Persona | None:
        return self._personas.get(persona_id)

    def create(
        self,
        persona_id: str,
        name: str,
        system_prompt: str,
        begin_dialogs: list[dict[str, str]] | None = None,
    ) -> Persona:
        if persona_id in self._personas:
            raise ValueError(f"Persona '{persona_id}' already exists")
        if not persona_id.strip():
            raise ValueError("persona_id cannot be empty")
        if not name.strip():
            raise ValueError("name cannot be empty")
        if not system_prompt.strip():
            raise ValueError("system_prompt cannot be empty")

        now = datetime.now().isoformat(timespec="seconds")
        persona = Persona(
            persona_id=persona_id.strip(),
            name=name.strip(),
            system_prompt=system_prompt.strip(),
            begin_dialogs=begin_dialogs or [],
            created_at=now,
            updated_at=now,
        )
        self._personas[persona.persona_id] = persona
        self._save()
        return persona

    def update(self, persona_id: str, **kwargs: Any) -> Persona:
        persona = self._personas.get(persona_id)
        if persona is None:
            raise ValueError(f"Persona '{persona_id}' not found")

        allowed = {"name", "system_prompt", "begin_dialogs"}
        for key, value in kwargs.items():
            if key in allowed and value is not None:
                setattr(persona, key, value)

        persona.updated_at = datetime.now().isoformat(timespec="seconds")
        self._save()
        return persona

    def delete(self, persona_id: str) -> None:
        if persona_id not in self._personas:
            raise ValueError(f"Persona '{persona_id}' not found")
        del self._personas[persona_id]
        if self._active_id == persona_id:
            self._active_id = None
        self._save()

    def reload(self) -> None:
        self._personas.clear()
        self._load()


# -- Module-level convenience -------------------------------------------------

_store: PersonaStore | None = None


def init_persona_store(project_dir: str | None = None) -> PersonaStore:
    global _store
    _store = PersonaStore(project_dir)
    return _store


def get_persona_store() -> PersonaStore:
    global _store
    if _store is None:
        _store = PersonaStore()
    return _store
