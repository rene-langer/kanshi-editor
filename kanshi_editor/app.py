"""Application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from .window import Window, default_config_path  # noqa: E402

APP_ID = "io.github.rene_langer.KanshiEditor"
HOMEPAGE = "https://github.com/rene-langer/kanshi-editor"

CSS = """
.status-pill {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: smaller;
  font-weight: bold;
  color: @success_color;
  background-color: alpha(@success_color, 0.15);
}
.status-pill.expected {
  color: @accent_color;
  background-color: alpha(@accent_color, 0.15);
}
"""


class App(Adw.Application):
    def __init__(self):
        self.errors = 0
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.NON_UNIQUE)
        self.add_main_option("version", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Show the version and exit", None)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._about)
        self.add_action(about)
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *_: self.get_active_window() and self.get_active_window().close())
        self.add_action(quit_)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS) if hasattr(provider, "load_from_string") else provider.load_from_data(CSS, -1)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_handle_local_options(self, options):
        if options.contains("version"):
            print(f"kanshi-editor {__version__}")
            return 0
        return -1

    def do_activate(self):
        self._present(Window(self, default_config_path()))

    def do_open(self, files, n_files, hint):
        for f in files:
            self._present(Window(self, Path(f.get_path())))

    def _present(self, win: Window) -> None:
        win.present()
        # Debug/docs hook: render the window to a PNG and quit.
        shot = os.environ.get("KANSHI_EDITOR_SCREENSHOT")
        if shot:
            # Any exception, even one inside a GTK callback, makes the run fail (for CI).
            previous = sys.excepthook

            def record(*exc):
                self.errors += 1
                previous(*exc)

            sys.excepthook = record
            select = os.environ.get("KANSHI_EDITOR_SELECT")  # "profile:output" indices
            if select:
                p, _, o = select.partition(":")
                win.profile_index = int(p)
                win.output_index = int(o) if o else None
                win.refresh()
            GLib.timeout_add(1500, self._screenshot, win, shot)

    def _screenshot(self, win: Window, path: str) -> bool:
        paintable = Gtk.WidgetPaintable.new(win)
        w, h = win.get_width(), win.get_height()
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, w, h)
        node = snap.to_node()
        if node is not None:
            texture = win.get_native().get_renderer().render_texture(node, None)
            texture.save_to_png(path)
        win.disk_text = win.current  # never prompt about unsaved changes here
        self.quit()
        return False

    def _about(self, *_):
        dialog = Adw.AboutDialog(
            application_name="Kanshi Editor",
            application_icon=APP_ID,
            developer_name="Rene Langer and contributors",
            version=__version__,
            comments="Arrange kanshi display profiles visually. Comments and formatting in the file are kept.",
            website=HOMEPAGE,
            issue_url=f"{HOMEPAGE}/issues",
            license_type=Gtk.License.MIT_X11,
        )
        dialog.present(self.get_active_window())


def main() -> int:
    app = App()
    status = app.run(sys.argv)
    return status or (1 if app.errors else 0)
