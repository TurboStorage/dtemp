"""Opt-in system setup (``dtemp --setup``): load modules, instantiate kernel drivers.

Nothing here runs unless the user asks for it. Instantiation is gated by the
same allowlisted, read-only identification used for direct reads: a driver is
only bound to an address after the device has positively identified itself as
an SPD5118 hub or a JC42.4 sensor. dtemp itself never writes to any DIMM
register; the kernel driver, once bound, does what the kernel driver does
(see README "Read-only guarantee").
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable, List, Optional

from dtemp.fsutil import write_text_nofollow
from dtemp.buses import ReadOnlyBus, is_root, probe_smbus_buses
from dtemp.hwmon import read_memory
from dtemp.sensors import scan_bus

PLATFORM_SMBUS_DRIVERS = ("i2c-i801", "i2c-piix4", "i2c-designware-pci", "i2c-nforce2", "i2c-sis96x")
SENSOR_MODULES = ("spd5118", "jc42", "drivetemp")
PERSIST_PATH = "/etc/modules-load.d/dtemp.conf"
PERSIST_TEXT = "# Written by dtemp --setup --persist\ni2c-dev\nspd5118\njc42\n"
MODPROBE_TIMEOUT_S = 5
SETTLE_S = 0.5


class SetupError(Exception):
    pass


def _modprobe(module: str) -> bool:
    try:
        proc = subprocess.run(
            ["modprobe", module], capture_output=True, timeout=MODPROBE_TIMEOUT_S, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def run_setup(
    opener: Callable[[int], ReadOnlyBus],
    bus_ids: Optional[List[int]] = None,
    sys_root: str = "/sys",
    persist: bool = False,
    dry_run: bool = False,
    modprobe: Callable[[str], bool] = _modprobe,
    persist_path: str = PERSIST_PATH,
    settle_s: float = SETTLE_S,
) -> List[str]:
    """Return a human-readable list of actions taken (or planned when dry_run)."""
    if not dry_run and not is_root():
        raise SetupError("--setup needs root (run with sudo), or use --dry-run to preview")

    actions: List[str] = []
    for module in ("i2c-dev", *PLATFORM_SMBUS_DRIVERS, *SENSOR_MODULES):
        if dry_run:
            actions.append(f"would modprobe {module}")
        else:
            ok = modprobe(module)
            actions.append(f"modprobe {module}: {'ok' if ok else 'not available (ignored)'}")
    if not dry_run:
        time.sleep(settle_s)

    present = bool(read_memory(sys_root))
    new_actions = _instantiate(opener, bus_ids, sys_root, dry_run)
    if present and not any(a.startswith(("instantiated", "would write '")) for a in new_actions):
        new_actions = ["hwmon DIMM devices already present; nothing further to instantiate"]
    actions.extend(new_actions)

    if persist:
        if dry_run:
            actions.append(f"would write {persist_path}")
        else:
            try:
                write_text_nofollow(persist_path, PERSIST_TEXT)
                actions.append(f"wrote {persist_path}")
            except OSError as exc:
                actions.append(f"could not write {persist_path}: {exc}")
    return actions


def _instantiate(opener, bus_ids, sys_root, dry_run) -> List[str]:
    actions: List[str] = []
    buses = probe_smbus_buses() if bus_ids is None else bus_ids
    for bus_id in buses:
        try:
            bus = opener(bus_id)
        except Exception as exc:  # any bus-open failure: report and continue
            actions.append(f"i2c-{bus_id}: cannot open ({exc})")
            continue
        try:
            found, _ = scan_bus(bus, bus_id, "setup")
        finally:
            bus.close()
        for reading in found:
            if reading.error is not None or reading.i2c_addr is None:
                continue
            driver = reading.source.rsplit("/", 1)[-1]
            target = Path(sys_root) / "bus" / "i2c" / "devices" / f"i2c-{bus_id}" / "new_device"
            line = f"{driver} 0x{reading.i2c_addr:02x}"
            if dry_run:
                actions.append(f"would write '{line}' to {target}")
                continue
            try:
                target.write_text(line)
                actions.append(f"instantiated {driver} at {reading.address}")
            except OSError as exc:
                actions.append(f"could not instantiate {driver} at {reading.address}: {exc}")
    if not actions:
        actions.append("no DIMM sensors identified on the scanned buses; nothing instantiated")
    return actions
