"""``dtemp --diag``: what can this machine do, and what does a bus scan find."""

from __future__ import annotations

import glob
import platform
from pathlib import Path
from typing import Callable, List, Optional

from dtemp import __version__, hwmon, regs, ui
from dtemp.buses import (
    SMBUS2_AVAILABLE,
    ReadOnlyBus,
    i2cget_path,
    is_root,
    probe_smbus_buses,
)
from dtemp.sensors import scan_buses

KERNEL_MODULES = ("i2c_dev", "i2c_i801", "i2c_piix4", "i2c_designware_pci", "spd5118", "jc42")


def _adapter_name(bus_id: int, adapter_root: str = "/sys/class/i2c-adapter") -> str:
    try:
        return (Path(adapter_root) / f"i2c-{bus_id}" / "name").read_text().strip()
    except OSError:
        return "unknown adapter"


def run_diag(
    opener: Optional[Callable[[int], ReadOnlyBus]] = None,
    bus_ids: Optional[List[int]] = None,
    include_all_buses: bool = False,
    simulated: bool = False,
    sys_root: str = "/sys",
    out=print,
) -> None:
    b, r, d = ui.BOLD, ui.RESET, ui.DIM
    out(f"\n{b}=== dtemp {__version__} diagnostics ==={r}\n")
    out(f"  Platform : {platform.system()} {platform.release()}  (root: {'yes' if is_root() else 'no'})")
    if simulated:
        out("  Mode     : SIMULATED (synthetic fixture, no hardware is touched)")

    out("\n  Read methods")
    memory = hwmon.read_memory(sys_root)
    for driver in hwmon.MEMORY_DRIVERS:
        addrs = [m.address for m in memory if m.source.endswith(driver)]
        state = f"{len(addrs)} device(s): {', '.join(addrs)}" if addrs else "no devices (try: sudo dtemp --setup)"
        out(f"    {driver:<8} (sysfs) : {state}")
    out(f"    smbus2   (lib)   : {'available' if SMBUS2_AVAILABLE else 'not installed (optional: pip install smbus2)'}")
    out(f"    i2cget   (bin)   : {'available' if i2cget_path() else 'not found (apt install i2c-tools)'}")

    out("\n  Kernel modules")
    for mod in KERNEL_MODULES:
        loaded = bool(glob.glob("/dev/i2c-*")) if mod == "i2c_dev" else Path(f"{sys_root}/module/{mod}").exists()
        hint = "" if loaded else f"  ->  sudo modprobe {mod.replace('_', '-')}"
        out(f"    {mod:<20}: {'loaded' if loaded else 'not loaded' + hint}")

    buses = bus_ids if bus_ids is not None else probe_smbus_buses(include_all=include_all_buses)
    out("\n  Buses to scan")
    if buses:
        for bus_id in buses:
            out(f"    i2c-{bus_id}: {'(simulated)' if simulated else _adapter_name(bus_id)}")
    else:
        out("    none (is i2c-dev loaded? non-SMBus adapters are skipped unless --all-buses)")

    storage = hwmon.read_storage(sys_root)
    out(f"\n  Storage sensors: {len(storage)}")
    for s in storage:
        out(f"    {s.slot:<10} {s.address:<24} {s.temp_c:.1f} C  ({s.source})")

    out("\n  Read-only allowlist (anything else is refused before touching the bus)")
    for name, info in regs.allowlist_summary().items():
        out(f"    {name:<8} addr {', '.join(info['addresses'])}  byte {info['byte_reads']}  word {info['word_reads']}")

    if opener is None:
        out(f"\n  {d}Direct scan skipped: no read method available (install i2c-tools or smbus2).{r}\n")
        return
    if not simulated and not is_root():
        out(f"\n  {d}Direct SMBus scan skipped (needs root). Re-run with sudo.{r}\n")
        return
    out("\n  Direct scan (read-only identification)")
    readings, notes = scan_buses(opener, buses, "diag")
    for reading in readings:
        detail = f"{reading.temp_c:.2f} C" if reading.temp_c is not None else f"ERROR: {reading.error}"
        out(f"    {reading.address:<16} {reading.source.rsplit('/', 1)[-1]:<8} {detail}")
    for note in notes:
        out(f"    note: {note}")
    if not readings and not notes:
        out("    no DIMM sensors responded")
    out("")
