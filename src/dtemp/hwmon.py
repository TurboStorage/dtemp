"""sysfs hwmon readers: DIMM sensors (spd5118, jc42) and storage (nvme, drivetemp).

Reading sysfs is read-only. ``sys_root`` exists so tests can point at a fake tree.
"""

from __future__ import annotations

import glob
from pathlib import Path
from typing import List, Optional

from dtemp import regs
from dtemp.fsutil import clean_text
from dtemp.model import KIND_MEMORY, KIND_STORAGE, Reading

MEMORY_DRIVERS = ("spd5118", "jc42")
STORAGE_DRIVERS = ("nvme", "drivetemp")
STORAGE_NAME_PREFIXES = ("nvme", "sd", "hd", "vd", "xvd", "mmcblk")
MODEL_LABEL_WIDTH = 22


def _hwmon_dirs(sys_root: str) -> List[Path]:
    return [Path(p).parent for p in sorted(glob.glob(f"{sys_root}/class/hwmon/hwmon*/name"))]


def _read_text(path: Path) -> Optional[str]:
    try:
        return clean_text(path.read_text()).strip()
    except OSError:
        return None


def _address_from_symlink(hwmon: Path) -> Optional[str]:
    """hwmonN/device -> .../i2c-9/9-0050 gives 'i2c-9 0x50'."""
    try:
        name = (hwmon / "device").resolve().name
    except OSError:
        return None
    parts = name.split("-")
    if len(parts) != 2:
        return None
    try:
        return f"i2c-{int(parts[0])} 0x{int(parts[1], 16):02x}"
    except ValueError:
        return None


def _read_millidegrees(path: Path) -> Optional[float]:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return int(text) / 1000.0
    except ValueError:
        return None


def read_memory(sys_root: str = "/sys") -> List[Reading]:
    """Every spd5118 / jc42 hwmon device. A failed read is an error, never a silent gap."""
    readings = []
    for hwmon in _hwmon_dirs(sys_root):
        driver = _read_text(hwmon / "name")
        if driver not in MEMORY_DRIVERS:
            continue
        address = _address_from_symlink(hwmon) or hwmon.name
        label = _read_text(hwmon / "temp1_label")
        slot = label if label and label.lower() != "unknown" else ""
        source = f"sysfs/{driver}"
        enable = _read_text(hwmon / "temp1_enable")
        temp = _read_millidegrees(hwmon / "temp1_input")
        error = None
        if enable == "0":
            error = "temperature sensor disabled in the kernel driver"
        elif temp is None:
            error = "could not read temp1_input"
        elif not regs.plausible(temp):
            error = f"implausible reading {temp:.2f} C"
        readings.append(
            Reading(
                slot=slot,
                address=address,
                source=source,
                kind=KIND_MEMORY,
                temp_c=None if error else temp,
                error=error,
            )
        )
    return readings


def _storage_device_name(hwmon: Path) -> Optional[str]:
    try:
        parts = (hwmon / "device").resolve().parts
    except OSError:
        return None
    for part in reversed(parts):
        if part.startswith(STORAGE_NAME_PREFIXES):
            return part
    return None


def _storage_model(hwmon: Path) -> Optional[str]:
    try:
        dev = (hwmon / "device").resolve()
    except OSError:
        return None
    for candidate in (dev / "model", dev.parent / "model", dev / "device" / "model"):
        text = _read_text(candidate)
        if text:
            return text
    return None


def read_storage(sys_root: str = "/sys") -> List[Reading]:
    readings = []
    seen = set()
    for hwmon in _hwmon_dirs(sys_root):
        driver = _read_text(hwmon / "name")
        if driver not in STORAGE_DRIVERS:
            continue
        dev = _storage_device_name(hwmon) or hwmon.name
        if dev in seen:
            continue
        seen.add(dev)
        temp = _read_millidegrees(hwmon / "temp1_input")
        if temp is None or not regs.plausible(temp):
            continue
        model = _storage_model(hwmon)
        readings.append(
            Reading(
                slot=dev,
                address=model[:MODEL_LABEL_WIDTH] if model else dev,
                source=f"sysfs/{driver}",
                kind=KIND_STORAGE,
                temp_c=temp,
            )
        )
    return readings
