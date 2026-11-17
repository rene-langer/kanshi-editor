"""Which monitors exist: the connected ones, and every one seen before.

Connected outputs come from ``swaymsg -t get_outputs`` (or ``wlr-randr --json`` on
other wlroots compositors). Each monitor seen is remembered in
``$XDG_STATE_HOME/kanshi-editor/monitors.json``, so profiles for a desk you are
not sitting at still know the monitors' sizes and modes.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import host
from .config import Mode


@dataclass
class Monitor:
    make: str
    model: str
    serial: str
    connector: str = ""  # last seen as, e.g. "DP-3"
    modes: list[Mode] = field(default_factory=list)
    preferred: Mode | None = None
    current: Mode | None = None
    connected: bool = False
    active: bool = False  # connected and turned on right now

    @property
    def description(self) -> str:
        """The "Make Model Serial" string kanshi matches on."""
        return " ".join(p or "Unknown" for p in (self.make, self.model, self.serial))

    @property
    def is_internal(self) -> bool:
        return self.connector.startswith(("eDP", "LVDS", "DSI"))

    @property
    def best_mode(self) -> Mode | None:
        if self.preferred:
            return self.preferred
        if self.current:
            return self.current
        if self.modes:
            return max(self.modes, key=lambda m: (m.width * m.height, m.refresh or 0))
        return None

    def to_json(self) -> dict:
        d = asdict(self)
        del d["connected"]
        del d["active"]
        return d

    @classmethod
    def from_json(cls, d: dict) -> Monitor:
        def mode(m):
            return Mode(m["width"], m["height"], m.get("refresh")) if m else None

        return cls(
            make=d.get("make", ""),
            model=d.get("model", ""),
            serial=d.get("serial", ""),
            connector=d.get("connector", ""),
            modes=[mode(m) for m in d.get("modes", [])],
            preferred=mode(d.get("preferred")),
            current=mode(d.get("current")),
        )


def _unique_modes(modes: list[Mode]) -> list[Mode]:
    seen, out = set(), []
    for m in sorted(modes, key=lambda m: (-m.width * m.height, -m.width, -(m.refresh or 0))):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _round_hz(hz: float | None) -> float | None:
    return None if hz is None else round(hz, 3)


def parse_sway_outputs(data: list[dict]) -> list[Monitor]:
    out = []
    for o in data:
        if o.get("type") not in (None, "output"):
            continue
        modes = [Mode(m["width"], m["height"], _round_hz(m["refresh"] / 1000)) for m in o.get("modes", [])]
        cur = o.get("current_mode")
        current = Mode(cur["width"], cur["height"], _round_hz(cur["refresh"] / 1000)) if cur else None
        # sway does not say which mode is preferred; best_mode falls back to current.
        out.append(
            Monitor(
                make=o.get("make", ""),
                model=o.get("model", ""),
                serial=o.get("serial", ""),
                connector=o.get("name", ""),
                modes=_unique_modes(modes),
                current=current,
                connected=True,
                active=bool(o.get("active", cur is not None)),
            )
        )
    return out


def parse_wlr_randr(data: list[dict]) -> list[Monitor]:
    out = []
    for o in data:
        modes = [Mode(m["width"], m["height"], _round_hz(m.get("refresh"))) for m in o.get("modes", [])]
        pref = next((m for m, raw in zip(modes, o.get("modes", []), strict=True) if raw.get("preferred")), None)
        cur = next((m for m, raw in zip(modes, o.get("modes", []), strict=True) if raw.get("current")), None)
        out.append(
            Monitor(
                make=o.get("make") or "",
                model=o.get("model") or "",
                serial=o.get("serial") or "",
                connector=o.get("name", ""),
                modes=_unique_modes(modes),
                preferred=pref,
                current=cur,
                connected=True,
                active=bool(o.get("enabled", True)),
            )
        )
    return out


def detect_connected() -> tuple[list[Monitor], str | None]:
    """Return (monitors, error). Error is a human-readable reason when detection failed."""
    demo = host.demo_dir()
    if demo is not None:
        try:
            return parse_sway_outputs(json.loads((demo / "outputs.json").read_text())), None
        except (OSError, ValueError, KeyError) as e:
            return [], f"Demo outputs could not be read: {e}"
    attempts = []
    if os.environ.get("SWAYSOCK") and host.which("swaymsg"):
        attempts.append((["swaymsg", "-r", "-t", "get_outputs"], parse_sway_outputs))
    if host.which("wlr-randr"):
        attempts.append((["wlr-randr", "--json"], parse_wlr_randr))
    if not attempts:
        return [], "Neither sway (SWAYSOCK) nor wlr-randr is available, so connected monitors are unknown."
    errors = []
    for cmd, parser in attempts:
        try:
            res = host.run(cmd, timeout=5, check=True)
            return parser(json.loads(res.stdout)), None
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as e:
            errors.append(f"{cmd[0]}: {e}")
    return [], "Could not list monitors (" + "; ".join(errors) + ")."


def state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "kanshi-editor" / "monitors.json"


class MonitorDB:
    """Remembered monitors keyed by description, merged with the connected ones."""

    def __init__(self, path: Path | None = None):
        self.path = path or state_path()
        self.monitors: dict[str, Monitor] = {}
        self.error: str | None = None

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            for d in data.get("monitors", []):
                m = Monitor.from_json(d)
                self.monitors[m.description] = m
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            pass  # a corrupt cache is not worth bothering anyone about; it is rebuilt

    def merge_connected(self, connected: list[Monitor]) -> None:
        for m in self.monitors.values():
            m.connected = m.active = False
        for m in connected:
            self.monitors[m.description] = m

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"monitors": [m.to_json() for m in self.monitors.values()]}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2) + "\n")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def refresh(self) -> None:
        """Re-read the cache and the connected monitors; write the cache only if it changed."""
        self.load()
        before = self._snapshot()
        connected, self.error = detect_connected()
        self.merge_connected(connected)
        if connected and self._snapshot() != before:
            self.save()

    def _snapshot(self) -> str:
        return json.dumps([m.to_json() for m in self.monitors.values()], sort_keys=True)

    def sorted(self) -> list[Monitor]:
        return sorted(self.monitors.values(), key=lambda m: (not m.connected, not m.is_internal, m.description))

    def find(self, criteria: str) -> Monitor | None:
        """The monitor a criteria refers to, preferring connected ones."""
        if criteria == "*" or criteria.startswith("$"):
            return None
        candidates = self.sorted()
        for m in candidates:
            if m.description == criteria:
                return m
        for m in candidates:
            if m.connector == criteria:
                return m
        if any(c in criteria for c in "*?["):
            for m in candidates:
                if fnmatch.fnmatchcase(m.description, criteria):
                    return m
        return None
