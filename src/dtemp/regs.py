"""Register knowledge, temperature decoding, exceptions and the read allowlist.

SPD5118 (DDR5 hub) - JEDEC JESD300-5, cross-checked against the Linux
``drivers/hwmon/spd5118.c`` driver (register numbers are facts about the chip):

======== ====== ==========================================================
MR0/MR1  0x00   device type, reads 0x51 0x18
MR5      0x05   capability, bit1 = temperature sensor supported
MR11     0x0B   legacy mode / NVM page pointer (bits 2:0 = page)
MR26     0x1A   TS_CONFIG, bit0 = TS_DISABLE (1 = sensor disabled)
MR49/50  0x31   temperature, 16 bit little endian
======== ====== ==========================================================

The SPD5118 temperature word holds an 11-bit two's complement value in bits
12:2 with a resolution of 0.25 C (range -256.00 .. +255.75 C). Bits 1:0 and
15:13 are ignored. The sensor answers on the hub's SPD address 0x50-0x57.

JC42.4 (DDR3/DDR4 module sensor, e.g. TSE2004) answers on 0x18-0x1F. Its
registers are 16 bit and big endian on the wire; temperature is register 0x05,
a 13-bit two's complement value (sign at bit 12) at 0.0625 C per LSB.

Read-only safety
----------------
Every bus backend derives from :class:`ReadOnlyBus` (see ``buses.py``), which
exposes only ``read_byte`` and ``read_word`` and checks each call against the
per-family allowlists below *before* any bus traffic. dtemp contains no data or
configuration write path (an SMBus read still sends the register address as
part of the read), so the DIMM's SPD NVM and configuration registers cannot
be modified through the direct-read code.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, NamedTuple, Optional

# ── SPD5118 ──────────────────────────────────────────────────────────────────
SPD5118_ADDRESSES = range(0x50, 0x58)
SPD5118_REG_TYPE = 0x00
SPD5118_REG_CAPABILITY = 0x05
SPD5118_REG_LEGACY_MODE = 0x0B
SPD5118_REG_TS_CONFIG = 0x1A
SPD5118_REG_TEMP = 0x31

SPD5118_TYPE_WORD = 0x1851  # SMBus word is little endian: MR0=0x51, MR1=0x18
SPD5118_CAP_TS_SUPPORT = 0x02
SPD5118_TS_DISABLE = 0x01
SPD5118_PAGE_MASK = 0x07

SPD5118_UNIT_C = 0.25
SPD5118_MIN_C = -256.0
SPD5118_MAX_C = 255.75

# ── JC42.4 ───────────────────────────────────────────────────────────────────
JC42_ADDRESSES = range(0x18, 0x20)
JC42_REG_CAP = 0x00
JC42_REG_CONFIG = 0x01
JC42_REG_TEMP = 0x05
JC42_REG_MANID = 0x06
JC42_REG_DEVICEID = 0x07
JC42_UNIT_C = 0.0625

# Manufacturer IDs accepted by the Linux jc42 driver.
JC42_MANUFACTURER_IDS = frozenset(
    {0x11D4, 0x001F, 0x1114, 0x004D, 0x00B3, 0x0054, 0x1131, 0x1B09, 0x104A, 0x1C68, 0x132D, 0x1C85}
)


class Family(NamedTuple):
    name: str
    addresses: range
    byte_reads: FrozenSet[int]
    word_reads: FrozenSet[int]


SPD5118 = Family(
    "spd5118",
    SPD5118_ADDRESSES,
    frozenset({SPD5118_REG_CAPABILITY, SPD5118_REG_LEGACY_MODE, SPD5118_REG_TS_CONFIG}),
    frozenset({SPD5118_REG_TYPE, SPD5118_REG_TEMP}),
)
JC42 = Family(
    "jc42",
    JC42_ADDRESSES,
    frozenset(),
    frozenset({JC42_REG_CAP, JC42_REG_CONFIG, JC42_REG_TEMP, JC42_REG_MANID, JC42_REG_DEVICEID}),
)
FAMILIES = (SPD5118, JC42)


def family_for(addr: int) -> Optional[Family]:
    for fam in FAMILIES:
        if addr in fam.addresses:
            return fam
    return None


# ── Exceptions ───────────────────────────────────────────────────────────────
class ReadOnlyViolation(Exception):
    """An operation outside the allowlist was requested (blocked before I/O)."""


class DeviceAbsent(Exception):
    """No device acknowledged the address."""


class AddressBusy(Exception):
    """Address is claimed by a kernel driver (spd5118 / jc42)."""


class BusPermissionError(Exception):
    """Not allowed to open the bus (needs root or membership of the i2c group)."""


class BusUnavailable(Exception):
    """The bus node is missing or no read tool is installed."""


class BusError(Exception):
    """Any other transfer failure."""


# ── Decoding ─────────────────────────────────────────────────────────────────
def decode_spd5118_word(word: int) -> float:
    """Decode the little-endian 16-bit MR49:MR50 word into degrees Celsius."""
    if not 0 <= word <= 0xFFFF:
        raise ValueError(f"temperature word out of range: {word!r}")
    value = (word >> 2) & 0x7FF
    if value & 0x400:
        value -= 0x800
    return value * SPD5118_UNIT_C


def encode_spd5118_word(temp_c: float) -> int:
    """Inverse of :func:`decode_spd5118_word` (used by simulation and tests)."""
    clamped = max(SPD5118_MIN_C, min(SPD5118_MAX_C, temp_c))
    return (round(clamped / SPD5118_UNIT_C) & 0x7FF) << 2


def swap16(word: int) -> int:
    return ((word & 0xFF) << 8) | ((word >> 8) & 0xFF)


def decode_jc42_word(word_be: int) -> float:
    """Decode a JC42 temperature register already in register (big endian) order."""
    if not 0 <= word_be <= 0xFFFF:
        raise ValueError(f"temperature word out of range: {word_be!r}")
    value = word_be & 0x1FFF
    if value & 0x1000:
        value -= 0x2000
    return value * JC42_UNIT_C


def encode_jc42_word(temp_c: float) -> int:
    clamped = max(-256.0, min(255.9375, temp_c))
    return round(clamped / JC42_UNIT_C) & 0x1FFF


HEALTHY_RANGE_C = (-40.0, 150.0)


def plausible(temp_c: float) -> bool:
    """A DIMM reading outside this range is reported as an error, not shown."""
    return HEALTHY_RANGE_C[0] <= temp_c <= HEALTHY_RANGE_C[1]


def allowlist_summary() -> Dict[str, Dict[str, list]]:
    """Human-readable allowlist (used by --diag and the README test)."""
    return {
        f.name: {
            "addresses": [f"0x{f.addresses.start:02x}-0x{f.addresses.stop - 1:02x}"],
            "byte_reads": [f"0x{r:02x}" for r in sorted(f.byte_reads)],
            "word_reads": [f"0x{r:02x}" for r in sorted(f.word_reads)],
        }
        for f in FAMILIES
    }
