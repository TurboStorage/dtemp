"""Optional DIMM labels from dmidecode (``--dmi-labels``).

Best effort only: hwmon/I2C enumeration order and DMI slot order are not
guaranteed to match, so labels may be attached to the wrong module. It is
therefore opt-in. Serial numbers are deliberately never parsed.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from typing import Dict, List

from dtemp.fsutil import clean_text
from dtemp.model import Reading

DMIDECODE_TIMEOUT_S = 3


def parse_dmidecode_memory(text: str) -> List[Dict[str, str]]:
    devices = []
    for block in text.split("\n\n"):
        if "Memory Device" not in block:
            continue
        data = {}
        for raw_line in block.splitlines():
            if ":" in raw_line:
                key, value = raw_line.split(":", 1)
                data[clean_text(key).strip()] = clean_text(value).strip()
        size = data.get("Size", "")
        if not size or size == "No Module Installed":
            continue
        devices.append(
            {
                "locator": data.get("Locator", ""),
                "bank": data.get("Bank Locator", ""),
                "manufacturer": data.get("Manufacturer", ""),
                "part": data.get("Part Number", ""),
            }
        )
    return devices


def read_dmidecode() -> List[Dict[str, str]]:
    exe = shutil.which("dmidecode")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "-t", "memory"], capture_output=True, text=True, timeout=DMIDECODE_TIMEOUT_S, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    return parse_dmidecode_memory(proc.stdout) if proc.returncode == 0 else []


def display_name(device: Dict[str, str]) -> str:
    primary = " ".join(p for p in (device["manufacturer"], device["part"]) if p)
    location = " ".join(p for p in (device["locator"], device["bank"]) if p)
    return " | ".join(p for p in (primary, location) if p)


def apply_labels(readings: List[Reading], devices: List[Dict[str, str]]) -> List[Reading]:
    """Return new readings whose ``address`` shows the DMI name (index order)."""
    out = []
    for index, reading in enumerate(readings):
        name = display_name(devices[index]) if index < len(devices) else ""
        out.append(replace(reading, address=name) if name else reading)
    return out
