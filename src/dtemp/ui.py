"""Terminal rendering: panels, tables and braille trend charts.

Ported from the original single-file tool. Display settings (thresholds,
refresh interval, history length, colour, banner) live in one immutable
``UiConfig`` that ``configure`` replaces atomically.
"""

from __future__ import annotations

import io
import re
import shutil
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass, field

from dtemp.model import Thresholds

ANSI_CODES = {
    "GREEN": "\033[92m",
    "YELLOW": "\033[93m",
    "RED": "\033[91m",
    "BLUE": "\033[94m",
    "CYAN": "\033[96m",
    "BOLD": "\033[1m",
    "DIM": "\033[2m",
    "RESET": "\033[0m",
}
GREEN = YELLOW = RED = BLUE = CYAN = BOLD = DIM = RESET = ""
ANSI_RE = re.compile(r"\033\[[0-9;]*m")


@dataclass(frozen=True)
class UiConfig:
    thresholds: Thresholds = field(default_factory=Thresholds)
    refresh_secs: float = 2
    history_limit: int = 120
    color: bool = True
    banner: str = ""


_CFG = UiConfig()


def configure(cfg: UiConfig) -> None:
    """Install a new display configuration (also switches colour on/off)."""
    global _CFG, GREEN, YELLOW, RED, BLUE, CYAN, BOLD, DIM, RESET
    _CFG = cfg
    codes = ANSI_CODES if cfg.color else {k: "" for k in ANSI_CODES}
    GREEN, YELLOW, RED, BLUE = codes["GREEN"], codes["YELLOW"], codes["RED"], codes["BLUE"]
    CYAN, BOLD, DIM, RESET = codes["CYAN"], codes["BOLD"], codes["DIM"], codes["RESET"]


def _banner_text():
    return f"  {BOLD}{YELLOW}[{_CFG.banner}]{RESET}" if _CFG.banner else ""


configure(UiConfig())


def temp_color(t):
    if t < _CFG.thresholds.warn: return GREEN
    if t < _CFG.thresholds.hot: return YELLOW
    return RED


def temp_status(t):
    return _CFG.thresholds.status(t)


def fmt_stat(val):
    return f"{val:.2f}C" if val is not None else "---"


def _term_width():
    return max(80, shutil.get_terminal_size((120, 30)).columns)


def _term_height():
    return max(24, shutil.get_terminal_size((120, 30)).lines)


def _visible_len(text):
    return len(ANSI_RE.sub("", text))


def _center_ansi(text, width):
    return " " * max(0, (width - _visible_len(text)) // 2) + text


def _fit(text, width):
    if width <= 0:
        return ""
    if len(text) <= width:
        return text.ljust(width)
    if width == 1:
        return text[:1]
    return text[:width - 1] + "…"


def _refresh_live_screen():
    if not sys.stdout.isatty():
        return
    print("\033[H\033[J", end="", flush=True)


def _history_samples(values, width):
    numeric_samples = [v for v in values if isinstance(v, (int, float))]
    if width <= 0:
        return []
    if not numeric_samples:
        return [None] * width
    if len(numeric_samples) == 1:
        return numeric_samples * width

    samples = []
    max_index = len(numeric_samples) - 1
    for i in range(width):
        pos = (i / max(1, width - 1)) * max_index
        left = int(pos)
        right = min(max_index, left + 1)
        frac = pos - left
        value = numeric_samples[left] * (1 - frac) + numeric_samples[right] * frac
        samples.append(value)
    return samples


def _history_range(values, min_v=None, max_v=None):
    numeric_samples = [v for v in values if isinstance(v, (int, float))]
    if not numeric_samples:
        return (None, None)
    lo = min(numeric_samples) if min_v is None else min_v
    hi = max(numeric_samples) if max_v is None else max_v
    if hi - lo < 0.5:
        hi = lo + 0.5
    return (lo, hi)


def _axis_marks(lo, hi, rows):
    if lo is None or hi is None or rows <= 0:
        return {}
    marks = {0: hi, rows - 1: lo}
    if rows >= 5:
        marks[rows // 2] = (lo + hi) / 2
    return marks


def _braille_char(mask):
    return chr(0x2800 + mask) if mask else " "


def _line_chart(values, width, height=6, min_v=None, max_v=None):
    subcols = max(1, width * 2)
    subrows = max(1, height * 4)
    samples = _history_samples(values, subcols)
    numeric_samples = [v for v in samples if isinstance(v, (int, float))]
    if width <= 0 or height <= 0:
        return []
    if not numeric_samples:
        return [" " * width for _ in range(height)]

    lo, hi = _history_range(numeric_samples, min_v=min_v, max_v=max_v)

    points = []
    for value in samples:
        if value is None:
            points.append(None)
            continue
        scaled = (value - lo) / (hi - lo)
        points.append(subrows - 1 - round(scaled * (subrows - 1)))

    grid = [[0 for _ in range(width)] for _ in range(height)]

    def set_dot(x, y):
        if not (0 <= x < subcols and 0 <= y < subrows):
            return
        cell_x = x // 2
        cell_y = y // 4
        dx = x % 2
        dy = y % 4
        dot_masks = (
            (0x01, 0x08),
            (0x02, 0x10),
            (0x04, 0x20),
            (0x40, 0x80),
        )
        grid[cell_y][cell_x] |= dot_masks[dy][dx]

    prev_y = None
    for x, y in enumerate(points):
        if y is None:
            prev_y = None
            continue
        if prev_y is not None:
            top = min(prev_y, y)
            bottom = max(prev_y, y)
            for row in range(top, bottom + 1):
                set_dot(x, row)
            if x > 0:
                set_dot(x - 1, prev_y)
        set_dot(x, y)
        prev_y = y

    return ["".join(_braille_char(mask) for mask in row) for row in grid]


def _summarize(results):
    temps = [r["temp"] for r in results if r.get("temp") is not None]
    if not temps:
        return {"count": 0, "ok": 0, "warn": 0, "hot": 0, "peak": None, "avg": None}
    ok = sum(1 for t in temps if t < _CFG.thresholds.warn)
    warn = sum(1 for t in temps if _CFG.thresholds.warn <= t < _CFG.thresholds.hot)
    hot = sum(1 for t in temps if t >= _CFG.thresholds.hot)
    return {
        "count": len(temps),
        "ok": ok,
        "warn": warn,
        "hot": hot,
        "peak": max(temps),
        "avg": sum(temps) / len(temps),
    }


def _panel(title, lines, width):
    inner = max(20, width - 4)
    title_text = f" {title} "
    pad_left = max(1, (inner - len(title_text)) // 2)
    pad_right = max(1, inner - len(title_text) - pad_left)
    print(f"{CYAN}┌{'─' * pad_left}{title_text}{'─' * pad_right}┐{RESET}")
    for line in lines:
        visible = _visible_len(line)
        print(f"{CYAN}│{RESET} {line}{' ' * max(0, inner - 1 - visible)}{CYAN}│{RESET}")
    print(f"{CYAN}└{'─' * inner}┘{RESET}")


def _history_values(history, slot):
    return [sample for sample in history.get(slot, []) if isinstance(sample, float)]


def print_header(now_str, elapsed, width):
    h, rem = divmod(int(elapsed), 3600)
    m, sec = divmod(rem, 60)
    title = f"{BOLD}dtemp{RESET}  {DIM}DDR + SSD Thermal Monitor{RESET}{_banner_text()}"
    meta = f"{DIM}{now_str}  |  session {h:02d}:{m:02d}:{sec:02d}  |  refresh {_CFG.refresh_secs:g}s  |  Ctrl+C exits{RESET}"
    print()
    print(_center_ansi(title, width))
    print(_center_ansi(meta, width))
    print()


def print_summary(dimm_results, ssd_results, width):
    mem = _summarize(dimm_results)
    ssd = _summarize(ssd_results)
    left = (
        f"{BOLD}Memory{RESET}: {mem['count']} sensors  "
        f"{GREEN}OK {mem['ok']}{RESET}  "
        f"{YELLOW}WARN {mem['warn']}{RESET}  "
        f"{RED}HOT {mem['hot']}{RESET}  "
        f"{DIM}avg {fmt_stat(mem['avg'])}  peak {fmt_stat(mem['peak'])}{RESET}"
    )
    right = (
        f"{BOLD}Storage{RESET}: {ssd['count']} sensors  "
        f"{GREEN}OK {ssd['ok']}{RESET}  "
        f"{YELLOW}WARN {ssd['warn']}{RESET}  "
        f"{RED}HOT {ssd['hot']}{RESET}  "
        f"{DIM}avg {fmt_stat(ssd['avg'])}  peak {fmt_stat(ssd['peak'])}{RESET}"
    )
    _panel("Overview", [left, right], width)


def print_section(title, results, stats, history, width):
    if not results:
        _panel(title, [f"{DIM}No sensors found.{RESET}"], width)
        return

    inner = max(76, width - 4)
    slot_w = 10
    temp_w = 8
    status_w = 6
    stat_w = 7
    src_w = min(20, max(17, inner // 8))
    addr_w = min(30, max(18, inner - (slot_w + temp_w + status_w + stat_w * 3 + src_w + 14)))

    header = (
        f"{_fit('SENSOR', slot_w)} "
        f"{_fit('ADDRESS', addr_w)} "
        f"{'TEMP':>{temp_w}} "
        f"{_fit('STATE', status_w)} "
        f"{'MIN':>{stat_w}} "
        f"{'MAX':>{stat_w}} "
        f"{'AVG':>{stat_w}} "
        f"{_fit('SOURCE', src_w)}"
    )
    lines = [f"{BOLD}{header}{RESET}"]
    for r in results:
        slot = r.get("slot", "?")
        address = r.get("address", "")
        temp = r.get("temp")
        err = r.get("error")
        src = r.get("source", "")
        s = stats[slot]
        if err:
            lines.append(
                f"{_fit(slot, slot_w)} {_fit(address, addr_w)} "
                f"{RED}{_fit(err, inner - slot_w - addr_w - 3)}{RESET}"
            )
            continue
        if temp is None:
            lines.append(f"{_fit(slot, slot_w)} {_fit(address, addr_w)} {'N/A':>{temp_w}}")
            continue
        color = temp_color(temp)
        lines.append(
            f"{_fit(slot, slot_w)} "
            f"{DIM}{_fit(address, addr_w)}{RESET} "
            f"{color}{BOLD}{temp:>{temp_w - 1}.2f}C{RESET} "
            f"{color}{_fit(temp_status(temp), status_w)}{RESET} "
            f"{DIM}{fmt_stat(s['min']):>{stat_w}} {fmt_stat(s['max']):>{stat_w}} {fmt_stat(s['avg']):>{stat_w}}{RESET} "
            f"{DIM}{_fit(src, src_w)}{RESET}"
        )
    _panel(title, lines, width)


def print_history_panel(title, results, history, width):
    if not results:
        return
    inner = max(72, width - 4)
    slot_w = min(16, max(10, inner // 9))
    temp_w = 8
    chart_h = 6
    axis_w = 7
    graph_w = max(32, inner - slot_w - temp_w - axis_w - 10)
    history_seconds = int(_CFG.history_limit * _CFG.refresh_secs)
    scale_text = "TEMP C"
    time_text = f"TIME WINDOW  0s -> +{history_seconds}s"
    lines = [
        f"{BOLD}{_fit('SENSOR ID', slot_w)} {'CURRENT':>{temp_w}}  {'SCALE':>{axis_w}}  {_fit('TEMPERATURE TREND FIELD', graph_w)}{RESET}",
        f"{DIM}{' ' * (slot_w + temp_w + 4)}{_fit(scale_text, axis_w)}  {_fit(time_text, graph_w)}{RESET}",
        f"{DIM}{' ' * (slot_w + temp_w + axis_w + 6)}0s{' ' * max(1, graph_w - 8)}+{history_seconds}s{RESET}",
        f"{DIM}{' ' * (slot_w + temp_w + axis_w + 6)}{'─' * graph_w}{RESET}",
    ]
    for r in results:
        temp = r.get("temp")
        if temp is None:
            continue
        slot = r.get("slot", "?")
        values = _history_values(history, slot)
        lo = temp - 5.0
        hi = temp + 5.0
        chart_rows = _line_chart(values, graph_w, height=chart_h, min_v=lo, max_v=hi)
        axis_marks = _axis_marks(lo, hi, chart_h)
        trend_color = BLUE
        for idx, graph in enumerate(chart_rows):
            label = _fit(slot, slot_w) if idx == 0 else " " * slot_w
            now = f"{temp:>{temp_w - 1}.2f}C" if idx == 0 else " " * temp_w
            now_text = f"{temp_color(temp)}{BOLD}{now}{RESET}" if idx == 0 else now
            axis_value = axis_marks.get(idx)
            axis_label = f"{axis_value:>{axis_w - 1}.1f}" if axis_value is not None else " " * axis_w
            lines.append(f"{label} {now_text}  {DIM}{axis_label}{RESET}  {trend_color}{graph}{RESET}")
        lines.append(f"{DIM}{' ' * (slot_w + temp_w + axis_w + 6)}{'·' * graph_w}{RESET}")
    _panel(title, lines, width)


def print_compact_section(title, results, stats, width, limit=4):
    shown = [r for r in results if r.get("temp") is not None][:limit]
    if not shown:
        _panel(title, [f"{DIM}No sensors found.{RESET}"], width)
        return

    inner = max(52, width - 4)
    slot_w = min(18, max(10, inner // 5))
    temp_w = 8
    status_w = 6
    stat_w = 7
    addr_w = max(16, inner - (slot_w + temp_w + status_w + stat_w + 12))
    lines = [
        f"{BOLD}{_fit('SENSOR', slot_w)} {_fit('IDENTIFIER', addr_w)} {'TEMP':>{temp_w}} {_fit('STATE', status_w)} {'AVG':>{stat_w}}{RESET}"
    ]
    for r in shown:
        slot = r.get("slot", "?")
        address = r.get("address", "")
        temp = r.get("temp")
        color = temp_color(temp)
        avg = stats[slot]["avg"]
        lines.append(
            f"{_fit(slot, slot_w)} {DIM}{_fit(address, addr_w)}{RESET} "
            f"{color}{BOLD}{temp:>{temp_w - 1}.2f}C{RESET} "
            f"{color}{_fit(temp_status(temp), status_w)}{RESET} "
            f"{DIM}{fmt_stat(avg):>{stat_w}}{RESET}"
        )
    hidden = sum(1 for r in results if r.get("temp") is not None) - len(shown)
    if hidden > 0:
        lines.append(f"{DIM}+ {hidden} additional sensor(s) hidden in live mode{RESET}")
    _panel(title, lines, width)


def print_compact_history(title, results, history, width, limit=2):
    shown = [r for r in results if r.get("temp") is not None][:limit]
    if not shown:
        return

    inner = max(52, width - 4)
    slot_w = min(16, max(10, inner // 6))
    temp_w = 8
    graph_w = max(24, inner - slot_w - temp_w - 5)
    lines = [
        f"{BOLD}{_fit('SENSOR', slot_w)} {'NOW':>{temp_w}}  {_fit('TREND  0s -> +240s', graph_w)}{RESET}"
    ]
    for r in shown:
        slot = r.get("slot", "?")
        temp = r.get("temp")
        values = _history_values(history, slot)
        graph = _line_chart(values, graph_w, height=2, min_v=temp - 5.0, max_v=temp + 5.0)
        lines.append(
            f"{_fit(slot, slot_w)} {temp_color(temp)}{BOLD}{temp:>{temp_w - 1}.2f}C{RESET}  {BLUE}{graph[0]}{RESET}"
        )
        lines.append(
            f"{' ' * slot_w} {' ' * temp_w}  {BLUE}{graph[1]}{RESET}"
        )
    _panel(title, lines, width)


def render_table(dimm_results, dimm_stats, dimm_history, ssd_results, ssd_stats, ssd_history, elapsed, now_str):
    width = _term_width()
    print_header(now_str, elapsed, width)
    print_summary(dimm_results, ssd_results, width)
    if _CFG.thresholds != Thresholds():
        _panel("Thresholds", [f"{DIM}WARN >= {_CFG.thresholds.warn:.1f}C   HOT >= {_CFG.thresholds.hot:.1f}C{RESET}"], width)
    print_section("Memory Sensors", dimm_results, dimm_stats, dimm_history, width)
    print()
    print_history_panel("Memory History", dimm_results, dimm_history, width)
    print()
    print_section("Storage Sensors", ssd_results, ssd_stats, ssd_history, width)
    print()
    print_history_panel("Storage History", ssd_results, ssd_history, width)
    print()


def render_compact_table(dimm_results, dimm_stats, dimm_history, ssd_results, ssd_stats, ssd_history, elapsed, now_str):
    width = _term_width()
    print_header(now_str, elapsed, width)
    print_summary(dimm_results, ssd_results, width)
    if _CFG.thresholds != Thresholds():
        _panel("Thresholds", [f"{DIM}WARN >= {_CFG.thresholds.warn:.1f}C   HOT >= {_CFG.thresholds.hot:.1f}C{RESET}"], width)
    print_compact_section("Memory Sensors", dimm_results, dimm_stats, width, limit=4)
    print()
    print_compact_history("Memory Trend", dimm_results, dimm_history, width, limit=2)
    print()
    print_compact_section("Storage Sensors", ssd_results, ssd_stats, width, limit=3)
    print()
    print_compact_history("Storage Trend", ssd_results, ssd_history, width, limit=2)
    print()


def _live_frame_text(dimm_results, dimm_stats, dimm_history, ssd_results, ssd_stats, ssd_history, elapsed, now_str):
    buf = io.StringIO()
    with redirect_stdout(buf):
        if not dimm_results and not ssd_results:
            width = _term_width()
            lines = [
                f"{DIM}No DIMM or storage sensors were detected.{RESET}",
                f"{DIM}Try: sudo dtemp --setup   (loads drivers; see README){RESET}",
                f"{DIM}Then: sudo dtemp --diag{RESET}",
            ]
            print_header(now_str, elapsed, width)
            _panel("No Sensors", lines, width)
            print()
        else:
            render_compact_table(
                dimm_results, dimm_stats, dimm_history,
                ssd_results, ssd_stats, ssd_history,
                elapsed, now_str,
            )
    return buf.getvalue()


def print_table(dimm_results, dimm_stats, dimm_history, ssd_results, ssd_stats, ssd_history, elapsed, now_str):
    render_table(dimm_results, dimm_stats, dimm_history, ssd_results, ssd_stats, ssd_history, elapsed, now_str)

def refresh_live_screen():
    _refresh_live_screen()


def live_frame_text(*args):
    return _live_frame_text(*args)

