import errno
import subprocess

import pytest

import dtemp.buses as buses
from dtemp import regs
from dtemp.buses import I2cgetBus, Smbus2Bus, classify_i2cget_failure, map_os_error, probe_smbus_buses
from dtemp.regs import (
    AddressBusy,
    BusError,
    BusPermissionError,
    BusUnavailable,
    DeviceAbsent,
)


@pytest.mark.parametrize(
    "code, expected",
    [
        (errno.ENXIO, DeviceAbsent),
        (121, DeviceAbsent),  # EREMOTEIO on Linux
        (errno.EBUSY, AddressBusy),
        (errno.EACCES, BusPermissionError),
        (errno.EPERM, BusPermissionError),
        (errno.ENOENT, BusUnavailable),
        (errno.EIO, BusError),
    ],
)
def test_map_os_error(code, expected):
    assert isinstance(map_os_error(OSError(code, "x")), expected)


@pytest.mark.parametrize(
    "stderr, expected",
    [
        ("Error: Could not open file `/dev/i2c-1' or `/dev/i2c/1': Permission denied", BusPermissionError),
        ("Error: Could not open file `/dev/i2c-9': No such file or directory", BusUnavailable),
        ("Error: Could not set address to 0x50: Device or resource busy", AddressBusy),
        ("Error: Read failed", DeviceAbsent),
        ("something odd", BusError),
        ("", BusError),
    ],
)
def test_classify_i2cget_failure(stderr, expected):
    assert isinstance(classify_i2cget_failure(stderr), expected)


class Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_i2cget_bus_parses_output_and_uses_word_mode(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return Proc(stdout="0x0190\n")

    monkeypatch.setattr(buses.subprocess, "run", fake_run)
    bus = I2cgetBus(3, exe="/x/i2cget")
    assert bus.read_word(0x50, regs.SPD5118_REG_TEMP) == 0x0190
    assert seen == [["/x/i2cget", "-y", "3", "0x50", "0x31", "w"]]


def test_i2cget_bus_reports_failures_by_type(monkeypatch):
    monkeypatch.setattr(buses.subprocess, "run", lambda *a, **k: Proc(1, "", "Error: Read failed"))
    with pytest.raises(DeviceAbsent):
        I2cgetBus(1, exe="x").read_byte(0x50, regs.SPD5118_REG_CAPABILITY)


def test_i2cget_bus_garbage_output(monkeypatch):
    monkeypatch.setattr(buses.subprocess, "run", lambda *a, **k: Proc(0, "zzz"))
    with pytest.raises(BusError):
        I2cgetBus(1, exe="x").read_byte(0x50, regs.SPD5118_REG_CAPABILITY)


def test_i2cget_bus_timeout_and_missing_binary(monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("i2cget", 2)

    monkeypatch.setattr(buses.subprocess, "run", timeout)
    with pytest.raises(BusError):
        I2cgetBus(1, exe="x").read_byte(0x50, regs.SPD5118_REG_CAPABILITY)

    def missing(*a, **k):
        raise FileNotFoundError("i2cget")

    monkeypatch.setattr(buses.subprocess, "run", missing)
    with pytest.raises(BusUnavailable):
        I2cgetBus(1, exe="x").read_byte(0x50, regs.SPD5118_REG_CAPABILITY)


def test_i2cget_bus_requires_binary(monkeypatch):
    monkeypatch.setattr(buses, "i2cget_path", lambda: None)
    with pytest.raises(BusUnavailable):
        I2cgetBus(1)


def test_smbus2_bus_unavailable_without_library(monkeypatch):
    monkeypatch.setattr(buses, "SMBUS2_AVAILABLE", False)
    with pytest.raises(BusUnavailable):
        Smbus2Bus(0)


def test_smbus2_bus_wraps_os_errors(monkeypatch):
    class FakeSMBus:
        def __init__(self, bus_id):
            pass

        def read_byte_data(self, addr, reg):
            raise OSError(errno.EBUSY, "busy")

        def read_word_data(self, addr, reg):
            return 0x1851

        def close(self):
            pass

    monkeypatch.setattr(buses, "SMBUS2_AVAILABLE", True)
    monkeypatch.setattr(buses, "SMBus", FakeSMBus)
    bus = Smbus2Bus(0)
    assert bus.read_word(0x50, regs.SPD5118_REG_TYPE) == 0x1851
    with pytest.raises(AddressBusy):
        bus.read_byte(0x50, regs.SPD5118_REG_CAPABILITY)
    bus.close()


def test_probe_smbus_buses_filters_gpu_adapters(tmp_path):
    dev = tmp_path / "dev"
    dev.mkdir()
    adapters = tmp_path / "adapters"
    for number, name in {0: "SMBus I801 adapter at f000", 1: "AMDGPU DM i2c hw bus 0",
                         2: "SMBus PIIX4 adapter port 0", 3: None, 10: "NVIDIA i2c adapter 1"}.items():
        (dev / f"i2c-{number}").write_text("")
        if name:
            (adapters / f"i2c-{number}").mkdir(parents=True)
            (adapters / f"i2c-{number}" / "name").write_text(name)
    kwargs = dict(dev_glob=str(dev / "i2c-*"), adapter_root=str(adapters))
    assert probe_smbus_buses(**kwargs) == [0, 2, 3]  # unknown name is kept, GPU buses dropped
    assert probe_smbus_buses(include_all=True, **kwargs) == [0, 1, 2, 3, 10]  # numeric, not lexical
