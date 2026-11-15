"""Per-turn iteration budgets, resolved at run time so dotenv settings apply."""
from __future__ import annotations

import logging
import os

DEFAULT_MAX_ITERATIONS = 1000


def iteration_limit(role: str = "agent", default: int = DEFAULT_MAX_ITERATIONS) -> int:
    names = ["FUNHARNESS_MAX_ITERATIONS"]
    if role != "agent":
        names.insert(0, f"FUNHARNESS_{role.upper()}_MAX_ITERATIONS")
    for name in names:
        raw = os.getenv(name, "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
        logging.getLogger(__name__).warning("%s must be a positive integer; ignoring this setting", name)
    return default
