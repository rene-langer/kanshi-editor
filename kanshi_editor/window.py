"""Main window: profile list, arrangement canvas and the properties panel."""

from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import host, settings  # noqa: E402
from .canvas import Canvas  # noqa: E402
from .config import TRANSFORMS, Document, Exec, Mode, Output, ParseError, Profile, parse  # noqa: E402
from .layout import Placed, assign_connected, issues, matches_connected, place, predicted_profile, resolve  # noqa: E402
from .monitors import MonitorDB  # noqa: E402
from .thumbnails import Thumbnails  # noqa: E402

POLL_SECONDS = 3
_STATUS = re.compile(r"^Current profile:\s*(.+?)\s*$", re.MULTILINE)

TRANSFORM_LABELS = {
    "normal": "None",
    "90": "90° counter-clockwise",
    "180": "180°",
    "270": "90° clockwise (270)",
    "flipped": "Flipped",
    "flipped-90": "Flipped, 90° counter-clockwise",
    "flipped-180": "Flipped, 180°",
    "flipped-270": "Flipped, 90° clockwise",
}


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "kanshi" / "config"


def display_path(path: Path) -> str:
    home = os.path.expanduser("~")
    text = str(path)
    return "~" + text[len(home) :] if text == home or text.startswith(home + os.sep) else text


def fmt_scale(v: float) -> str:
    return f"{round(v, 4):g}"


class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, path: Path):
        super().__init__(application=app, title="Kanshi Editor")
        self.set_default_size(1280, 800)
        self.path = path
        self.db = MonitorDB()
        self.doc: Document = Document()
        self.disk_text: str | None = None  # file content as last loaded or saved
        self.current: str = ""  # last committed render, the top of the undo history
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        self.profile_index = 0
        self.output_index: int | None = None
        self.placed: list[Placed] = []
        self.kanshi_running = False
        self.kanshi_current: str | None = None  # from `kanshictl status`
        self.prefs = settings.load()
        self._loading = False  # ignore widget signals while filling widgets

        self._build()
        self.thumbs = Thumbnails(self.canvas.queue_draw)
        self._actions()
        self.connect("close-request", self._close_request)
        self.db.refresh()
        self.load()
        if self.prefs["show_thumbnails"] and Thumbnails.available():
            self.thumbs.capture(self._connected())
        self._poll_id = GLib.timeout_add_seconds(POLL_SECONDS, self._poll)
        self.connect("destroy", lambda *_: GLib.source_remove(self._poll_id))
        self.connect("notify::is-active", lambda *_: self.is_active() and self._poll())

    # ================================================================== building

    def _build(self) -> None:
        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        self.split = Adw.OverlaySplitView()
        self.split.set_min_sidebar_width(220)
        self.split.set_max_sidebar_width(300)
        self.toasts.set_child(self.split)

        # ---- sidebar: profiles
        side = Adw.ToolbarView()
        side_header = Adw.HeaderBar()
        side_header.set_title_widget(Adw.WindowTitle(title="Profiles"))
        add = Gtk.MenuButton(icon_name="list-add-symbolic", tooltip_text="Add profile")
        menu = Gio.Menu()
        menu.append("From connected displays", "win.new-profile-connected")
        menu.append("Empty profile", "win.new-profile-empty")
        add.set_menu_model(menu)
        side_header.pack_start(add)
        side.add_top_bar(side_header)

        self.profile_list = Gtk.ListBox()
        self.profile_list.add_css_class("navigation-sidebar")
        self.profile_list.connect("row-selected", self._profile_selected)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.profile_list)
        side.set_content(scroller)

        bar = Gtk.Box(spacing=4, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        for icon, action, tip in [
            ("go-up-symbolic", "win.profile-up", "Move up (kanshi uses the first profile that matches)"),
            ("go-down-symbolic", "win.profile-down", "Move down"),
            ("edit-copy-symbolic", "win.profile-duplicate", "Duplicate"),
        ]:
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, action_name=action)
            b.add_css_class("flat")
            bar.append(b)
        bar.append(Gtk.Box(hexpand=True))
        delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete profile", action_name="win.profile-delete")
        delete.add_css_class("flat")
        bar.append(delete)
        side.add_bottom_bar(bar)
        self.split.set_sidebar(side)

        # ---- content
        content = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="Kanshi Editor", subtitle=display_path(self.path))
        header.set_title_widget(self.title)
        toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Show profiles")
        self.split.bind_property("show-sidebar", toggle, "active", 3)  # sync-create | bidirectional
        header.pack_start(toggle)
        undo = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Undo (Ctrl+Z)", action_name="win.undo")
        redo = Gtk.Button(icon_name="edit-redo-symbolic", tooltip_text="Redo (Ctrl+Shift+Z)", action_name="win.redo")
        header.pack_start(undo)
        header.pack_start(redo)

        main_menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Show Changes", "win.show-diff")
        section.append("Reload From Disk", "win.reload")
        section.append("Rescan Displays", "win.rescan")
        section.append("Show Screen Contents", "win.show-thumbnails")
        main_menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Open in Text Editor", "win.open-external")
        section.append("About Kanshi Editor", "app.about")
        main_menu.append_section(None, section)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=main_menu, tooltip_text="Main menu")
        header.pack_end(menu_button)
        self.save_button = Gtk.Button(label="Save", action_name="win.save", tooltip_text="Save and reload kanshi (Ctrl+S)")
        self.save_button.add_css_class("suggested-action")
        header.pack_end(self.save_button)
        content.add_top_bar(header)

        self.stack = Gtk.Stack()
        content.set_content(self.stack)
        self.split.set_content(content)

        self.error_page = Adw.StatusPage(icon_name="dialog-error-symbolic", title="Can't read the configuration")
        err_buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        for label, action in [("Open in Text Editor", "win.open-external"), ("Try Again", "win.reload")]:
            b = Gtk.Button(label=label, action_name=action)
            b.add_css_class("pill")
            err_buttons.append(b)
        self.error_page.set_child(err_buttons)
        self.stack.add_named(self.error_page, "error")

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)
        paned.set_resize_end_child(False)
        self.stack.add_named(paned, "editor")

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay = Gtk.Overlay()
        self.canvas = Canvas(self._canvas_select, self._canvas_move, self._canvas_move_done, self.remove_output)
        self.canvas.set_size_request(420, 320)
        overlay.set_child(self.canvas)

        tools = Gtk.Box(spacing=6, halign=Gtk.Align.START, valign=Gtk.Align.START, margin_top=12, margin_start=12)
        self.add_output_button = Gtk.MenuButton(tooltip_text="Add a display to this profile")
        self.add_output_button.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Add Display"))
        self.add_output_button.set_always_show_arrow(True)
        tools.append(self.add_output_button)
        overlay.add_overlay(tools)

        self.status_pill = Gtk.Label(halign=Gtk.Align.END, valign=Gtk.Align.START, margin_top=16, margin_end=12)
        self.status_pill.add_css_class("status-pill")
        self.status_pill.set_visible(False)
        overlay.add_overlay(self.status_pill)

        hint = Gtk.Label(
            label="Drag to arrange · Shift: no snapping · Arrow keys nudge (Ctrl ×10, Shift ×100)",
            halign=Gtk.Align.END,
            valign=Gtk.Align.END,
            margin_bottom=8,
            margin_end=12,
        )
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        overlay.add_overlay(hint)
        left.append(overlay)

        self.issue_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.issue_box.add_css_class("boxed-list")
        self.issue_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        issue_wrap = Gtk.Box(margin_start=12, margin_end=12, margin_bottom=12)
        issue_wrap.append(self.issue_box)
        self.issue_box.set_hexpand(True)
        self.issue_revealer.set_child(issue_wrap)
        left.append(self.issue_revealer)
        paned.set_start_child(left)

        self.panel = self._build_panel()
        self.panel.set_size_request(380, -1)
        paned.set_end_child(self.panel)
        paned.set_position(860)

    def _build_panel(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        # ---- profile
        g = Adw.PreferencesGroup(title="Profile")
        self.name_row = Adw.EntryRow(title="Name", show_apply_button=True)
        self.name_row.connect("apply", self._name_applied)
        g.add(self.name_row)
        self.match_row = Adw.ActionRow(title="Connected displays")
        self.match_row.add_css_class("property")
        self.switch_button = Gtk.Button(label="Switch Now", valign=Gtk.Align.CENTER, action_name="win.switch-profile")
        self.switch_button.set_tooltip_text("Ask kanshi to apply this profile (save first)")
        self.match_row.add_suffix(self.switch_button)
        g.add(self.match_row)
        page.add(g)

        # ---- output
        self.output_group = Adw.PreferencesGroup(title="Display")
        g = self.output_group
        self.criteria_row = Adw.EntryRow(title="Matches", show_apply_button=True)
        self.criteria_row.set_tooltip_text(
            '"Make Model Serial", a connector like eDP-1, a glob like "Dell Inc. *", "*" or an alias like $desk'
        )
        self.criteria_row.connect("apply", self._criteria_applied)
        self.criteria_pick = Gtk.MenuButton(
            icon_name="view-more-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Pick a known display"
        )
        self.criteria_pick.add_css_class("flat")
        self.criteria_row.add_suffix(self.criteria_pick)
        g.add(self.criteria_row)

        self.enabled_row = Adw.SwitchRow(title="Turned on")
        self.enabled_row.connect("notify::active", self._enabled_changed)
        g.add(self.enabled_row)

        self.mode_combo = Adw.ComboRow(title="Resolution")
        self.mode_combo.connect("notify::selected", self._mode_selected)
        self._mode_values: list[str | None] = []
        g.add(self.mode_combo)
        self.mode_entry = Adw.EntryRow(title="Resolution (e.g. 1920x1080@60Hz)", show_apply_button=True)
        self.mode_entry.connect("apply", self._mode_applied)
        g.add(self.mode_entry)

        self.scale_row = Adw.SpinRow.new_with_range(0.25, 4, 0.05)
        self.scale_row.set_title("Scale")
        self.scale_row.set_digits(2)
        self.scale_row.connect("notify::value", self._scale_changed)
        g.add(self.scale_row)

        self.transform_combo = Adw.ComboRow(title="Rotation", model=Gtk.StringList.new([TRANSFORM_LABELS[t] for t in TRANSFORMS]))
        self.transform_combo.connect("notify::selected", self._transform_selected)
        g.add(self.transform_combo)

        self.x_row = Adw.SpinRow.new_with_range(-32768, 32768, 1)
        self.x_row.set_title("Position X")
        self.x_row.connect("notify::value", self._position_changed)
        g.add(self.x_row)
        self.y_row = Adw.SpinRow.new_with_range(-32768, 32768, 1)
        self.y_row.set_title("Position Y")
        self.y_row.connect("notify::value", self._position_changed)
        g.add(self.y_row)

        self.vrr_combo = Adw.ComboRow(title="Adaptive sync", model=Gtk.StringList.new(["Not set", "On", "Off"]))
        self.vrr_combo.connect("notify::selected", self._vrr_selected)
        g.add(self.vrr_combo)

        if hasattr(Adw, "ButtonRow"):  # libadwaita 1.6+
            remove = Adw.ButtonRow(title="Remove From Profile", action_name="win.remove-output")
            remove.add_css_class("destructive-action")
            g.add(remove)
        else:
            remove = Gtk.Button(label="Remove From Profile", action_name="win.remove-output", margin_top=12)
            remove.add_css_class("destructive-action")
            g.add(remove)
        page.add(g)

        self.no_output = Adw.PreferencesGroup(title="Display", description="Select a display on the left to change it.")
        page.add(self.no_output)

        # ---- exec
        self.exec_group = Adw.PreferencesGroup(
            title="Commands",
            description="Run after kanshi applies this profile.",
        )
        self._exec_rows: list[Gtk.Widget] = []
        page.add(self.exec_group)
        return page

    def _actions(self) -> None:
        def act(name, cb, accels=None, param=None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            a.connect("activate", lambda _a, p: cb(p.unpack() if p is not None else None))
            self.add_action(a)
            if accels:
                self.get_application().set_accels_for_action(f"win.{name}", accels)
            return a

        self.a_save = act("save", lambda _: self.save(), ["<Control>s"])
        self.a_undo = act("undo", lambda _: self.undo(), ["<Control>z"])
        self.a_redo = act("redo", lambda _: self.redo(), ["<Control><Shift>z", "<Control>y"])
        act("reload", lambda _: self._confirm_discard(self.load))
        act("rescan", lambda _: self.rescan())
        act("show-diff", lambda _: self.show_diff())
        act("open-external", lambda _: self.open_external())
        thumbs = Gio.SimpleAction.new_stateful("show-thumbnails", None, GLib.Variant("b", self.prefs["show_thumbnails"]))
        thumbs.connect("change-state", self._toggle_thumbnails)
        self.add_action(thumbs)
        act("new-profile-connected", lambda _: self.new_profile(from_connected=True))
        act("new-profile-empty", lambda _: self.new_profile(from_connected=False))
        self.a_up = act("profile-up", lambda _: self.move_profile(-1))
        self.a_down = act("profile-down", lambda _: self.move_profile(+1))
        self.a_dup = act("profile-duplicate", lambda _: self.duplicate_profile())
        self.a_del = act("profile-delete", lambda _: self.delete_profile())
        self.a_switch = act("switch-profile", lambda _: self.switch_profile())
        act("add-output", self.add_output, param="s")
        act("set-criteria", self.set_criteria, param="s")
        self.a_remove_output = act("remove-output", lambda _: self.remove_output())

    # ================================================================== model access

    @property
    def profile(self) -> Profile | None:
        profiles = self.doc.profiles
        return profiles[self.profile_index] if 0 <= self.profile_index < len(profiles) else None

    @property
    def output(self) -> Output | None:
        p = self.profile
        if p is None or self.output_index is None:
            return None
        outs = p.outputs
        return outs[self.output_index] if 0 <= self.output_index < len(outs) else None

    def select_output(self, out: Output | None) -> None:
        p = self.profile
        self.output_index = p.outputs.index(out) if (p and out in p.outputs) else None

    @property
    def dirty(self) -> bool:
        return self.disk_text is None or self.current != self.disk_text

    # ================================================================== load / save

    def load(self) -> None:
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            text = ""
        except OSError as e:
            self._show_error(str(e))
            return
        try:
            doc = parse(text)
        except ParseError as e:
            self._show_error(f"{self.path}: {e}\n\nFix it in a text editor, then try again.")
            return
        self.doc = doc
        self.disk_text = text
        self.current = text
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.profile_index = min(self.profile_index, max(len(doc.profiles) - 1, 0))
        self.output_index = None
        self._query_active()
        self.stack.set_visible_child_name("editor")
        self.refresh()

    def _show_error(self, message: str) -> None:
        self.error_page.set_description(GLib.markup_escape_text(message))
        self.stack.set_visible_child_name("error")
        self.profile_list.remove_all()
        self.a_save.set_enabled(False)

    def save(self) -> None:
        text = self.doc.render()
        try:
            check = parse(text).render()
        except ParseError as e:  # should never happen; refuse to write a broken file
            self.toast(f"Not saved: the result would not parse ({e}).")
            return
        if check != text:
            self.toast("Not saved: internal round-trip check failed.")
            return
        real = Path(os.path.realpath(self.path))
        try:
            on_disk = real.read_text()
        except FileNotFoundError:
            on_disk = ""
        except OSError as e:
            self.toast(f"Not saved: {e}")
            return
        if self.disk_text is not None and on_disk != self.disk_text:
            dialog = Adw.AlertDialog(
                heading="File changed on disk",
                body=f"{real} was changed by another program since it was opened. Overwrite those changes?",
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("diff", "Reload From Disk")
            dialog.add_response("overwrite", "Overwrite")
            dialog.set_response_appearance("overwrite", Adw.ResponseAppearance.DESTRUCTIVE)

            def done(d, res):
                r = d.choose_finish(res)
                if r == "overwrite":
                    self._write(real, text)
                elif r == "diff":
                    self.load()

            dialog.choose(self, None, done)
            return
        self._write(real, text)

    def _write(self, real: Path, text: str) -> None:
        # Write through symlinks (e.g. a dotfiles repo), atomically, keeping permissions.
        try:
            real.parent.mkdir(parents=True, exist_ok=True)
            tmp = real.with_name(f".{real.name}.kanshi-editor.tmp")
            tmp.write_text(text)
            if real.exists():
                shutil.copymode(real, tmp)
            os.replace(tmp, real)
        except OSError as e:
            self.toast(f"Not saved: {e}")
            return
        self.disk_text = text
        self.refresh_title()
        self._reload_kanshi()

    def _reload_kanshi(self) -> None:
        if host.demo_dir() is not None:
            self.toast("Saved. Demo mode, so kanshi was not reloaded.")
            return
        if not host.which("kanshictl"):
            self.toast("Saved. kanshictl not found, so kanshi was not reloaded.")
            return
        try:
            res = host.run(["kanshictl", "reload"])
        except (OSError, subprocess.SubprocessError) as e:
            self.toast(f"Saved, but reloading kanshi failed: {e}")
            return
        if res.returncode == 0:
            self.toast("Saved and reloaded kanshi.")
        else:
            msg = (res.stderr or res.stdout).strip().splitlines()
            self.toast("Saved, but kanshi did not reload: " + (msg[0] if msg else f"exit {res.returncode}"))
        GLib.timeout_add(800, self._after_reload)

    def _after_reload(self) -> bool:
        self.db.refresh()
        self._query_active()
        self.refresh()
        return False

    def _query_active(self) -> None:
        """Ask kanshi which profile it applied ("Current profile: NAME")."""
        self.kanshi_running = False
        self.kanshi_current = None
        demo = host.demo_dir()
        if demo is not None:
            try:
                out = (demo / "status.txt").read_text()
            except OSError:
                return
        else:
            if not host.which("kanshictl"):
                return
            try:
                res = host.run(["kanshictl", "status"], timeout=3)
            except (OSError, subprocess.SubprocessError):
                return
            if res.returncode != 0:
                return
            out = res.stdout
        self.kanshi_running = True
        m = _STATUS.search(out)
        if m and m.group(1) not in ("(null)", ""):
            self.kanshi_current = m.group(1)

    def active(self) -> tuple[Profile | None, str]:
        """The profile in use and how we know: "current" (kanshi said so) or "expected".

        Without a name from kanshi (it isn't running, or the active profile has no
        name), the answer is the profile kanshi picks: the first one that matches.
        """
        if self.kanshi_current:
            p = next((q for q in self.doc.profiles if q.name == self.kanshi_current), None)
            if p is not None:
                return p, "current"
        return predicted_profile(self.doc, self._connected()), "expected"

    def _status_key(self):
        mons = tuple((m.description, m.connector, m.active) for m in self._connected())
        return mons, self.kanshi_running, self.kanshi_current

    def _poll(self) -> bool:
        """Every few seconds while focused: notice hotplug, profile changes and refresh pictures."""
        if not self.is_active() or self.canvas._drag or self.stack.get_visible_child_name() != "editor":
            return GLib.SOURCE_CONTINUE
        before = self._status_key()
        self.db.refresh()
        self._query_active()
        if self._status_key() != before:
            self.refresh_status()
        if self.prefs["show_thumbnails"] and Thumbnails.available():
            self.thumbs.capture(self._connected())
        return GLib.SOURCE_CONTINUE

    def _toggle_thumbnails(self, action, value) -> None:
        action.set_state(value)
        self.prefs["show_thumbnails"] = value.get_boolean()
        settings.save(self.prefs)
        if not self.prefs["show_thumbnails"]:
            self.thumbs.clear()
        elif not Thumbnails.available():
            self.toast("Install grim to see screen contents.")
        else:
            self.thumbs.capture(self._connected())
        self.canvas.queue_draw()

    def refresh_status(self) -> None:
        """Update what depends on connected displays and kanshi, but leave the editing panel alone."""
        self._loading = True
        try:
            self._refresh_profiles()
            self._refresh_layout()
            self._refresh_match()
            self._refresh_add_menu()
        finally:
            self._loading = False

    def switch_profile(self) -> None:
        p = self.profile
        if not p or not p.name:
            return
        if self.dirty:
            self.toast("Save first: kanshi only knows the saved profiles.")
            return
        try:
            res = host.run(["kanshictl", "switch", p.name])
        except (OSError, subprocess.SubprocessError) as e:
            self.toast(f"Could not switch: {e}")
            return
        if res.returncode == 0:
            self.toast(f"Switched to {p.name}.")
            GLib.timeout_add(800, self._after_reload)
        else:
            msg = (res.stderr or res.stdout).strip().splitlines()
            self.toast(f"kanshi did not switch to {p.name}: " + (msg[-1] if msg else f"exit {res.returncode}"))

    def rescan(self) -> None:
        self.db.refresh()
        self.refresh()
        n = sum(1 for m in self.db.monitors.values() if m.connected)
        self.toast(self.db.error or f"Found {n} connected display{'s' if n != 1 else ''}.")

    def open_external(self) -> None:
        real = Path(os.path.realpath(self.path))
        Gio.AppInfo.launch_default_for_uri(real.as_uri(), None)

    # ================================================================== history

    def changed(self) -> None:
        """Call after every edit to the document."""
        text = self.doc.render()
        if text != self.current:
            self.undo_stack.append(self.current)
            self.redo_stack.clear()
            self.current = text
        self.refresh()

    def _restore(self, text: str) -> None:
        self.doc = parse(text)
        self.current = text
        self.profile_index = min(self.profile_index, max(len(self.doc.profiles) - 1, 0))
        p = self.profile
        if p is None or self.output_index is None or self.output_index >= len(p.outputs):
            self.output_index = None
        self.refresh()

    def undo(self) -> None:
        if self.undo_stack:
            self.redo_stack.append(self.current)
            self._restore(self.undo_stack.pop())

    def redo(self) -> None:
        if self.redo_stack:
            self.undo_stack.append(self.current)
            self._restore(self.redo_stack.pop())

    # ================================================================== refresh

    def refresh(self) -> None:
        self._loading = True
        try:
            self._refresh_profiles()
            self._refresh_layout()
            self._refresh_panel()
            self._refresh_add_menu()
            self.refresh_title()
        finally:
            self._loading = False

    def refresh_title(self) -> None:
        self.title.set_title(("• " if self.dirty else "") + "Kanshi Editor")
        self.a_save.set_enabled(self.dirty and self.stack.get_visible_child_name() == "editor")
        self.a_undo.set_enabled(bool(self.undo_stack))
        self.a_redo.set_enabled(bool(self.redo_stack))

    def _connected(self):
        return [m for m in self.db.sorted() if m.connected]

    def _status_text(self, kind: str) -> str:
        if kind == "current":
            return "kanshi is using this profile right now"
        if not self.kanshi_running:
            return "kanshi isn't running; this is the first profile that matches the connected displays"
        return "The first profile that matches the connected displays"

    def _refresh_profiles(self) -> None:
        self.profile_list.remove_all()
        connected = self._connected()
        active, kind = self.active()
        for p in self.doc.profiles:
            row = Adw.ActionRow(title=GLib.markup_escape_text(p.name or "(unnamed)"))
            n = len(p.outputs)
            sub = f"{n} display{'s' if n != 1 else ''}"
            if matches_connected(self.doc, p, connected):
                sub += " · matches now"
            row.set_subtitle(sub)
            if p is active:
                pill = Gtk.Label(label="Active" if kind == "current" else "Expected", valign=Gtk.Align.CENTER)
                pill.add_css_class("status-pill")
                if kind != "current":
                    pill.add_css_class("expected")
                pill.set_tooltip_text(self._status_text(kind))
                row.add_suffix(pill)
            self.profile_list.append(row)
        row = self.profile_list.get_row_at_index(self.profile_index)
        if row:
            self.profile_list.select_row(row)
        n = len(self.doc.profiles)
        has = self.profile is not None
        self.a_up.set_enabled(has and self.profile_index > 0)
        self.a_down.set_enabled(has and self.profile_index < n - 1)
        self.a_dup.set_enabled(has)
        self.a_del.set_enabled(has)

    def _refresh_layout(self) -> None:
        p = self.profile
        if p is None:
            self.placed = []
            self.canvas.set_layout([], None, set())
            self._set_issues([])
            self.status_pill.set_visible(False)
            return
        self.placed = place(self.doc, p, self.db)
        found = issues(self.placed, p)
        problems = {id(o) for o, msg in found if o is not None and "overlaps" in msg}
        assigned = assign_connected(self.doc, p, self._connected()) or {}
        self.canvas.thumbs = self.thumbs.surfaces if self.prefs["show_thumbnails"] else {}
        self.canvas.set_layout(self.placed, self.output, problems, assigned)
        self._set_issues([m for _, m in found])
        active, kind = self.active()
        self.status_pill.set_visible(p is active)
        if p is active:
            self.status_pill.set_label("● Active profile" if kind == "current" else "● Expected profile")
            self.status_pill.set_tooltip_text(self._status_text(kind))
            if kind == "current":
                self.status_pill.remove_css_class("expected")
            else:
                self.status_pill.add_css_class("expected")

    def _set_issues(self, messages: list[str]) -> None:
        self.issue_box.remove_all()
        for m in messages:
            row = Adw.ActionRow(title=GLib.markup_escape_text(m))
            row.set_title_lines(3)
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
            self.issue_box.append(row)
        self.issue_revealer.set_reveal_child(bool(messages))

    def _placed_for(self, out: Output | None) -> Placed | None:
        return next((q for q in self.placed if q.output is out), None)

    def _refresh_match(self) -> None:
        p = self.profile
        connected = self._connected()
        active, kind = self.active()
        if p is None:
            self.match_row.set_subtitle("No profile selected")
        elif not connected:
            self.match_row.set_subtitle(self.db.error or "None detected")
        elif p is active and kind == "current":
            self.match_row.set_subtitle("Active: kanshi is using this profile")
        elif matches_connected(self.doc, p, connected):
            if p is active:
                self.match_row.set_subtitle("Matches, and is the first profile that does")
            else:
                self.match_row.set_subtitle(f"Matches, but kanshi picks {active.name or 'an earlier profile'} first")
        else:
            self.match_row.set_subtitle("Doesn't match: " + ", ".join(m.model or m.connector for m in connected))
        self.a_switch.set_enabled(bool(p and p.name) and not (p is active and kind == "current"))

    def _refresh_panel(self) -> None:
        p = self.profile
        self.name_row.set_sensitive(p is not None)
        self.name_row.set_text(p.name or "" if p else "")
        self._refresh_match()

        out = self.output
        pl = self._placed_for(out)
        self.output_group.set_visible(out is not None)
        self.no_output.set_visible(out is None and p is not None)
        self.a_remove_output.set_enabled(out is not None)
        if out is not None and pl is not None:
            self._fill_output(out, pl)

        # exec rows
        for r in self._exec_rows:
            self.exec_group.remove(r)
        self._exec_rows = []
        self.exec_group.set_visible(p is not None)
        if p:
            for e in p.execs:
                row = Adw.EntryRow(title="Command", text=e.command, show_apply_button=True)
                row.connect("apply", self._exec_applied, e)
                rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove command")
                rm.add_css_class("flat")
                rm.connect("clicked", self._exec_removed, e)
                row.add_suffix(rm)
                self.exec_group.add(row)
                self._exec_rows.append(row)
            new = Adw.EntryRow(title="Add a command, e.g. swaymsg workspace 1, move workspace to eDP-1", show_apply_button=True)
            new.connect("apply", self._exec_added)
            self.exec_group.add(new)
            self._exec_rows.append(new)

    def _fill_output(self, out: Output, pl: Placed) -> None:
        eff = pl.eff
        self.criteria_row.set_text(out.criteria)
        title = "Display"
        if pl.monitor:
            title = f"{pl.monitor.make} {pl.monitor.model}".strip() or pl.monitor.connector
            if pl.monitor.connected:
                title += f" — connected as {pl.monitor.connector}"
        elif out.criteria == "*":
            title = "Any display"
        self.output_group.set_title(GLib.markup_escape_text(title))
        self.criteria_pick.set_menu_model(self._criteria_menu())

        def inh(key: str, base: str = "") -> str:
            return "From the global defaults" if key in eff.inherited else base

        self.enabled_row.set_active(eff.enabled)
        self.enabled_row.set_subtitle(inh("enabled", "" if out.enabled is not None else "Not set: on by default"))

        # resolution
        modes = pl.monitor.modes if pl.monitor else []
        directive = out.arg("mode")
        if out.get("mode") and len(out.get("mode").args) == 2:
            directive = " ".join(out.get("mode").args)
        if modes:
            values: list[str | None] = [None, "preferred"]
            labels = ["Not set (monitor's preferred)", "Preferred"]
            for m in modes:
                values.append(str(m))
                labels.append(f"{m.width} × {m.height}" + (f" @ {m.refresh:g} Hz" if m.refresh else ""))
            if directive and directive not in values:
                values.append(directive)
                labels.append(directive)
            self._mode_values = values
            self.mode_combo.set_model(Gtk.StringList.new(labels))
            self.mode_combo.set_selected(values.index(directive) if directive in values else 0)
            self.mode_combo.set_subtitle(inh("mode"))
            self.mode_combo.set_visible(True)
            self.mode_entry.set_visible(False)
        else:
            self.mode_entry.set_text(directive or "")
            self.mode_combo.set_visible(False)
            self.mode_entry.set_visible(True)

        self.scale_row.set_value(eff.scale)
        self.scale_row.set_subtitle(inh("scale", "" if out.get("scale") else "Not set"))
        self.transform_combo.set_selected(TRANSFORMS.index(eff.transform) if eff.transform in TRANSFORMS else 0)
        self.transform_combo.set_subtitle(inh("transform"))
        self.x_row.set_value(pl.rect.x)
        self.y_row.set_value(pl.rect.y)
        pos_note = "Not set: placed automatically" if pl.auto_position else inh("position")
        self.x_row.set_subtitle(pos_note)
        self.y_row.set_subtitle("")
        self.x_row.set_sensitive(eff.enabled)
        self.y_row.set_sensitive(eff.enabled)
        vrr = {None: 0, "on": 1, "off": 2}.get(eff.adaptive_sync, 0)
        self.vrr_combo.set_selected(vrr)
        self.vrr_combo.set_subtitle(inh("adaptive_sync"))

    def _criteria_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        known = Gio.Menu()
        for m in self.db.sorted():
            label = m.description + (f"  ({m.connector}, connected)" if m.connected else "")
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.set-criteria", GLib.Variant("s", m.description))
            known.append_item(item)
        menu.append_section("Known displays", known)
        other = Gio.Menu()
        for label, value in [("Any display (*)", "*")] + [(f"Connector {m.connector}", m.connector) for m in self._connected()]:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.set-criteria", GLib.Variant("s", value))
            other.append_item(item)
        menu.append_section(None, other)
        return menu

    def _refresh_add_menu(self) -> None:
        p = self.profile
        self.add_output_button.set_sensitive(p is not None)
        menu = Gio.Menu()
        if p is None:
            self.add_output_button.set_menu_model(menu)
            return
        used = {resolve(self.doc, o).criteria for o in p.outputs}
        known = Gio.Menu()
        for m in self.db.sorted():
            if m.description in used or m.connector in used:
                continue
            label = f"{m.make} {m.model}".strip() + (f" ({m.connector})" if m.connected else " (not connected)")
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.add-output", GLib.Variant("s", m.description))
            known.append_item(item)
        if known.get_n_items():
            menu.append_section("Known displays", known)
        other = Gio.Menu()
        for label, value in [("Any display (*)", "*"), ("All other displays (...*)", "...*"), ("Other…", "")]:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.add-output", GLib.Variant("s", value))
            other.append_item(item)
        menu.append_section(None, other)
        self.add_output_button.set_menu_model(menu)

    # ================================================================== edits: profiles

    def _profile_selected(self, box, row) -> None:
        if self._loading or row is None:
            return
        idx = row.get_index()
        if idx != self.profile_index:
            self.profile_index = idx
            self.output_index = None
            self.refresh()

    def _unique_name(self, base: str) -> str:
        names = {p.name for p in self.doc.profiles}
        if base not in names:
            return base
        i = 2
        while f"{base}-{i}" in names:
            i += 1
        return f"{base}-{i}"

    def new_profile(self, from_connected: bool) -> None:
        prof = Profile(name=self._unique_name("new"))
        if from_connected:
            x = 0
            for m in self._connected():
                o = Output(criteria=m.connector if m.is_internal else m.description)
                prof.add(o)
                o.set("enable")
                mode = m.current or m.best_mode
                if mode:
                    o.set("mode", str(mode))
                    o.set("position", f"{x},0")
                    x += mode.width
        self.doc.add_profile(prof, after=self.profile)
        self.profile_index = self.doc.profiles.index(prof)
        self.output_index = None
        self.changed()
        self.name_row.grab_focus()

    def duplicate_profile(self) -> None:
        p = self.profile
        if not p:
            return
        # Re-parse the profile's own text to get an independent copy.
        copy = parse(p.text()).profiles[0]
        copy.rename(self._unique_name((p.name or "profile") + "-copy"))
        copy.leading = ""
        self.doc.add_profile(copy, after=p)
        self.profile_index = self.doc.profiles.index(copy)
        self.changed()

    def delete_profile(self) -> None:
        p = self.profile
        if not p:
            return
        self.doc.remove_profile(p)
        self.profile_index = max(0, min(self.profile_index, len(self.doc.profiles) - 1))
        self.output_index = None
        self.changed()
        self.toast(f"Deleted {p.name or 'profile'}.", undo=True)

    def move_profile(self, delta: int) -> None:
        p = self.profile
        if p and self.doc.move_profile(p, delta):
            self.profile_index += delta
            self.changed()

    def _name_applied(self, row) -> None:
        p = self.profile
        if p:
            name = row.get_text().strip()
            if name and name != p.name and name in {q.name for q in self.doc.profiles}:
                self.toast(f"There is already a profile called {name}.")
                return
            p.rename(name or None)
            self.changed()

    # ================================================================== edits: outputs

    def _canvas_select(self, out: Output | None) -> None:
        self.select_output(out)
        self._loading = True
        try:
            self.canvas.set_layout(self.placed, self.output, self.canvas.problem_outputs)
            self._refresh_panel()
        finally:
            self._loading = False

    def _canvas_move(self, out: Output, x: int, y: int) -> None:
        out.set("position", f"{x},{y}")
        # Live update without touching history.
        self._loading = True
        try:
            self._refresh_layout()
            pl = self._placed_for(out)
            if pl:
                self.x_row.set_value(pl.rect.x)
                self.y_row.set_value(pl.rect.y)
        finally:
            self._loading = False

    def _canvas_move_done(self) -> None:
        self.changed()

    def add_output(self, criteria: str) -> None:
        p = self.profile
        if not p:
            return
        keyword = "output"
        if criteria == "...*":
            keyword, criteria = "...output", "*"
        if not criteria:
            criteria = "DP-1"
        out = Output(keyword=keyword, criteria=criteria)
        p.add(out)
        if keyword == "output":
            right = max((q.rect.right for q in self.placed if q.enabled), default=0)
            out.set("position", f"{int(right)},0")
        self.output_index = p.outputs.index(out)
        self.changed()
        if criteria == "DP-1":
            self.criteria_row.grab_focus()

    def set_criteria(self, criteria: str) -> None:
        out = self.output
        if out:
            out.set_criteria(criteria)
            self.changed()

    def remove_output(self) -> None:
        p, out = self.profile, self.output
        if p and out:
            p.remove(out)
            self.output_index = None
            self.changed()

    def _criteria_applied(self, row) -> None:
        text = row.get_text().strip()
        if text:
            self.set_criteria(text)

    def _enabled_changed(self, row, _pspec) -> None:
        if self._loading or not self.output:
            return
        self.output.set_enabled(row.get_active())
        self.changed()

    def _mode_selected(self, row, _pspec) -> None:
        if self._loading or not self.output:
            return
        i = row.get_selected()
        if i < 0 or i >= len(self._mode_values):
            return
        value = self._mode_values[i]
        if value is None:
            self.output.unset("mode")
        else:
            self.output.set("mode", *value.split(" "))
        self.changed()

    def _mode_applied(self, row) -> None:
        out = self.output
        if not out:
            return
        text = row.get_text().strip()
        if not text:
            out.unset("mode")
        elif text == "preferred" or Mode.parse(text.removeprefix("--custom").strip()):
            out.set("mode", *text.split())
        else:
            self.toast("Resolution must look like 1920x1080 or 1920x1080@60Hz.")
            return
        self.changed()

    def _scale_changed(self, row, _pspec) -> None:
        if self._loading or not self.output:
            return
        self.output.set("scale", fmt_scale(row.get_value()))
        self.changed()

    def _transform_selected(self, row, _pspec) -> None:
        if self._loading or not self.output:
            return
        t = TRANSFORMS[row.get_selected()]
        if t == "normal" and self.output.get("transform") is None:
            return
        self.output.set("transform", t)
        self.changed()

    def _position_changed(self, row, _pspec) -> None:
        if self._loading or not self.output:
            return
        self.output.set("position", f"{int(self.x_row.get_value())},{int(self.y_row.get_value())}")
        self.changed()

    def _vrr_selected(self, row, _pspec) -> None:
        if self._loading or not self.output:
            return
        i = row.get_selected()
        if i == 0:
            self.output.unset("adaptive_sync")
        else:
            self.output.set("adaptive_sync", "on" if i == 1 else "off")
        self.changed()

    # ================================================================== edits: exec

    def _exec_added(self, row) -> None:
        p = self.profile
        text = row.get_text().strip()
        if p and text:
            p.add(Exec(command=text))
            self.changed()

    def _exec_applied(self, row, e: Exec) -> None:
        text = row.get_text().strip()
        if text:
            e.set_command(text)
            self.changed()

    def _exec_removed(self, _button, e: Exec) -> None:
        p = self.profile
        if p and e in p.children:
            p.remove(e)
            self.changed()

    # ================================================================== dialogs

    def toast(self, text: str, undo: bool = False) -> None:
        t = Adw.Toast(title=GLib.markup_escape_text(text), timeout=4)
        if undo:
            t.set_button_label("Undo")
            t.set_action_name("win.undo")
        self.toasts.add_toast(t)

    def show_diff(self) -> None:
        before = (self.disk_text or "").splitlines(keepends=True)
        after = self.current.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(before, after, "on disk", "edited"))
        dialog = Adw.Dialog(title="Unsaved Changes", content_width=760, content_height=560)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
        view.set_left_margin(12)
        view.set_top_margin(12)
        buf = view.get_buffer()
        if not diff:
            buf.set_text("No unsaved changes.")
        else:
            add = buf.create_tag("add", foreground="#26a269")
            rem = buf.create_tag("rem", foreground="#c01c28")
            hunk = buf.create_tag("hunk", foreground="#1c71d8")
            for line in diff.splitlines(keepends=True):
                tag = None
                if line.startswith("+") and not line.startswith("+++"):
                    tag = add
                elif line.startswith("-") and not line.startswith("---"):
                    tag = rem
                elif line.startswith("@@"):
                    tag = hunk
                end = buf.get_end_iter()
                if tag:
                    buf.insert_with_tags(end, line, tag)
                else:
                    buf.insert(end, line)
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.set_child(view)
        tv.set_content(sw)
        dialog.set_child(tv)
        dialog.present(self)

    def _confirm_discard(self, then) -> None:
        if not self.dirty or self.disk_text is None:
            then()
            return
        dialog = Adw.AlertDialog(heading="Discard unsaved changes?", body="Your changes since the last save will be lost.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Discard")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.choose(self, None, lambda d, res: then() if d.choose_finish(res) == "discard" else None)

    def _close_request(self, _win) -> bool:
        if not self.dirty or self.disk_text is None:
            return False
        dialog = Adw.AlertDialog(heading="Save changes?", body="The kanshi configuration has unsaved changes.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Discard")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        def done(d, res):
            r = d.choose_finish(res)
            if r == "save":
                self.save()
                if not self.dirty:
                    self.destroy()
            elif r == "discard":
                self.disk_text = self.current
                self.destroy()

        dialog.choose(self, None, done)
        return True
