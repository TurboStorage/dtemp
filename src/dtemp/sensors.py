"""Direct-read sensor identification and reading (SPD5118 and JC42.4).

Everything here goes through :class:`dtemp.buses.ReadOnlyBus`, so only
allowlisted read operations are possible.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from dtemp import regs
from dtemp.buses import ReadOnlyBus
from dtemp.model import Reading
from dtemp.regs import (
    AddressBusy,
    BusError,
    BusPermissionError,
    BusUnavailable,
    DeviceAbsent,
)

JC42_CONFIG_RESERVED_MASK = 0xF820
JC42_CONFIG_SHUTDOWN = 0x0100


def _label(bus_id: int, addr: int) -> str:
    return f"i2c-{bus_id} 0x{addr:02x}"


def read_spd5118(bus: ReadOnlyBus, bus_id: int, addr: int, source: str) -> Optional[Reading]:
    """Identify and read one address. None if it is not an SPD5118 hub.

    Raises DeviceAbsent / AddressBusy / BusPermissionError / BusError.
    """
    ident = bus.read_word(addr, regs.SPD5118_REG_TYPE)
    common = dict(slot="", address=_label(bus_id, addr), source=source, bus=bus_id, i2c_addr=addr)
    if ident != regs.SPD5118_TYPE_WORD:
        return _unidentified_hub(bus, ident, common)

    if not bus.read_byte(addr, regs.SPD5118_REG_CAPABILITY) & regs.SPD5118_CAP_TS_SUPPORT:
        return Reading(error="hub reports no temperature sensor (MR5)", **common)

    if bus.read_byte(addr, regs.SPD5118_REG_TS_CONFIG) & regs.SPD5118_TS_DISABLE:
        return Reading(
            error="temperature sensor disabled (TS_CONFIG bit0); dtemp is read-only and will not enable it",
            **common,
        )

    word = bus.read_word(addr, regs.SPD5118_REG_TEMP)
    return _finish(regs.decode_spd5118_word(word), word, common)


def _unidentified_hub(bus: ReadOnlyBus, ident: int, common: dict) -> Optional[Reading]:
    """Type registers did not read 0x5118: usually some other device.

    Some hubs return 0 in MR0/1 while a non-zero NVM page is selected in MR11.
    Fixing that needs a bus write, which dtemp never does, so report it.
    """
    if ident != 0:
        return None
    page = bus.read_byte(common["i2c_addr"], regs.SPD5118_REG_LEGACY_MODE) & regs.SPD5118_PAGE_MASK
    if not page:
        return None
    return Reading(
        error=(
            f"possible SPD5118 with NVM page {page} selected; dtemp is read-only "
            "and will not change it (use the kernel spd5118 driver instead)"
        ),
        **common,
    )


def read_jc42(bus: ReadOnlyBus, bus_id: int, addr: int, source: str) -> Optional[Reading]:
    """Identify (same checks as the Linux jc42 driver) and read a DDR3/DDR4 sensor."""
    common = dict(slot="", address=_label(bus_id, addr), source=source, bus=bus_id, i2c_addr=addr)
    cap = regs.swap16(bus.read_word(addr, regs.JC42_REG_CAP))
    config = regs.swap16(bus.read_word(addr, regs.JC42_REG_CONFIG))
    manid = regs.swap16(bus.read_word(addr, regs.JC42_REG_MANID))
    if cap & 0xFF00 or config & JC42_CONFIG_RESERVED_MASK or manid not in regs.JC42_MANUFACTURER_IDS:
        return None
    if config & JC42_CONFIG_SHUTDOWN:
        return Reading(error="temperature sensor in shutdown mode; dtemp will not change it", **common)
    word = bus.read_word(addr, regs.JC42_REG_TEMP)
    return _finish(regs.decode_jc42_word(regs.swap16(word)), word, common)


def _finish(temp_c: float, word: int, common: dict) -> Reading:
    if not regs.plausible(temp_c):
        return Reading(error=f"implausible reading {temp_c:.2f} C (raw 0x{word:04x})", raw_word=word, **common)
    return Reading(temp_c=temp_c, raw_word=word, **common)


_Reader = Callable[[ReadOnlyBus, int, int, str], Optional[Reading]]


def scan_bus(bus: ReadOnlyBus, bus_id: int, source_prefix: str) -> Tuple[List[Reading], List[str]]:
    """Scan every DIMM sensor address on one bus.

    Returns (readings, notes). Notes are informational (e.g. an address owned
    by a kernel driver); readings carry real errors.
    """
    readings: List[Reading] = []
    notes: List[str] = []
    transfer_errors: List[str] = []

    plan = (
        (regs.SPD5118_ADDRESSES, read_spd5118, f"{source_prefix}/spd5118"),
        (regs.JC42_ADDRESSES, read_jc42, f"{source_prefix}/jc42"),
    )
    for addresses, reader, source in plan:
        for addr in addresses:
            try:
                reading = reader(bus, bus_id, addr, source)
            except DeviceAbsent:
                continue
            except AddressBusy:
                notes.append(f"{_label(bus_id, addr)} is claimed by a kernel driver (see --mode hwmon)")
                continue
            except (BusPermissionError, BusUnavailable) as exc:
                return readings + [_bus_error(bus_id, source_prefix, str(exc))], notes
            except BusError as exc:
                transfer_errors.append(f"{_label(bus_id, addr)}: {exc}")
                continue
            if reading is not None:
                readings.append(reading)

    if not readings and transfer_errors:
        detail = f"{len(transfer_errors)} transfer error(s), first: {transfer_errors[0]}"
        readings.append(_bus_error(bus_id, source_prefix, detail))
    return readings, notes


def _bus_error(bus_id: int, source_prefix: str, message: str) -> Reading:
    return Reading(slot=f"i2c-{bus_id}", address=f"i2c-{bus_id}", source=source_prefix, error=message, bus=bus_id)


def scan_buses(opener: Callable[[int], ReadOnlyBus], bus_ids: List[int], source_prefix: str):
    """Scan several buses; bus-open failures become per-bus error readings."""
    readings: List[Reading] = []
    notes: List[str] = []
    for bus_id in bus_ids:
        try:
            bus = opener(bus_id)
        except (BusPermissionError, BusUnavailable) as exc:
            readings.append(_bus_error(bus_id, source_prefix, str(exc)))
            continue
        try:
            found, found_notes = scan_bus(bus, bus_id, source_prefix)
        finally:
            bus.close()
        readings.extend(found)
        notes.extend(found_notes)
    return readings, notes
