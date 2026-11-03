import csv
import json
import sys
from pathlib import Path

import pytest

from dtemp import report
from dtemp.model import Thresholds, Reading, label_slots, make_stats, update_stats

sys.path.insert(0, str(Path(__file__).parent))
from helpers import SAMPLE_PATH, run_cli, synthetic_json  # noqa: E402


def write_fixture(tmp_path, devices, name="fixture.json", storage=None):
    path = tmp_path / name
    path.write_text(json.dumps({"buses": [{"bus": 1, "devices": devices}], "storage": storage or []}))
    return str(path)


# ── JSON contract ────────────────────────────────────────────────────────────


def test_json_is_valid_and_has_stable_keys():
    code, out = run_cli(["--simulate", "--json"])
    rows = json.loads(out)
    assert code == 0 and len(rows) == 6
    assert set(rows[0]) == {"timestamp", "slot", "address", "kind", "temp_c", "status", "source",
                            "error", "min_c", "max_c", "avg_c"}
    assert {r["kind"] for r in rows} == {"memory", "storage"}
    assert all(r["source"].startswith("simulated") for r in rows)


def test_storage_rows_get_their_own_stats():
    _, out = run_cli(["--simulate", "--json"])
    storage = [r for r in json.loads(out) if r["kind"] == "storage"]
    assert all(r["min_c"] == r["temp_c"] for r in storage)  # regression: was None in the old tool


def test_sample_output_file_matches_generator():
    assert json.loads(SAMPLE_PATH.read_text(encoding="utf-8")) == synthetic_json()


def test_sample_output_contains_no_real_identifiers():
    text = SAMPLE_PATH.read_text(encoding="utf-8").lower()
    assert "simulated" in text
    for token in ("/home", "c:\\users", "@", "serial"):
        assert token not in text


# ── validate exit codes (stable contract) ────────────────────────────────────


@pytest.mark.parametrize(
    "devices, expected",
    [
        ([{"addr": 0x50, "temp_c": 30.0}], 0),
        ([{"addr": 0x50, "temp_c": 30.0}, {"addr": 0x51, "temp_c": 50.0}], 1),   # == warn threshold
        ([{"addr": 0x50, "temp_c": 80.0}], 1),
        ([], 2),
        ([{"addr": 0x50, "temp_c": 30.0, "ts_enabled": False}], 3),
        ([{"addr": 0x50, "temp_c": 80.0}, {"addr": 0x51, "ts_enabled": False, "temp_c": 1}], 3),
        ([{"addr": 0x50, "fault": "io"}], 3),
    ],
)
def test_validate_exit_codes(tmp_path, devices, expected):
    fixture = write_fixture(tmp_path, devices)
    code, _ = run_cli(["--simulate", fixture, "--validate", "--no-color"])
    assert code == expected


def test_validate_honours_custom_thresholds(tmp_path):
    fixture = write_fixture(tmp_path, [{"addr": 0x50, "temp_c": 55.0}])
    assert run_cli(["--simulate", fixture, "--validate"])[0] == 1
    assert run_cli(["--simulate", fixture, "--validate", "--warn", "60", "--fail", "80"])[0] == 0


def test_validate_ignores_storage_temperatures(tmp_path):
    fixture = write_fixture(tmp_path, [{"addr": 0x50, "temp_c": 30.0}],
                            storage=[{"name": "nvme0", "temp_c": 99.0}])
    assert run_cli(["--simulate", fixture, "--validate"])[0] == 0


def test_validate_treats_missing_hwmon_reading_as_error():
    assert report.validate_exit_code([{"slot": "a", "temp": None, "error": None}], Thresholds()) == 3


# ── usage errors ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [["--buses", "a,b"], ["--buses", "-1"], ["--buses", ","], ["--warn", "90", "--fail", "80"],
     ["--warn", "nan"], ["--interval", "0"], ["--simulate", "/no/such/fixture.json"], ["--mode", "bogus"],
     ["--not-an-option"]],
)
def test_usage_errors_exit_64(argv, capsys):
    code, _ = run_cli(argv)
    assert code == 64
    assert "dtemp:" in capsys.readouterr().err


def test_bad_fixture_json(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert run_cli(["--simulate", str(bad)])[0] == 64
    assert "not valid JSON" in capsys.readouterr().err


def test_fixture_with_malformed_entry(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"buses": [{"devices": []}]}))
    assert run_cli(["--simulate", str(bad), "--once"])[0] == 64


# ── output modes ─────────────────────────────────────────────────────────────


def test_table_marks_simulated_data_and_warns(capsys):
    code, out = run_cli(["--simulate", "--once", "--no-color"])
    assert code == 0
    assert "SIMULATED DATA" in out and "WARN" in out and "DIMM 0" in out and "nvme0" in out


def test_diag_in_simulation_lists_allowlist_and_scan():
    code, out = run_cli(["--simulate", "--diag", "--no-color"])
    assert code == 0
    assert "SIMULATED" in out and "Read-only allowlist" in out
    assert "i2c-1 0x50" in out and "38.50 C" in out


def test_csv_log_written_with_error_rows(tmp_path):
    fixture = write_fixture(tmp_path, [{"addr": 0x50, "temp_c": 40.0}, {"addr": 0x51, "ts_enabled": False, "temp_c": 1}])
    log = tmp_path / "out.csv"
    code, _ = run_cli(["--simulate", fixture, "--once", "--log", str(log), "--no-color"])
    assert code == 0
    rows = list(csv.reader(log.open(encoding="utf-8")))
    assert rows[0] == report.CSV_COLUMNS
    assert rows[1][3] == "40.0000" and rows[1][4] == "OK"
    assert rows[2][3] == "" and rows[2][4] == "ERROR" and "disabled" in rows[2][7]


def test_unwritable_log_path_is_reported(tmp_path, capsys):
    code, _ = run_cli(["--simulate", "--once", "--log", str(tmp_path / "no" / "dir" / "x.csv"), "--no-color"])
    assert code == 73
    assert "cannot write log" in capsys.readouterr().err


def test_smbus_mode_without_backend_exits_69(monkeypatch, capsys):
    import dtemp.cli as cli

    monkeypatch.setattr(cli, "SMBUS2_AVAILABLE", False)
    monkeypatch.setattr(cli, "i2cget_path", lambda: None)
    code, _ = run_cli(["--mode", "smbus"])
    assert code == 69
    assert "no SMBus read method" in capsys.readouterr().err


def test_setup_without_root_exits_77(monkeypatch, capsys):
    import dtemp.setup as setup_mod

    monkeypatch.setattr(setup_mod, "is_root", lambda: False)
    code, _ = run_cli(["--simulate", "--setup"])
    assert code == 77


def test_setup_dry_run_via_cli_prints_plan(tmp_path):
    code, out = run_cli(["--simulate", "--setup", "--dry-run"])
    assert code == 0 and "would modprobe spd5118" in out and "would write 'spd5118 0x50'" in out


def test_parse_bus_list_dedupes():
    from dtemp.cli import parse_bus_list

    assert parse_bus_list("1, 0,1") == [1, 0]
    assert parse_bus_list(None) is None


# ── model ────────────────────────────────────────────────────────────────────


def test_thresholds_status_boundaries():
    thr = Thresholds()
    assert [thr.status(t) for t in (49.99, 50.0, 71.99, 72.0)] == ["OK", "WARN", "WARN", "HOT"]


def test_thresholds_validation():
    for warn, hot in ((80, 70), (50, 50), (float("inf"), 100)):
        with pytest.raises(ValueError):
            Thresholds(warn, hot)


def test_label_slots_keeps_existing_labels():
    out = label_slots([Reading("", "a", "s"), Reading("DIMM A1", "b", "s"), Reading("", "c", "s")])
    assert [r.slot for r in out] == ["DIMM 0", "DIMM A1", "DIMM 2"]


def test_stats_track_min_max_avg():
    stats = make_stats()
    for t in (40.0, 50.0, 45.0):
        update_stats(stats, [{"slot": "x", "temp": t}])
    update_stats(stats, [{"slot": "x", "temp": None}])
    assert (stats["x"]["min"], stats["x"]["max"], stats["x"]["avg"], stats["x"]["count"]) == (40.0, 50.0, 45.0, 3)


def test_dmi_parse_and_label_never_reads_serials():
    from dtemp import dmi

    text = """Memory Device
\tLocator: DIMM_A1
\tBank Locator: BANK 0
\tSize: 16 GB
\tManufacturer: ACME
\tPart Number: X-5600
\tSerial Number: SHOULD-NOT-APPEAR

Memory Device
\tSize: No Module Installed
\tLocator: DIMM_B1
"""
    devices = dmi.parse_dmidecode_memory(text)
    assert len(devices) == 1 and "SHOULD-NOT-APPEAR" not in str(devices)
    labelled = dmi.apply_labels([Reading("DIMM 0", "i2c-0 0x50", "s"), Reading("DIMM 1", "i2c-0 0x51", "s")], devices)
    assert labelled[0].address == "ACME X-5600 | DIMM_A1 BANK 0"
    assert labelled[1].address == "i2c-0 0x51"


# ── hardening helpers ────────────────────────────────────────────────────────


def test_clean_text_strips_terminal_escapes():
    from dtemp.fsutil import clean_text

    assert clean_text("A\x1b[2Jdimm\x07\x9b") == "A[2Jdimm"


def test_csv_safe_neutralises_formulas():
    from dtemp.fsutil import csv_safe

    assert csv_safe("=1+1") == "'=1+1" and csv_safe("@x") == "'@x" and csv_safe("DIMM") == "DIMM"


def test_log_write_refuses_symlink(tmp_path):
    from dtemp.fsutil import open_for_write

    target = tmp_path / "victim.txt"
    target.write_text("keep")
    link = tmp_path / "log.csv"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(OSError):
        open_for_write(str(link))
    assert target.read_text() == "keep"


def test_log_write_overwrites_regular_file(tmp_path):
    from dtemp.fsutil import write_text_nofollow

    p = tmp_path / "f.txt"
    write_text_nofollow(str(p), "one\n")
    write_text_nofollow(str(p), "two\n")
    assert p.read_text() == "two\n"
