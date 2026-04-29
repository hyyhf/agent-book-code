"""
FunHarness - Scheduled Work

Schedules remember future intent. When a schedule fires it emits a notification;
the main agent loop decides what to do with that prompt.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class ScheduleRecord:
    schedule_id: str
    name: str
    when: str
    prompt: str
    recurring: bool
    enabled: bool = True
    created_at: float = 0.0
    next_fire_at: float = 0.0
    last_fired_at: float = 0.0
    last_fired_key: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduleRecord":
        return cls(
            schedule_id=data["schedule_id"],
            name=data.get("name", ""),
            when=data.get("when", ""),
            prompt=data.get("prompt", ""),
            recurring=bool(data.get("recurring", False)),
            enabled=bool(data.get("enabled", True)),
            created_at=float(data.get("created_at", 0.0)),
            next_fire_at=float(data.get("next_fire_at", 0.0)),
            last_fired_at=float(data.get("last_fired_at", 0.0)),
            last_fired_key=data.get("last_fired_key", ""),
        )


class ScheduleManager:
    def __init__(self, path: str | Path = ".funharness/schedules.json", check_interval: float = 5.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.check_interval = check_interval
        self._lock = threading.Lock()
        self._records: dict[str, ScheduleRecord] = {}
        self._notifications: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in data.get("schedules", []):
            try:
                record = ScheduleRecord.from_dict(item)
            except (KeyError, ValueError, TypeError):
                continue
            self._records[record.schedule_id] = record

    def _save(self):
        data = {"schedules": [r.to_dict() for r in self._records.values()]}
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def create(self, name: str, when: str, prompt: str, recurring: bool = False) -> ScheduleRecord:
        schedule_id = f"job_{uuid.uuid4().hex[:8]}"
        created_at = time.time()
        next_fire_at = _parse_delay_or_timestamp(when, created_at)
        record = ScheduleRecord(
            schedule_id=schedule_id,
            name=name or schedule_id,
            when=when,
            prompt=prompt,
            recurring=recurring or _looks_like_cron(when),
            created_at=created_at,
            next_fire_at=next_fire_at,
        )
        with self._lock:
            self._records[schedule_id] = record
            self._save()
        return record

    def delete(self, schedule_id: str) -> bool:
        with self._lock:
            existed = self._records.pop(schedule_id, None) is not None
            if existed:
                self._save()
            return existed

    def list(self) -> list[ScheduleRecord]:
        with self._lock:
            return sorted(self._records.values(), key=lambda r: r.created_at, reverse=True)

    def drain_notifications(self) -> list[dict]:
        with self._lock:
            items = list(self._notifications)
            self._notifications.clear()
            return items

    def check_due(self, now: datetime | None = None):
        now = now or datetime.now()
        now_ts = now.timestamp()
        fired = []
        with self._lock:
            for record in list(self._records.values()):
                if not record.enabled:
                    continue
                if _is_due(record, now, now_ts):
                    key = now.strftime("%Y%m%d%H%M")
                    if _looks_like_cron(record.when) and record.last_fired_key == key:
                        continue
                    record.last_fired_at = now_ts
                    record.last_fired_key = key
                    fired.append(record)
                    self._notifications.append({
                        "type": "scheduled_prompt",
                        "schedule_id": record.schedule_id,
                        "name": record.name,
                        "prompt": record.prompt,
                    })
                    if not record.recurring:
                        record.enabled = False
            if fired:
                self._save()
        return fired

    def _loop(self):
        while not self._stop.wait(self.check_interval):
            self.check_due()

    def summary(self) -> str:
        records = self.list()
        if not records:
            return "(no schedules)"
        lines = ["Schedules:"]
        for record in records:
            state = "on" if record.enabled else "off"
            lines.append(
                f"  {record.schedule_id} [{state}] {record.when} -> "
                f"{record.name}: {record.prompt[:80]}"
            )
        return "\n".join(lines)


def _looks_like_cron(value: str) -> bool:
    return len(value.strip().split()) == 5


def _parse_delay_or_timestamp(value: str, now_ts: float) -> float:
    text = value.strip().lower()
    match = re.fullmatch(r"in\s+(\d+)\s*(s|sec|secs|m|min|mins|h|hr|hrs|d|day|days)", text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        factor = 1
        if unit.startswith("m"):
            factor = 60
        elif unit.startswith("h"):
            factor = 3600
        elif unit.startswith("d"):
            factor = 86400
        return now_ts + amount * factor
    if _looks_like_cron(value):
        return 0.0
    try:
        return datetime.fromisoformat(value.strip()).timestamp()
    except ValueError:
        return now_ts


def _is_due(record: ScheduleRecord, now: datetime, now_ts: float) -> bool:
    if _looks_like_cron(record.when):
        return _cron_matches(record.when, now)
    return bool(record.next_fire_at and now_ts >= record.next_fire_at)


def _cron_matches(expr: str, now: datetime) -> bool:
    fields = expr.split()
    if len(fields) != 5:
        return False
    minute, hour, day, month, weekday = fields
    return (
        _field_matches(minute, now.minute, 0, 59)
        and _field_matches(hour, now.hour, 0, 23)
        and _field_matches(day, now.day, 1, 31)
        and _field_matches(month, now.month, 1, 12)
        and _field_matches(weekday, now.weekday(), 0, 6)
    )


def _field_matches(field: str, value: int, low: int, high: int) -> bool:
    for part in field.split(","):
        part = part.strip()
        if part == "*":
            return True
        if part.startswith("*/"):
            try:
                step = int(part[2:])
            except ValueError:
                continue
            return step > 0 and (value - low) % step == 0
        try:
            if int(part) == value:
                return True
        except ValueError:
            continue
    return False
