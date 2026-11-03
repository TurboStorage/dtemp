# dtemp

Read DDR5 / DDR4 DIMM temperatures (and NVMe / SATA drive temperatures) on Linux, from the terminal or from scripts.

* Live terminal monitor with per-sensor min / max / avg and a trend chart
* `--json`, `--log` (CSV) and `--validate` (stable exit codes) for automation
* Works through the kernel `spd5118` / `jc42` hwmon drivers, or reads the sensor directly over SMBus (`smbus2` or `i2cget`) when no driver is bound
* **Read-only.** dtemp never writes to a DIMM's SPD NVM or configuration registers (see [Read-only guarantee](#read-only-guarantee))
* `--simulate` mode produces realistic output from synthetic data, so you can try the tool, or test a script that consumes it, without any hardware

Pure Python 3.9+, standard library only. `smbus2` is optional.

## Status: please read

dtemp started as a personal tool and was tested on **one machine**. Everything below the "Confirmed" line is unverified.

| Platform | DIMM path | Status |
|----------|-----------|--------|
| Intel NUC 14 Pro (Meteor Lake), 2x DDR5, Linux 6.17 | hwmon (`spd5118` driver) | **Confirmed** on an earlier build of this tool: temperatures matched expectations |
| Same machine, NVMe drives | hwmon (`nvme`) | **Confirmed** on an earlier build |
| Any machine, direct SMBus read (`smbus2` / `i2cget`) of SPD5118 | direct | **Untested on hardware.** Rewritten from the JEDEC/kernel register map for this release; verified against synthetic data only |
| DDR4 / DDR3 (`jc42`) | hwmon or direct | **Untested on hardware** |
| SATA (`drivetemp`) | hwmon | **Untested** |
| **AMD EPYC, AMD Threadripper / Threadripper Pro, Intel Xeon (server platforms)** | any | **Untested. Not expected to work out of the box** on many boards: see below |

Known reasons server and workstation-class platforms may not work:

* DIMMs are often behind an **I2C multiplexer** (e.g. PCA9548 at 0x70-0x77). dtemp does not drive multiplexers yet, so those DIMMs are invisible to a direct scan.
* Many server platforms keep the SPD hubs in **I3C** mode or reserve the bus for the BMC / platform firmware. dtemp is I2C/SMBus only. If the kernel `spd5118` driver is bound (I2C or I3C), the hwmon path can still work.
* Some firmware (for example Intel ME on some client platforms) blocks host access to parts of the SMBus.

The software has been checked with an automated test-suite (synthetic data) on Python 3.12 (Linux) and 3.14 (Windows). That proves the decode logic and the safety guard, not that any particular motherboard behaves.

### Reporting results

If you try it on hardware, especially server platforms, please open an issue at <https://gitlab.com/TurboStorage/dtemp/-/issues> with:

1. Output of `sudo dtemp --diag --no-color` (safe to share; it prints bus names, driver state and the read-only scan)
2. CPU and motherboard model, DIMM type (DDR4 / DDR5), kernel version (`uname -r`), distro
3. Whether `--mode hwmon` and `--mode smbus` each worked, and the output of `sudo dtemp --once --json`
4. Anything that looked wrong (a temperature you can cross-check against the BIOS or `sensors`)

Please remove hostnames, usernames and serial numbers before posting. dtemp does not print serial numbers.

## Install

```bash
pip install .            # from a checkout; provides the `dtemp` command
pip install '.[smbus]'   # optional: adds smbus2 for direct reads without i2cget
python3 -m dtemp ...     # or run without installing: PYTHONPATH=src python3 -m dtemp
```

Requirements by read path:

| Path | Needs |
|------|-------|
| hwmon (preferred) | kernel 6.8+ for `spd5118` (`jc42` for DDR3/4 since 4.x); no root once the device exists |
| direct SMBus | root (or the `i2c` group), `i2c-dev` loaded, and either `smbus2` or `i2c-tools` (`i2cget`) |

`i2c-dev` is a kernel module, not an apt package. `i2c-tools` provides `i2cget` / `i2cdetect` but not the module.

## Usage

```bash
dtemp --simulate                 # try it: live monitor on synthetic data
dtemp --diag                     # START HERE on a new machine (root for the bus scan)
sudo dtemp --setup               # one-time: load drivers and bind them to identified sensors
dtemp                            # live monitor (hwmon first, direct SMBus fallback)
dtemp --once                     # single snapshot
dtemp --once --json              # machine-readable
dtemp --validate                 # exit code for scripts, see below
dtemp --mode hwmon               # only the kernel-driver path
sudo dtemp --mode smbus          # only direct reads
sudo dtemp --buses 0,1,4         # only these i2c buses
sudo dtemp --all-buses           # also GPU/DDC and other non-SMBus adapters
dtemp --warn 55 --fail 75        # thresholds in C (defaults 50 / 72)
dtemp --log session.csv          # CSV log written on exit
dtemp --dmi-labels               # label DIMMs from dmidecode (best effort, may mislabel)
```

dtemp makes **no system changes unless you run `--setup`**. Earlier builds silently loaded modules, ran `pip install --break-system-packages` and wrote `/etc/modules-load.d` on every root run; that behaviour was removed.

### `--setup`

Root only (`--dry-run` previews without root). It `modprobe`s the I2C host drivers and `spd5118` / `jc42` / `drivetemp`, then, for every address where a **read-only identification** succeeds (SPD5118 type registers read `0x5118`, or a JC42 manufacturer ID the kernel driver knows), it binds the kernel driver through `/sys/bus/i2c/devices/i2c-N/new_device`. Nothing is bound to an address that did not positively identify itself. `--persist` also writes `/etc/modules-load.d/dtemp.conf` (module list only; bound devices do not survive a reboot, re-run `--setup`).

### Validate exit codes (stable)

| Code | Meaning |
|------|---------|
| 0 | all DIMM sensors read OK, all below `--warn` |
| 1 | one or more at WARN or HOT |
| 2 | no DIMM sensors found |
| 3 | read error on one or more (includes a disabled sensor and an unreadable hwmon file) |

Storage temperatures are shown and logged but do not affect `--validate`. Usage errors exit 64, missing backend 69, `--setup` without root 77, unwritable `--log` 73, unexpected internal error 70.

### CSV columns

`timestamp, slot, address, temp_c, status, source, kind, error`. Sensors that failed produce a row with `status=ERROR`.

## Sample output (synthetic data)

`dtemp --simulate --once` (excerpt; the full screen adds trend charts). The data is synthetic and is labelled as such on screen and in the `source` field:

```
                                   dtemp  DDR + SSD Thermal Monitor  [SIMULATED DATA]

┌────────────────────────────────────────────────── Memory Sensors ──────────────────────────────────────────────────┐
│ SENSOR     ADDRESS                            TEMP STATE      MIN     MAX     AVG SOURCE                           │
│ DIMM 0     i2c-1 0x50                       38.50C OK      38.50C  38.50C  38.50C simulated/spd5118                │
│ DIMM 1     i2c-1 0x51                       41.25C OK      41.25C  41.25C  41.25C simulated/spd5118                │
│ DIMM 2     i2c-1 0x52                       52.75C WARN    52.75C  52.75C  52.75C simulated/spd5118                │
│ DIMM 3     i2c-1 0x53                       44.00C OK      44.00C  44.00C  44.00C simulated/spd5118                │
└────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

`dtemp --simulate --json` (excerpt; full file: [`examples/sample-output.json`](examples/sample-output.json)):

```json
[
  {
    "timestamp": "2026-01-01 12:00:00",
    "slot": "DIMM 2",
    "address": "i2c-1 0x52",
    "kind": "memory",
    "temp_c": 52.75,
    "status": "WARN",
    "source": "simulated/spd5118",
    "error": null,
    "min_c": 52.75,
    "max_c": 52.75,
    "avg_c": 52.75
  }
]
```

A screenshot from an earlier build on the confirmed NUC (older layout, real hwmon data) is in [`docs/screenshot-earlier-build.png`](docs/screenshot-earlier-build.png).

### Your own fixtures

`dtemp --simulate my.json` reads a fixture (format documented in `src/dtemp/sim.py`). Register images are built from it and read back through the real decode path and the read-only guard, so you can test edge cases: negative temperatures, a disabled sensor, a device that is not an SPD5118, a busy address, transfer errors.

```json
{"buses": [{"bus": 1, "devices": [
  {"addr": 80, "temp_c": -3.25},
  {"addr": 81, "temp_c": 30.0, "ts_enabled": false},
  {"addr": 82, "raw_word": 4},
  {"addr": 83, "foreign": true},
  {"addr": 24, "temp_c": 41.0625}
]}], "storage": []}
```

## How the sensors are read

**DDR5 (SPD5118 hub).** The temperature sensor lives in the hub and answers at the hub's SPD address, `0x50`-`0x57`. Register numbers below match JEDEC JESD300-5 as implemented by the Linux `spd5118` driver.

| Item | Value |
|------|-------|
| Identify | MR0/MR1 (`0x00`, word) reads `0x51 0x18` |
| Has sensor | MR5 (`0x05`) bit 1 |
| Enabled | MR26 TS_CONFIG (`0x1A`) bit 0 is TS_DISABLE, must be 0 |
| Temperature | MR49:MR50 (`0x31`, little-endian word) |
| Format | bits 12:2 = 11-bit two's complement, **0.25 C per LSB**, -256.00 to +255.75 C; other bits ignored |
| NVM page | MR11 (`0x0B`) bits 2:0; only read, to explain a hub that answers 0 while a non-zero page is selected |

**DDR3/DDR4 (JC42.4, e.g. TSE2004).** Address `0x18`-`0x1F`, big-endian 16-bit registers: capability `0x00`, config `0x01`, temperature `0x05` (13-bit two's complement, 0.0625 C/LSB), manufacturer `0x06`, device `0x07`.

Every reading is identified first, so a device that merely happens to sit at one of these addresses is never mis-decoded. A temperature outside -40..150 C is reported as an error rather than shown.

## Read-only guarantee

* The bus API (`ReadOnlyBus`) has only `read_byte` and `read_word`. Every call is checked against an explicit allowlist (address range and register, per device family) **before** any bus traffic; anything else raises `ReadOnlyViolation`. `dtemp --diag` prints the allowlist.
* The `i2cget` backend builds its command line through the same allowlist and only ever uses `-y <bus> <addr> <reg> b|w`: no `-f`, no data-write form, no `i2cset` / `i2ctransfer`.
* An SMBus/I2C read necessarily transmits the register address to the device before reading. That is part of the read, not a data or configuration write. No `write_*` smbus2 calls and no raw I2C transfers exist in the code. The test-suite scans the sources for them and fails if one appears.
* SPD NVM (`0x80` and up), write-protect registers and configuration registers are not on the allowlist.
* sysfs and file writes exist only in the opt-in `--setup` (`new_device`, optional `/etc/modules-load.d`) and the CSV `--log`.

One thing dtemp cannot control: once the **kernel** `spd5118` driver is bound (by `--setup` or by the distro), the kernel driver behaves as it always does. Notably it toggles the sensor-disable bit around suspend/resume and, for hubs that report a non-zero NVM page, may reset the page pointer during probing. Those are configuration bits, not NVM contents (the driver exposes the SPD EEPROM read-only), but they are writes by the kernel, not by dtemp. If you need a strict guarantee, use `--mode smbus` without `--setup`.

Direct reads still touch shared hardware. On a live system another agent (BMC, firmware, another tool) may be using the same SMBus; dtemp uses only short single-register reads, but bus contention is possible. Scans are limited to SMBus-class adapters by default so that GPU and display buses are not probed.

## Development

```bash
pip install -e '.[test]'
pytest --cov                 # coverage must stay at or above 80%
python tests/helpers.py      # regenerate examples/sample-output.json
```

Layout: `regs.py` (register map, decode, allowlist), `buses.py` (read-only backends), `sensors.py` (identify + read), `hwmon.py`, `setup.py`, `sim.py`, `report.py`, `ui.py`, `cli.py`.

## License

MIT, see [LICENSE](LICENSE).
