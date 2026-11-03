"""Command line entry point."""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional, Sequence

from dtemp import __version__, dmi, hwmon, report, sim, ui
from dtemp.buses import (
    SMBUS2_AVAILABLE,
    i2cget_path,
    open_real_bus,
    probe_smbus_buses,
)
from dtemp.diag import run_diag
from dtemp.model import (
    DEFAULT_HOT_C,
    DEFAULT_WARN_C,
    KIND_MEMORY,
    KIND_STORAGE,
    Reading,
    Thresholds,
    label_slots,
    make_stats,
    update_stats,
)
from dtemp.sensors import scan_buses
from dtemp.setup import SetupError, run_setup

HISTORY_LIMIT = 120
DEFAULT_INTERVAL_S = 2.0
MIN_INTERVAL_S = 0.2
BUILTIN_FIXTURE = ""  # argparse const: --simulate with no file


class UsageError(Exception):
    """Bad option value; reported with exit code 64."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # exit 64 so usage errors never look like validate results
        self.print_usage(sys.stderr)
        self.exit(report.EXIT_USAGE, f"dtemp: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="dtemp",
        description="Read-only DDR DIMM (and SSD) temperature reader. Never writes to DIMM SPD NVM.",
    )
    p.add_argument("--mode", choices=["auto", "hwmon", "smbus"], default="auto",
                   help="read path: auto (hwmon first, direct SMBus fallback), hwmon only, or smbus only")
    p.add_argument("--buses", metavar="N,N", help="SMBus IDs to scan, e.g. --buses 0,1,4")
    p.add_argument("--all-buses", action="store_true",
                   help="scan every i2c bus, not only SMBus-class adapters (GPU/DDC buses included)")
    p.add_argument("--once", action="store_true", help="print once and exit")
    p.add_argument("--validate", action="store_true",
                   help="exit 0=all OK, 1=WARN/HOT, 2=no sensors, 3=read errors (DIMM sensors only)")
    p.add_argument("--json", action="store_true", dest="json_out", help="JSON output (implies --once)")
    p.add_argument("--diag", action="store_true", help="show read methods, modules, buses and a read-only scan")
    p.add_argument("--warn", type=float, default=DEFAULT_WARN_C, metavar="C", help="WARN threshold (default 50)")
    p.add_argument("--fail", "--hot", type=float, default=DEFAULT_HOT_C, metavar="C", dest="hot",
                   help="HOT threshold (default 72)")
    p.add_argument("--log", metavar="FILE", help="write a CSV log on exit")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S, metavar="S",
                   help="live refresh seconds (default 2)")
    p.add_argument("--simulate", nargs="?", const=BUILTIN_FIXTURE, metavar="FIXTURE.json",
                   help="use synthetic data (built-in, or a fixture file); no hardware is touched")
    p.add_argument("--dmi-labels", action="store_true",
                   help="label DIMMs from dmidecode (best effort, order based, may mislabel)")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    p.add_argument("--setup", action="store_true",
                   help="(root) modprobe drivers and bind spd5118/jc42 to identified sensors, then exit")
    p.add_argument("--persist", action="store_true", help="with --setup: also write /etc/modules-load.d/dtemp.conf")
    p.add_argument("--dry-run", action="store_true", help="with --setup: show what would be done")
    p.add_argument("--version", action="version", version=f"dtemp {__version__}")
    return p


def parse_bus_list(text: Optional[str]) -> Optional[List[int]]:
    if text is None:
        return None
    try:
        ids = [int(part.strip()) for part in text.split(",") if part.strip()]
    except ValueError:
        raise UsageError(f"invalid --buses value {text!r}; use integers like 0,1,4") from None
    if not ids or any(i < 0 for i in ids):
        raise UsageError(f"invalid --buses value {text!r}; use integers like 0,1,4")
    return list(dict.fromkeys(ids))


class Source:
    """Where readings come from: real hardware or a simulated fixture."""

    def __init__(self, args, bus_ids: Optional[List[int]], fixture: Optional[dict]):
        self.args = args
        self.fixture = fixture
        self.notes: List[str] = []
        if fixture is not None:
            self.opener, self.sim_bus_ids, self._sim_storage = sim.open_sim(fixture)
            self.bus_ids = bus_ids if bus_ids is not None else self.sim_bus_ids
            self.direct_available = True
        else:
            self.direct_available = SMBUS2_AVAILABLE or bool(i2cget_path())
            self.opener = lambda bus_id: open_real_bus(bus_id)
            self.bus_ids = bus_ids
            self._sim_storage = []

    def resolved_bus_ids(self) -> List[int]:
        if self.bus_ids is not None:
            return self.bus_ids
        return probe_smbus_buses(include_all=self.args.all_buses)

    def memory(self) -> List[Reading]:
        mode = self.args.mode
        if self.fixture is None and mode in ("auto", "hwmon"):
            found = hwmon.read_memory()
            if found or mode == "hwmon":
                return label_slots(found)
        if not self.direct_available:
            self.notes.append("no direct SMBus read method (install i2c-tools or smbus2)")
            return []
        found, notes = scan_buses(self.opener, self.resolved_bus_ids(), "smbus" if self.fixture is None else "simulated")
        self.notes = notes
        return label_slots(found)

    def storage(self) -> List[Reading]:
        return self._sim_storage if self.fixture is not None else hwmon.read_storage()


def _emit_notes(source: Source) -> None:
    for note in dict.fromkeys(source.notes):
        print(f"dtemp: note: {note}", file=sys.stderr)


def _maybe_dmi(args, memory: List[Reading]) -> List[Reading]:
    if not args.dmi_labels or args.simulate is not None:
        return memory
    return dmi.apply_labels(memory, dmi.read_dmidecode())


def _main(args) -> int:
    try:
        thr = Thresholds(args.warn, args.hot)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc
    if args.interval < MIN_INTERVAL_S:
        raise UsageError(f"--interval must be at least {MIN_INTERVAL_S}")
    bus_ids = parse_bus_list(args.buses)
    fixture = sim.load_fixture(args.simulate) if args.simulate is not None else None
    color = not args.no_color and sys.stdout.isatty() and "NO_COLOR" not in os.environ
    ui.configure(ui.UiConfig(thresholds=thr, refresh_secs=args.interval, history_limit=HISTORY_LIMIT,
                             color=color, banner="SIMULATED DATA" if fixture is not None else ""))
    source = Source(args, bus_ids, fixture)

    if args.setup:
        return _do_setup(args, source, bus_ids)
    if args.diag:
        run_diag(opener=source.opener if source.direct_available else None, bus_ids=source.bus_ids,
                 include_all_buses=args.all_buses, simulated=fixture is not None)
        return report.EXIT_OK
    if args.mode == "smbus" and not source.direct_available:
        print("dtemp: no SMBus read method available; install i2c-tools (i2cget) or pip install smbus2",
              file=sys.stderr)
        return report.EXIT_UNAVAILABLE
    return _monitor(args, source, thr)


def _do_setup(args, source: Source, bus_ids) -> int:
    try:
        actions = run_setup(source.opener, bus_ids=bus_ids if bus_ids is not None else source.bus_ids,
                            persist=args.persist, dry_run=args.dry_run)
    except SetupError as exc:
        print(f"dtemp: {exc}", file=sys.stderr)
        return report.EXIT_NO_PERMISSION
    for action in actions:
        print(f"  {action}")
    return report.EXIT_OK


def _monitor(args, source: Source, thr: Thresholds) -> int:
    once = args.once or args.json_out or args.validate
    mem_stats, sto_stats = make_stats(), make_stats()
    stats_by_kind: Dict[str, dict] = {KIND_MEMORY: mem_stats, KIND_STORAGE: sto_stats}
    mem_hist = defaultdict(lambda: deque(maxlen=HISTORY_LIMIT))
    sto_hist = defaultdict(lambda: deque(maxlen=HISTORY_LIMIT))
    log_rows: List[List[str]] = []
    memory_results: List[dict] = []
    start = time.monotonic()
    notes_shown = False

    try:
        while True:
            memory = _maybe_dmi(args, source.memory())
            storage = source.storage()
            memory_results = [r.as_result() for r in memory]
            storage_results = [r.as_result() for r in storage]
            if not notes_shown:
                _emit_notes(source)
                notes_shown = True
            update_stats(mem_stats, memory_results)
            update_stats(sto_stats, storage_results)
            for res in memory_results:
                mem_hist[res["slot"]].append(res.get("temp"))
            for res in storage_results:
                sto_hist[res["slot"]].append(res.get("temp"))
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            elapsed = time.monotonic() - start

            if args.log:
                log_rows.extend(report.csv_row(now_str, res, thr) for res in memory_results + storage_results)
            if args.json_out:
                print(report.dumps_json(report.build_json(memory_results + storage_results, stats_by_kind, now_str, thr)))
            elif once:
                ui.print_table(memory_results, mem_stats, mem_hist, storage_results, sto_stats, sto_hist, elapsed, now_str)
            else:
                ui.refresh_live_screen()
                print(ui.live_frame_text(memory_results, mem_stats, mem_hist, storage_results, sto_stats,
                                         sto_hist, elapsed, now_str), end="")
            if once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n  {ui.DIM}Interrupted.{ui.RESET}")

    log_status = _write_log(args, log_rows)
    if args.validate:
        return report.validate_exit_code(memory_results, thr)
    return log_status


def _write_log(args, rows: List[List[str]]) -> int:
    if not args.log:
        return report.EXIT_OK
    if not rows:
        print("dtemp: no data collected; log not written", file=sys.stderr)
        return report.EXIT_OK
    try:
        report.write_csv(args.log, rows)
    except OSError as exc:
        print(f"dtemp: cannot write log {args.log!r}: {exc}", file=sys.stderr)
        return report.EXIT_CANT_CREATE
    print(f"\n  Log saved: {args.log}\n")
    return report.EXIT_OK


def _make_stdout_safe() -> None:
    """Never crash on a terminal that cannot show box-drawing characters."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _make_stdout_safe()
    try:
        return _main(args)
    except (UsageError, sim.FixtureError) as exc:
        print(f"dtemp: error: {exc}", file=sys.stderr)
        return report.EXIT_USAGE
    except BrokenPipeError:
        return report.EXIT_OK
    except Exception as exc:  # last resort: never a raw traceback unless DTEMP_DEBUG is set
        if os.environ.get("DTEMP_DEBUG"):
            raise
        print(f"dtemp: unexpected error: {exc!r} (set DTEMP_DEBUG=1 for a traceback)", file=sys.stderr)
        return report.EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
