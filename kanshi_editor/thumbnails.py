"""Small live pictures of what each connected screen shows, taken with grim.

They make it obvious which rectangle is which physical screen, even with two
identical monitors on one desk. Images are kept in memory only.
"""

from __future__ import annotations

import io
from collections.abc import Callable

import cairo
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import host  # noqa: E402
from .monitors import Monitor  # noqa: E402

TARGET_WIDTH = 320  # pixels; plenty for a canvas tile


class Thumbnails:
    def __init__(self, on_update: Callable[[], None]):
        self.on_update = on_update
        self.surfaces: dict[str, cairo.ImageSurface] = {}  # by connector
        self._pending: set[str] = set()

    @staticmethod
    def available() -> bool:
        return host.demo_dir() is not None or host.which("grim")

    def clear(self) -> None:
        self.surfaces.clear()

    def capture(self, monitors: list[Monitor]) -> None:
        """Start capturing the given monitors; on_update runs as each one arrives."""
        live = {m.connector for m in monitors if m.active}
        for gone in set(self.surfaces) - live:
            del self.surfaces[gone]
        demo = host.demo_dir()
        if demo is not None:
            for m in monitors:
                try:
                    self.surfaces[m.connector] = cairo.ImageSurface.create_from_png(str(demo / "screens" / f"{m.connector}.png"))
                except (cairo.Error, OSError):
                    pass
            self.on_update()
            return
        for m in monitors:
            if not m.active or not m.connector or m.connector in self._pending:
                continue
            mode = m.current or m.best_mode
            scale = min(max(TARGET_WIDTH / mode.width, 0.02), 1.0) if mode else 0.1
            cmd = host.argv(["grim", "-s", f"{scale:.3f}", "-o", m.connector, "-"])
            try:
                proc = Gio.Subprocess.new(cmd, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
            except GLib.Error:
                continue
            self._pending.add(m.connector)
            proc.communicate_async(None, None, self._done, m.connector)

    def _done(self, proc: Gio.Subprocess, result: Gio.AsyncResult, connector: str) -> None:
        self._pending.discard(connector)
        try:
            _ok, out, _err = proc.communicate_finish(result)
        except GLib.Error:
            return
        if not proc.get_successful() or out is None:
            return
        try:
            surface = cairo.ImageSurface.create_from_png(io.BytesIO(out.get_data()))
        except (cairo.Error, MemoryError, OSError):
            return
        self.surfaces[connector] = surface
        self.on_update()
