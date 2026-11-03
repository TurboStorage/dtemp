import os
from pathlib import Path

import pytest

from dtemp import hwmon, sim
from dtemp.buses import SimBus
from dtemp.setup import SetupError, run_setup


def make_hwmon(root: Path, index: int, name: str, files: dict, device_target: Path = None):
    d = root / "class" / "hwmon" / f"hwmon{index}"
    d.mkdir(parents=True)
    (d / "name").write_text(name + "\n")
    for fname, content in files.items():
        (d / fname).write_text(str(content) + "\n")
    if device_target is not None:
        try:
            os.symlink(device_target, d / "device", target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable")
    return d


def test_reads_spd5118_hwmon_with_address_from_symlink(tmp_path):
    target = tmp_path / "devices" / "i2c-0" / "0-0052"
    target.mkdir(parents=True)
    make_hwmon(tmp_path, 3, "spd5118", {"temp1_input": 39000}, target)
    (reading,) = hwmon.read_memory(str(tmp_path))
    assert reading.temp_c == 39.0
    assert reading.address == "i2c-0 0x52"
    assert reading.source == "sysfs/spd5118"


def test_failed_hwmon_read_is_an_error_not_a_silent_gap(tmp_path):
    make_hwmon(tmp_path, 0, "spd5118", {})  # temp1_input missing
    (reading,) = hwmon.read_memory(str(tmp_path))
    assert reading.temp_c is None and "temp1_input" in reading.error


def test_garbage_and_implausible_hwmon_values_are_errors(tmp_path):
    make_hwmon(tmp_path, 0, "spd5118", {"temp1_input": "junk"})
    make_hwmon(tmp_path, 1, "jc42", {"temp1_input": 300000})
    a, b = hwmon.read_memory(str(tmp_path))
    assert a.error and b.error and "implausible" in b.error


def test_disabled_sensor_in_kernel_driver_is_an_error(tmp_path):
    make_hwmon(tmp_path, 0, "spd5118", {"temp1_input": 30000, "temp1_enable": 0})
    (reading,) = hwmon.read_memory(str(tmp_path))
    assert "disabled" in reading.error


def test_memory_reader_ignores_other_drivers_and_uses_label(tmp_path):
    make_hwmon(tmp_path, 0, "coretemp", {"temp1_input": 50000})
    make_hwmon(tmp_path, 1, "jc42", {"temp1_input": 41500, "temp1_label": "DIMM A1"})
    (reading,) = hwmon.read_memory(str(tmp_path))
    assert reading.slot == "DIMM A1" and reading.temp_c == 41.5


def test_storage_reader(tmp_path):
    make_hwmon(tmp_path, 0, "nvme", {"temp1_input": 44850})
    make_hwmon(tmp_path, 1, "nvme", {"temp1_input": 999999})  # implausible: skipped
    make_hwmon(tmp_path, 2, "spd5118", {"temp1_input": 30000})
    readings = hwmon.read_storage(str(tmp_path))
    assert [r.temp_c for r in readings] == [44.85]
    assert readings[0].kind == "storage"


# ── --setup ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _pretend_root(monkeypatch):
    import dtemp.setup as setup_mod

    monkeypatch.setattr(setup_mod, "is_root", lambda: True)


def sim_opener(*specs):
    bus = SimBus({s["addr"]: sim.build_device(s) for s in specs})
    return lambda n: bus


def new_device_path(root, bus_id):
    p = root / "bus" / "i2c" / "devices" / f"i2c-{bus_id}"
    p.mkdir(parents=True, exist_ok=True)
    return p / "new_device"


def test_setup_binds_only_positively_identified_sensors(tmp_path):
    target = new_device_path(tmp_path, 1)
    opener = sim_opener({"addr": 0x50, "temp_c": 30.0}, {"addr": 0x51, "foreign": True},
                        {"addr": 0x18, "temp_c": 30.0}, {"addr": 0x53, "temp_c": 30.0, "ts_enabled": False})
    mods = []
    actions = run_setup(opener, bus_ids=[1], sys_root=str(tmp_path), modprobe=lambda m: mods.append(m) or True,
                        persist_path=str(tmp_path / "conf"), settle_s=0)
    written = target.read_text()
    assert written in ("jc42 0x18", "spd5118 0x50")  # file holds the last write on a plain file
    text = "\n".join(actions)
    assert "instantiated spd5118 at i2c-1 0x50" in text
    assert "instantiated jc42 at i2c-1 0x18" in text
    assert "0x51" not in text and "0x53" not in text  # foreign + disabled devices never bound
    assert "i2c-dev" in mods and "spd5118" in mods


def test_setup_dry_run_changes_nothing(tmp_path):
    target = new_device_path(tmp_path, 1)
    actions = run_setup(sim_opener({"addr": 0x50, "temp_c": 30.0}), bus_ids=[1], sys_root=str(tmp_path),
                        dry_run=True, persist=True, persist_path=str(tmp_path / "conf"))
    assert not target.exists() and not (tmp_path / "conf").exists()
    assert any(a.startswith("would write 'spd5118 0x50'") for a in actions)


def test_setup_skips_when_hwmon_already_present(tmp_path):
    make_hwmon(tmp_path, 0, "spd5118", {"temp1_input": 30000})
    actions = run_setup(sim_opener({"addr": 0x50, "temp_c": 30.0}), bus_ids=[1], sys_root=str(tmp_path),
                        modprobe=lambda m: True, persist_path=str(tmp_path / "c"), settle_s=0)
    assert any("already present" in a for a in actions)


def test_setup_persist_writes_module_list(tmp_path):
    conf = tmp_path / "dtemp.conf"
    run_setup(sim_opener(), bus_ids=[], sys_root=str(tmp_path), persist=True, modprobe=lambda m: True,
              persist_path=str(conf), settle_s=0)
    assert conf.read_text().splitlines()[1:] == ["i2c-dev", "spd5118", "jc42"]


def test_setup_requires_root(monkeypatch):
    import dtemp.setup as setup_mod

    monkeypatch.setattr(setup_mod, "is_root", lambda: False)
    with pytest.raises(SetupError):
        run_setup(sim_opener(), bus_ids=[])


def test_setup_reports_nothing_found(tmp_path):
    actions = run_setup(sim_opener(), bus_ids=[1], sys_root=str(tmp_path), modprobe=lambda m: False,
                        persist_path=str(tmp_path / "c"), settle_s=0)
    assert any("nothing instantiated" in a for a in actions)
