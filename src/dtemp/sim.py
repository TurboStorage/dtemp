"""Simulation mode: synthetic sensors that go through the real decode path.

A fixture is JSON::

    {
      "buses":   [{"bus": 1, "devices": [
                     {"addr": 80, "temp_c": 38.5},          # SPD5118 (0x50-0x57)
                     {"addr": 24, "temp_c": 41.0},          # JC42.4  (0x18-0x1F)
                     {"addr": 82, "temp_c": 30.0, "ts_enabled": false},
                     {"addr": 83, "raw_word": 4},           # exact register word
                     {"addr": 84, "foreign": true},         # some other device
                     {"addr": 85, "fault": "busy"}          # or "io"
                 ]}],
      "storage": [{"name": "nvme0", "model": "EXAMPLE NVMe", "temp_c": 44.85}]
    }

Register images are built here and read back through ``SimBus``, so decoding,
identification and the read-only guard are exercised exactly as on hardware.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Dict, List, Optional

from dtemp import regs
from dtemp.buses import SimBus
from dtemp.model import KIND_STORAGE, Reading
from dtemp.regs import BusUnavailable

SIM_SOURCE = "simulated"
_JC42_CAP = 0x0077
_JC42_MANID = 0x00B3
_JC42_DEVID = 0x2200


class FixtureError(Exception):
    pass


def load_default_fixture() -> dict:
    text = resources.files("dtemp").joinpath("data/simulate_default.json").read_text(encoding="utf-8")
    return json.loads(text)


def load_fixture(path: Optional[str]) -> dict:
    """path=None or '' loads the built-in fixture."""
    if not path:
        return load_default_fixture()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise FixtureError(f"cannot read fixture {path!r}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise FixtureError(f"fixture {path!r} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FixtureError("fixture must be a JSON object")
    return data


def _spd5118_image(spec: dict) -> dict:
    word = spec["raw_word"] if "raw_word" in spec else regs.encode_spd5118_word(float(spec.get("temp_c", 0.0)))
    return {
        "regs": {
            regs.SPD5118_REG_TYPE: 0x51,
            regs.SPD5118_REG_TYPE + 1: 0x18,
            regs.SPD5118_REG_CAPABILITY: 0x00 if spec.get("no_ts") else regs.SPD5118_CAP_TS_SUPPORT,
            regs.SPD5118_REG_LEGACY_MODE: 0x00,
            regs.SPD5118_REG_TS_CONFIG: 0x00 if spec.get("ts_enabled", True) else regs.SPD5118_TS_DISABLE,
            regs.SPD5118_REG_TEMP: word & 0xFF,
            regs.SPD5118_REG_TEMP + 1: (word >> 8) & 0xFF,
        }
    }


def _jc42_image(spec: dict) -> dict:
    reg = spec["raw_word"] if "raw_word" in spec else regs.encode_jc42_word(float(spec.get("temp_c", 0.0)))
    config = 0x0000 if spec.get("ts_enabled", True) else 0x0100
    words = {
        regs.JC42_REG_CAP: regs.swap16(_JC42_CAP),
        regs.JC42_REG_CONFIG: regs.swap16(config),
        regs.JC42_REG_TEMP: regs.swap16(reg),
        regs.JC42_REG_MANID: regs.swap16(_JC42_MANID),
        regs.JC42_REG_DEVICEID: regs.swap16(_JC42_DEVID),
    }
    return {"regs": {"words": words}}


def build_device(spec: dict) -> dict:
    if spec.get("fault"):
        return {"fault": spec["fault"], "regs": {}}
    if spec.get("foreign"):
        return {"regs": {0x00: 0x23, 0x01: 0x10}}  # e.g. DDR4-style SPD bytes
    addr = spec["addr"]
    if addr in regs.JC42_ADDRESSES:
        return _jc42_image(spec)
    return _spd5118_image(spec)


def build_buses(fixture: dict) -> Dict[int, SimBus]:
    buses: Dict[int, SimBus] = {}
    for entry in fixture.get("buses", []):
        try:
            devices = {int(d["addr"]): build_device(d) for d in entry.get("devices", [])}
            buses[int(entry["bus"])] = SimBus(devices)
        except (KeyError, TypeError, ValueError) as exc:
            raise FixtureError(f"bad bus entry in fixture: {exc!r}") from exc
    return buses


def storage_readings(fixture: dict) -> List[Reading]:
    readings = []
    for i, spec in enumerate(fixture.get("storage", [])):
        try:
            readings.append(
                Reading(
                    slot=str(spec.get("name", f"disk{i}")),
                    address=str(spec.get("model", spec.get("name", f"disk{i}")))[:22],
                    source=SIM_SOURCE,
                    kind=KIND_STORAGE,
                    temp_c=float(spec["temp_c"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FixtureError(f"bad storage entry in fixture: {exc!r}") from exc
    return readings


def simulated_bus_ids(fixture: dict) -> List[int]:
    return sorted(build_buses(fixture))


def open_sim(fixture: dict):
    """Return (opener, bus_ids, storage_readings) for a fixture."""
    buses = build_buses(fixture)

    def opener(bus_id: int) -> SimBus:
        if bus_id not in buses:
            raise BusUnavailable("bus not present in fixture")
        return buses[bus_id]

    return opener, sorted(buses), storage_readings(fixture)
