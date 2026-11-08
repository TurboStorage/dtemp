"""Small filesystem/text safety helpers (dtemp often runs as root)."""

from __future__ import annotations

import errno
import os
import re
import stat
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
    # No O_TRUNC at open: truncating before the ownership check would already have
    # clobbered a file the invoking user does not own (hard link, FIFO, device).
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags, _FILE_MODE)
    try:
        st = os.fstat(fd)
        allowed = {os.geteuid(), os.getuid()}
        if os.environ.get("SUDO_UID", "").isdigit():
            allowed.add(int(os.environ["SUDO_UID"]))
        if not stat.S_ISREG(st.st_mode) or st.st_uid not in allowed:
            raise PermissionError(errno.EPERM, "refusing to write: not a regular file owned by the invoking user", path)
        os.ftruncate(fd, 0)
        return os.fdopen(fd, "w", newline=newline, encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise


def write_text_nofollow(path: str, text: str) -> None:
    with open_for_write(path, newline="\n") as handle:
        handle.write(text)
