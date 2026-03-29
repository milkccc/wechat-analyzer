"""
Utilities for console I/O compatibility across terminals.
"""

from __future__ import annotations

import os
import sys


def configure_stdio() -> None:
    """
    Reconfigure stdout/stderr on Windows so emoji/log output won't crash under
    legacy non-UTF-8 terminals such as PowerShell with GBK encoding.
    """
    if os.name != "nt":
        return

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue

        encoding = (getattr(stream, "encoding", "") or "").lower()
        if encoding == "utf-8":
            continue

        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass
