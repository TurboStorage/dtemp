"""Read-only bus backends: smbus2, i2cget, and a simulated bus."""

from __future__ import annotations

import errno
import glob
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from dtemp.regs import (
    AddressBusy,
    BusError,
    BusPermissionError,
    BusUnavailable,
    DeviceAbsent,
    ReadOnlyViolation,
    family_for,
)

try:  # optional dependency
    from smbus2 import SMBus  # type: ignore

    SMBUS2_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on environment
    SMBus = None  # type: ignore
    SMBUS2_AVAILABLE = False

I2CGET_TIMEOUT_S = 2
# Adapter names that plausibly carry DIMM traffic. GPU DDC/HDMI buses
# (e.g. "AMDGPU DM i2c hw bus") are deliberately not matched; use --all-buses.
SMBUS_NAME_KEYWORDS = ("smbus", "i801", "piix4", "designware", "nforce", "ismt", "sis96x")

_EREMOTEIO = getattr(errno, "EREMOTEIO", 121)  # Linux value; absent from errno on some platforms
_ABSENT_ERRNOS = (errno.ENXIO, _EREMOTEIO)
_PERM_ERRNOS = (errno.EACCES, errno.EPERM)


def i2cget_path() -> Optional[str]:
    return shutil.which("i2cget")


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


# ── Guard ────────────────────────────────────────────────────────────────────


class ReadOnlyBus:
    """Public API is read-only and allowlisted; subclasses do the raw read."""

    def read_byte(self, addr: int, reg: int) -> int:
        fam = family_for(addr)
        if fam is None:
            raise ReadOnlyViolation(f"address 0x{addr:02x} is not a DIMM sensor address")
        if reg not in fam.byte_reads:
            raise ReadOnlyViolation(f"byte read of 0x{reg:02x} at 0x{addr:02x} not allowed")
        return self._read_byte(addr, reg)

    def read_word(self, addr: int, reg: int) -> int:
        fam = family_for(addr)
        if fam is None:
            raise ReadOnlyViolation(f"address 0x{addr:02x} is not a DIMM sensor address")
        if reg not in fam.word_reads:
            raise ReadOnlyViolation(f"word read of 0x{reg:02x} at 0x{addr:02x} not allowed")
        return self._read_word(addr, reg)

    def _read_byte(self, addr: int, reg: int) -> int:  # pragma: no cover
        raise NotImplementedError

    def _read_word(self, addr: int, reg: int) -> int:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        return None

    def __enter__(self) -> "ReadOnlyBus":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ── smbus2 ───────────────────────────────────────────────────────────────────


def map_os_error(exc: OSError) -> Exception:
    """Translate an OSError from an I2C transfer into a dtemp exception."""
    if exc.errno in _ABSENT_ERRNOS:
        return DeviceAbsent(str(exc))
    if exc.errno == errno.EBUSY:
        return AddressBusy("address claimed by a kernel driver; use --mode hwmon")
    if exc.errno in _PERM_ERRNOS:
        return BusPermissionError("permission denied (run as root)")
    if exc.errno == errno.ENOENT:
        return BusUnavailable("bus device node missing (is i2c-dev loaded?)")
    return BusError(f"I/O error: {exc}")


class Smbus2Bus(ReadOnlyBus):
    """SMBus reads through smbus2 (read_byte_data / read_word_data only)."""

    def __init__(self, bus_id: int):
        if not SMBUS2_AVAILABLE:
            raise BusUnavailable("smbus2 is not installed")
        try:
            self._bus = SMBus(bus_id)
        except OSError as exc:
            raise map_os_error(exc) from exc

    def _read_byte(self, addr: int, reg: int) -> int:
        try:
            return self._bus.read_byte_data(addr, reg)
        except OSError as exc:
            raise map_os_error(exc) from exc

    def _read_word(self, addr: int, reg: int) -> int:
        try:
            return self._bus.read_word_data(addr, reg)
        except OSError as exc:
            raise map_os_error(exc) from exc

    def close(self) -> None:
        self._bus.close()


# ── i2cget ───────────────────────────────────────────────────────────────────


def build_i2cget_argv(exe: str, bus_id: int, addr: int, reg: int, width: str) -> List[str]:
    """The only command line dtemp ever runs for direct reads (allowlist-checked)."""
    fam = family_for(addr)
    if fam is None:
        raise ReadOnlyViolation(f"address 0x{addr:02x} is not a DIMM sensor address")
    allowed = {"b": fam.byte_reads, "w": fam.word_reads}.get(width)
    if allowed is None or reg not in allowed:
        raise ReadOnlyViolation(f"i2cget mode {width!r} register 0x{reg:02x} not allowed")
    return [exe, "-y", str(bus_id), f"0x{addr:02x}", f"0x{reg:02x}", width]


def classify_i2cget_failure(stderr: str) -> Exception:
    text = (stderr or "").strip()
    low = text.lower()
    if "permission denied" in low:
        return BusPermissionError("permission denied (run as root)")
    if "no such file" in low:
        return BusUnavailable("bus device node missing (is i2c-dev loaded?)")
    if "busy" in low:
        return AddressBusy("address claimed by a kernel driver; use --mode hwmon")
    if "read failed" in low or "no such device" in low:
        return DeviceAbsent(text)
    return BusError(text or "i2cget failed")


class I2cgetBus(ReadOnlyBus):
    """Reads via the i2cget binary (mode b or w only, never forced)."""

    def __init__(self, bus_id: int, exe: Optional[str] = None):
        self._bus_id = bus_id
        self._exe = exe or i2cget_path()
        if not self._exe:
            raise BusUnavailable("i2cget not found (apt install i2c-tools)")

    def _run(self, addr: int, reg: int, width: str) -> int:
        argv = build_i2cget_argv(self._exe, self._bus_id, addr, reg, width)
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=I2CGET_TIMEOUT_S, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise BusError("i2cget timed out") from exc
        except OSError as exc:
            raise BusUnavailable(f"cannot run i2cget: {exc}") from exc
        if proc.returncode != 0:
            raise classify_i2cget_failure(proc.stderr)
        try:
            return int(proc.stdout.strip(), 16)
        except ValueError as exc:
            raise BusError(f"unparseable i2cget output: {proc.stdout.strip()!r}") from exc

    def _read_byte(self, addr: int, reg: int) -> int:
        return self._run(addr, reg, "b")

    def _read_word(self, addr: int, reg: int) -> int:
        return self._run(addr, reg, "w")


def open_real_bus(bus_id: int, force_i2cget: bool = False) -> ReadOnlyBus:
    """smbus2 when installed, otherwise i2cget. Raises BusUnavailable if neither."""
    if SMBUS2_AVAILABLE and not force_i2cget:
        return Smbus2Bus(bus_id)
    return I2cgetBus(bus_id)


# ── Simulation ───────────────────────────────────────────────────────────────


class SimBus(ReadOnlyBus):
    """Synthetic register images behind the same guard and decode path."""

    def __init__(self, devices: Dict[int, dict]):
        self._devices = devices

    def _lookup(self, addr: int) -> dict:
        dev = self._devices.get(addr)
        if dev is None:
            raise DeviceAbsent("no ack (simulated)")
        fault = dev.get("fault")
        if fault == "busy":
            raise AddressBusy("address claimed by a kernel driver; use --mode hwmon")
        if fault == "io":
            raise BusError("I/O error (simulated)")
        return dev

    def _read_byte(self, addr: int, reg: int) -> int:
        return self._lookup(addr)["regs"].get(reg, 0)

    def _read_word(self, addr: int, reg: int) -> int:
        """SMBus word semantics: first wire byte in the low byte."""
        regs = self._lookup(addr)["regs"]
        if "words" in regs and reg in regs["words"]:
            return regs["words"][reg]
        return regs.get(reg, 0) | (regs.get(reg + 1, 0) << 8)


# ── Bus discovery ────────────────────────────────────────────────────────────


def _bus_number(path: str) -> Optional[int]:
    match = re.search(r"i2c-(\d+)$", path)
    return int(match.group(1)) if match else None


def probe_smbus_buses(
    include_all: bool = False,
    dev_glob: str = "/dev/i2c-*",
    adapter_root: str = "/sys/class/i2c-adapter",
) -> List[int]:
    """Bus numbers worth scanning, sorted. SMBus-class adapters only by default."""
    found = sorted(n for n in map(_bus_number, glob.glob(dev_glob)) if n is not None)
    if include_all:
        return found
    buses: List[int] = []
    for bus_id in found:
        try:
            name = (Path(adapter_root) / f"i2c-{bus_id}" / "name").read_text().lower()
        except OSError:
            buses.append(bus_id)  # name unknown: include rather than miss the DIMM bus
            continue
        if any(k in name for k in SMBUS_NAME_KEYWORDS):
            buses.append(bus_id)
    return buses
