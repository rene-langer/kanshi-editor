"""Run tools on the host: directly, or through ``flatpak-spawn --host`` in a Flatpak.

The editor talks to swaymsg, wlr-randr, kanshictl and grim. Inside the Flatpak
sandbox those live on the host, so commands go through the Flatpak portal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import cache
from pathlib import Path

# Environment the host tools need to find the compositor and kanshi.
_FORWARD = ("SWAYSOCK", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR")


def demo_dir() -> Path | None:
    """KANSHI_EDITOR_DEMO=<dir> fakes the host, for screenshots and development.

    The directory may hold ``outputs.json`` (``swaymsg -t get_outputs`` format),
    ``status.txt`` (``kanshictl status`` output) and ``screens/<connector>.png``.
    """
    d = os.environ.get("KANSHI_EDITOR_DEMO")
    return Path(d) if d else None


def in_flatpak() -> bool:
    return os.path.exists("/.flatpak-info")


def argv(cmd: list[str]) -> list[str]:
    if not in_flatpak():
        return cmd
    env = [f"--env={k}={os.environ[k]}" for k in _FORWARD if k in os.environ]
    return ["flatpak-spawn", "--host", *env, *cmd]


@cache
def which(name: str) -> bool:
    if not in_flatpak():
        return shutil.which(name) is not None
    try:
        res = subprocess.run(argv(["sh", "-c", f'command -v "{name}"']), capture_output=True, timeout=5)
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run(cmd: list[str], timeout: float = 10, check: bool = False, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(argv(cmd), capture_output=True, text=text, timeout=timeout, check=check)
