"""Machine-readable output: JSON, CSV, validate exit codes."""

from __future__ import annotations

import csv
import json
from typing import Dict, List

from dtemp.fsutil import csv_safe, open_for_write
from dtemp.model import Thresholds

CSV_COLUMNS = ["timestamp", "slot", "address", "temp_c", "status", "source", "kind", "error"]

EXIT_OK = 0
EXIT_WARN_OR_HOT = 1
EXIT_NO_SENSORS = 2
EXIT_READ_ERRORS = 3
EXIT_USAGE = 64
EXIT_UNAVAILABLE = 69
EXIT_INTERNAL = 70
EXIT_CANT_CREATE = 73
EXIT_NO_PERMISSION = 77


def _round(value):
    return round(value, 4) if value is not None else None


def build_json(results: List[dict], stats_by_kind: Dict[str, dict], now_str: str, thr: Thresholds) -> List[dict]:
    """One object per sensor. Key set is stable; new keys are only ever added."""
    out = []
    for r in results:
        t = r.get("temp")
        s = stats_by_kind[r["kind"]][r["slot"]]
        out.append(
            {
                "timestamp": now_str,
                "slot": r["slot"],
                "address": r.get("address", ""),
                "kind": r["kind"],
                "temp_c": _round(t),
                "status": thr.status(t) if t is not None else ("ERROR" if r.get("error") else "N/A"),
                "source": r.get("source", ""),
                "error": r.get("error"),
                "min_c": _round(s["min"]),
                "max_c": _round(s["max"]),
                "avg_c": _round(s["avg"]),
            }
        )
    return out


def dumps_json(rows: List[dict]) -> str:
    return json.dumps(rows, indent=2)


def csv_row(now_str: str, r: dict, thr: Thresholds) -> List[str]:
    t = r.get("temp")
    return [
        now_str,
        csv_safe(r["slot"]),
        csv_safe(r.get("address", "")),
        f"{t:.4f}" if t is not None else "",
        thr.status(t) if t is not None else "ERROR",
        csv_safe(r.get("source", "")),
        r["kind"],
        csv_safe(r.get("error") or ""),
    ]


def write_csv(path: str, rows: List[List[str]]) -> None:
    with open_for_write(path) as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(rows)


def validate_exit_code(memory_results: List[dict], thr: Thresholds) -> int:
    """0 all OK, 1 WARN/HOT, 2 no sensors, 3 read errors. Codes are stable."""
    if not memory_results:
        return EXIT_NO_SENSORS
    if any(r.get("error") or r.get("temp") is None for r in memory_results):
        return EXIT_READ_ERRORS
    if any(r["temp"] >= thr.warn for r in memory_results):
        return EXIT_WARN_OR_HOT
    return EXIT_OK
