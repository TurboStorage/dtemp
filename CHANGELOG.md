# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.2.0] - unreleased (staged for first public release)

### Fixed
- **Direct SPD5118 read used the wrong registers, address range and format.** It read 0x12/0x13 at 0x18-0x1F and decoded 12 bits at 0.0625 C. The temperature is MR49:MR50 (0x31, little-endian word), 11-bit two's complement in bits 12:2 at 0.25 C, on the hub address 0x50-0x57. 0x13 is the driver's temperature-clear register.
- A hwmon read that failed produced a blank row and `--validate` still exited 0. It is now a read error (exit 3).
- `--json` storage rows looked up their min/max/avg in the memory statistics (always null); statistics are now per kind.
- Direct reads decoded any responding device at 0x18-0x1F as SPD5118 then JC42 and kept whichever looked plausible. Devices are now identified first (SPD5118 type registers, JC42 manufacturer IDs as in the Linux drivers); anything else is ignored.
- Out-of-range readings were silently dropped; they are now reported as errors.
- Transfer errors, EBUSY (address owned by a kernel driver) and "no device" were all treated as "not present". They are now distinguished.
- The TS_DISABLE / shutdown state is honoured (reported, never changed).
- Default bus scan no longer probes GPU/display adapters (`AMDGPU`-style names matched the old "amd" keyword). Use `--all-buses` to include them.

### Added
- Read-only guard: allowlist of addresses and registers enforced before any bus traffic; `i2cget` command line built through the same allowlist; static test that fails on any write-capable call.
- `--simulate [FIXTURE.json]`: synthetic sensors through the real decode path; `SIMULATED DATA` banner and `simulated` source.
- `--setup` (`--persist`, `--dry-run`): opt-in module loading and kernel-driver binding, gated by read-only identification.
- JC42 (DDR3/DDR4) identification and decoding on the direct path.
- `--interval`, `--no-color`, `--all-buses`, `--dmi-labels`, `--version`, `--hot` alias for `--fail`.
- `kind` field in JSON and CSV, `error` column in CSV, error rows in CSV.
- Package layout with `dtemp` console script, `python -m dtemp`, pytest suite (synthetic data).
- Stable non-validate exit codes: 64 usage, 69 backend unavailable, 70 internal, 73 log not writable, 77 no permission.
- Terminal control characters stripped from hardware-supplied strings; CSV formula-injection guard; `--log` refuses to follow symlinks.

### Changed
- No system changes on a normal run. Removed: automatic `modprobe` on every root run, silent `pip install smbus2 --break-system-packages`, writes to `/etc/modules-load.d`, and speculative `new_device` writes at 0x18-0x1F for the `spd5118` driver. These now happen only via `--setup`.
- DMI labels (`dmidecode`) are opt-in (`--dmi-labels`): matching by enumeration order can mislabel modules. Serial numbers are never parsed.
- Layout split from a single file into a package (standard library only; `smbus2` optional).
- Thresholds are validated (`--warn` must be lower than `--fail`) and passed explicitly instead of module globals.

### Removed
- Speculative WSL / LibreHardwareMonitor, RAM/disk usage and SMART experiments from the newer working copies are not part of this release.

## [0.1.0] - 2026 (private)
- Single-file tool: hwmon read path (`spd5118`, `jc42`), NVMe / SATA hwmon temperatures, live monitor, `--once`, `--json`, `--validate`, `--diag`, `--log`. Confirmed on one Intel NUC (hwmon path).
