"""Data model shared by readers and output: readings, thresholds, statistics."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Dict, List, Optional

KIND_MEMORY = "memory"
KIND_STORAGE = "storage"

DEFAULT_WARN_C = 50.0
DEFAULT_HOT_C = 72.0


@dataclass(frozen=True)
class Reading:
    """One sensor result. ``temp_c`` and ``error`` are mutually exclusive."""

    slot: str
    address: str
    source: str
    kind: str = KIND_MEMORY
    temp_c: Optional[float] = None
    error: Optional[str] = None
    bus: Optional[int] = None
    i2c_addr: Optional[int] = None
    raw_word: Optional[int] = None

    def as_result(self) -> dict:
        """Dict form consumed by the display layer (keys kept stable)."""
        return {
            "slot": self.slot,
            "address": self.address,
            "temp": self.temp_c,
            "error": self.error,
            "source": self.source,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class Thresholds:
    warn: float = DEFAULT_WARN_C
    hot: float = DEFAULT_HOT_C

    def __post_init__(self) -> None:
        if not (math.isfinite(self.warn) and math.isfinite(self.hot)):
            raise ValueError("thresholds must be finite numbers")
        if self.warn >= self.hot:
            raise ValueError(f"warn ({self.warn}) must be lower than hot ({self.hot})")

    def status(self, temp_c: float) -> str:
        if temp_c < self.warn:
            return "OK"
        if temp_c < self.hot:
            return "WARN"
        return "HOT"


def label_slots(readings: List[Reading], prefix: str = "DIMM") -> List[Reading]:
    """Assign 'DIMM n' labels in discovery order to readings without one."""
    out = []
    for index, reading in enumerate(readings):
        if reading.slot:
            out.append(reading)
        else:
            out.append(replace(reading, slot=f"{prefix} {index}"))
    return out


# ── Statistics (per slot, running) ───────────────────────────────────────────


def make_stats() -> Dict[str, dict]:
    return defaultdict(lambda: {"min": None, "max": None, "sum": 0.0, "count": 0, "avg": None})


def update_stats(stats: Dict[str, dict], results: List[dict]) -> None:
    for r in results:
        t = r.get("temp")
        if t is None:
            continue
        s = stats[r["slot"]]
        s["min"] = t if s["min"] is None else min(s["min"], t)
        s["max"] = t if s["max"] is None else max(s["max"], t)
        s["sum"] += t
        s["count"] += 1
        s["avg"] = s["sum"] / s["count"]
