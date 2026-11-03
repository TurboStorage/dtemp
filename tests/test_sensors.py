from dtemp import regs, sim
from dtemp.buses import SimBus
from dtemp.regs import AddressBusy, BusError, BusPermissionError, BusUnavailable, DeviceAbsent
from dtemp.sensors import read_jc42, read_spd5118, scan_bus, scan_buses


def bus_from(*specs):
    return SimBus({s["addr"]: sim.build_device(s) for s in specs})


def test_reads_spd5118_temperature_through_full_decode_path():
    bus = bus_from({"addr": 0x50, "temp_c": 38.5}, {"addr": 0x52, "temp_c": -3.25})
    readings, notes = scan_bus(bus, 1, "smbus")
    assert [(r.address, r.temp_c) for r in readings] == [("i2c-1 0x50", 38.5), ("i2c-1 0x52", -3.25)]
    assert all(r.source == "smbus/spd5118" for r in readings)
    assert notes == []


def test_reads_jc42_temperature():
    bus = bus_from({"addr": 0x18, "temp_c": 41.0625})
    readings, _ = scan_bus(bus, 0, "smbus")
    assert len(readings) == 1 and readings[0].temp_c == 41.0625
    assert readings[0].source == "smbus/jc42"


def test_foreign_device_at_spd_address_is_ignored():
    bus = bus_from({"addr": 0x50, "foreign": True})
    assert scan_bus(bus, 1, "smbus") == ([], [])


def test_sensor_disabled_is_reported_not_enabled():
    bus = bus_from({"addr": 0x50, "temp_c": 30.0, "ts_enabled": False})
    (reading,), _ = scan_bus(bus, 1, "smbus")
    assert reading.temp_c is None and "disabled" in reading.error


def test_hub_without_temperature_sensor_is_reported():
    (reading,), _ = scan_bus(bus_from({"addr": 0x50, "no_ts": True}), 1, "smbus")
    assert reading.temp_c is None and "no temperature sensor" in reading.error


def test_jc42_shutdown_mode_is_reported():
    (reading,), _ = scan_bus(bus_from({"addr": 0x19, "temp_c": 30.0, "ts_enabled": False}), 1, "smbus")
    assert reading.temp_c is None and "shutdown" in reading.error


def test_implausible_value_becomes_error_not_a_reading():
    (reading,), _ = scan_bus(bus_from({"addr": 0x50, "temp_c": 200.0}), 1, "smbus")
    assert reading.temp_c is None and "implausible" in reading.error


def test_busy_address_is_a_note_not_an_error():
    readings, notes = scan_bus(bus_from({"addr": 0x50, "fault": "busy"}), 2, "smbus")
    assert readings == []
    assert "claimed by a kernel driver" in notes[0]


def test_transfer_errors_collapse_to_one_bus_level_error():
    specs = [{"addr": a, "fault": "io"} for a in (0x50, 0x51, 0x52)]
    readings, _ = scan_bus(bus_from(*specs), 4, "smbus")
    assert len(readings) == 1
    assert readings[0].slot == "i2c-4" and "3 transfer error(s)" in readings[0].error


def test_one_good_sensor_hides_noise_from_other_io_faults():
    readings, _ = scan_bus(bus_from({"addr": 0x50, "temp_c": 40.0}, {"addr": 0x51, "fault": "io"}), 1, "smbus")
    assert [r.temp_c for r in readings] == [40.0]


def test_hub_with_nonzero_page_is_reported_not_repaired():
    device = sim.build_device({"addr": 0x50, "temp_c": 30.0})
    device["regs"][regs.SPD5118_REG_TYPE] = 0
    device["regs"][regs.SPD5118_REG_TYPE + 1] = 0
    device["regs"][regs.SPD5118_REG_LEGACY_MODE] = 0x03
    (reading,), _ = scan_bus(SimBus({0x50: device}), 1, "smbus")
    assert "NVM page 3" in reading.error and "read-only" in reading.error


def test_zero_type_with_page_zero_is_not_a_hub():
    device = sim.build_device({"addr": 0x50, "temp_c": 30.0})
    device["regs"][regs.SPD5118_REG_TYPE] = 0
    device["regs"][regs.SPD5118_REG_TYPE + 1] = 0
    assert scan_bus(SimBus({0x50: device}), 1, "smbus") == ([], [])


def test_read_functions_return_none_for_wrong_device():
    bus = bus_from({"addr": 0x50, "foreign": True}, {"addr": 0x18, "foreign": True})
    assert read_spd5118(bus, 1, 0x50, "s") is None
    assert read_jc42(bus, 1, 0x18, "s") is None


class DenyingBus(SimBus):
    def __init__(self, exc):
        super().__init__({})
        self._exc = exc

    def _read_word(self, addr, reg):
        raise self._exc


def test_permission_error_stops_bus_scan_with_single_error():
    readings, _ = scan_bus(DenyingBus(BusPermissionError("permission denied (run as root)")), 0, "smbus")
    assert len(readings) == 1 and "permission denied" in readings[0].error


def test_scan_buses_turns_open_failures_into_error_readings():
    def opener(bus_id):
        raise BusUnavailable("bus device node missing")

    readings, _ = scan_buses(opener, [7, 9], "smbus")
    assert [r.slot for r in readings] == ["i2c-7", "i2c-9"]
    assert all("missing" in r.error for r in readings)


def test_scan_buses_closes_every_bus():
    closed = []

    class B(SimBus):
        def close(self):
            closed.append(True)

    scan_buses(lambda n: B({}), [1, 2], "smbus")
    assert len(closed) == 2


def test_absent_exception_type_is_distinct_from_error():
    assert not issubclass(DeviceAbsent, BusError) and not issubclass(AddressBusy, BusError)
