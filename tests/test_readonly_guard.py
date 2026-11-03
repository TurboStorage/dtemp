"""The read-only guarantee: allowlist enforcement and a static scan of the sources."""

import re
from pathlib import Path

import pytest

from dtemp import regs
from dtemp.buses import I2cgetBus, ReadOnlyBus, SimBus, build_i2cget_argv
from dtemp.regs import ReadOnlyViolation

SRC = Path(__file__).resolve().parents[1] / "src" / "dtemp"


class RecordingBus(ReadOnlyBus):
    def __init__(self):
        self.calls = []

    def _read_byte(self, addr, reg):
        self.calls.append(("b", addr, reg))
        return 0

    def _read_word(self, addr, reg):
        self.calls.append(("w", addr, reg))
        return 0


def test_every_allowlisted_read_reaches_the_bus():
    bus = RecordingBus()
    for fam in regs.FAMILIES:
        addr = fam.addresses.start
        for reg in fam.byte_reads:
            bus.read_byte(addr, reg)
        for reg in fam.word_reads:
            bus.read_word(addr, reg)
    assert len(bus.calls) == sum(len(f.byte_reads) + len(f.word_reads) for f in regs.FAMILIES)


@pytest.mark.parametrize("addr", [0x00, 0x17, 0x20, 0x4F, 0x58, 0x70, 0x7F])
def test_addresses_outside_dimm_ranges_are_refused_before_io(addr):
    bus = RecordingBus()
    with pytest.raises(ReadOnlyViolation):
        bus.read_byte(addr, regs.SPD5118_REG_CAPABILITY)
    with pytest.raises(ReadOnlyViolation):
        bus.read_word(addr, regs.SPD5118_REG_TEMP)
    assert bus.calls == []


@pytest.mark.parametrize("reg", [r for r in range(0x100) if r not in regs.SPD5118.byte_reads])
def test_spd5118_byte_reads_off_allowlist_refused(reg):
    bus = RecordingBus()
    with pytest.raises(ReadOnlyViolation):
        bus.read_byte(0x50, reg)
    assert bus.calls == []


@pytest.mark.parametrize("reg", [r for r in range(0x100) if r not in regs.SPD5118.word_reads])
def test_spd5118_word_reads_off_allowlist_refused(reg):
    bus = RecordingBus()
    with pytest.raises(ReadOnlyViolation):
        bus.read_word(0x57, reg)
    assert bus.calls == []


def test_spd5118_nvm_and_config_registers_are_not_readable_or_writable_through_the_api():
    # NVM starts at 0x80; MR12 (0x0C) is the write-protect register; MR13.. are config.
    for reg in (0x0C, 0x0D, 0x80, 0xFF):
        assert reg not in regs.SPD5118.byte_reads | regs.SPD5118.word_reads


def test_the_public_bus_api_has_no_write_methods():
    public = {n for n in dir(ReadOnlyBus) if not n.startswith("_")}
    assert public == {"read_byte", "read_word", "close"}
    assert not [n for n in dir(SimBus) if n.startswith("write")]


def test_i2cget_argv_is_plain_read_never_forced():
    argv = build_i2cget_argv("/usr/sbin/i2cget", 3, 0x52, regs.SPD5118_REG_TEMP, "w")
    assert argv == ["/usr/sbin/i2cget", "-y", "3", "0x52", "0x31", "w"]
    assert "-f" not in argv


@pytest.mark.parametrize(
    "addr, reg, width",
    [(0x50, 0x31, "b"), (0x50, 0x05, "w"), (0x50, 0x80, "b"), (0x50, 0x31, "c"), (0x50, 0x31, "i"),
     (0x60, 0x31, "w"), (0x18, 0x05, "b")],
)
def test_i2cget_argv_builder_refuses_anything_off_allowlist(addr, reg, width):
    with pytest.raises(ReadOnlyViolation):
        build_i2cget_argv("i2cget", 1, addr, reg, width)


def test_i2cget_backend_never_spawns_process_for_forbidden_read(monkeypatch):
    import dtemp.buses as buses

    def boom(*a, **k):
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(buses.subprocess, "run", boom)
    bus = I2cgetBus(1, exe="/bin/true")
    with pytest.raises(ReadOnlyViolation):
        bus.read_byte(0x50, 0x80)


# ── Static scan: no write-capable calls anywhere in the package ──────────────

_FORBIDDEN = [
    r"write_(byte|word|block|i2c_block|quick)",   # smbus2 write API
    r"i2c_rdwr",                                  # raw transfers
    r"\bi2cset\b", r"\bi2ctransfer\b",            # write-capable CLI tools
    r"I2C_SLAVE_FORCE", r"\bioctl\b",
    r"['\"]-f['\"]",                              # i2cget force flag
    r"eeprom",                                    # no direct EEPROM access
]


def _source_files():
    return sorted(SRC.glob("*.py"))


@pytest.mark.parametrize("pattern", _FORBIDDEN)
def test_no_forbidden_write_paths_in_sources(pattern):
    hits = []
    for path in _source_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(pattern, code, re.IGNORECASE):
                hits.append(f"{path.name}:{number}: {line.strip()}")
    assert not hits, hits


def test_only_setup_module_writes_to_sysfs_or_files():
    """File/sysfs writes are confined to the opt-in --setup module and the fsutil helpers."""
    allowed = {"setup.py", "fsutil.py"}
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        if re.search(r"\.write_text\(|open\([^)]*['\"][wa]b?['\"]", text) and path.name not in allowed:
            offenders.append(path.name)
    assert not offenders, offenders
