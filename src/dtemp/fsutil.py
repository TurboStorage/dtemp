"""Small filesystem/text safety helpers (dtemp often runs as root)."""

from __future__ import annotations

import os
import re
from typing import IO

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_FILE_MODE = 0o644


def clean_text(text: str) -> str:
    """Drop control characters (terminal escape injection) from hardware-supplied strings."""
    return _CONTROL_CHARS.sub("", text)


def csv_safe(text: str) -> str:
    """Neutralise spreadsheet formula injection in free-text CSV cells."""
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def open_for_write(path: str, newline: str = "") -> IO[str]:
    """Open a text file for writing without following a symlink at ``path``.

    Prevents a planted symlink from redirecting a root-owned write.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, _FILE_MODE)
    return os.fdopen(fd, "w", newline=newline, encoding="utf-8")


def write_text_nofollow(path: str, text: str) -> None:
    with open_for_write(path, newline="\n") as handle:
        handle.write(text)
