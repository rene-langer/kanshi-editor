"""Drive the real window (needs a Wayland/X display; skipped otherwise)."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

FIXTURE = (Path(__file__).parent / "fixtures" / "full.conf").read_text()

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    HAVE_DISPLAY = Gtk.init_check()
except (ImportError, ValueError):
    HAVE_DISPLAY = False


@unittest.skipUnless(HAVE_DISPLAY, "no display available")
class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from kanshi_editor.window import Window

        cls.Window = Window
        Adw.init()
        cls.app = Adw.Application(application_id="io.github.rene_langer.KanshiEditor.Test")
        cls.app.register(None)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        os.environ["XDG_STATE_HOME"] = str(self.tmp / "state")
        os.environ.pop("SWAYSOCK", None)
        # Real file lives elsewhere; the config path is a symlink, as with dotfiles.
        (self.tmp / "dotfiles").mkdir()
        self.real = self.tmp / "dotfiles" / "config"
        self.real.write_text(FIXTURE)
        self.link = self.tmp / "config"
        self.link.symlink_to(self.real)
        self.win = self.Window(self.app, self.link)
        self.win._reload_kanshi = lambda: None  # do not talk to a running kanshi
        self.addCleanup(self.win.destroy)

    def test_loads_profiles(self):
        self.assertEqual(self.win.profile_list.get_row_at_index(0).get_title(), "desk")
        self.assertFalse(self.win.dirty)

    def test_drag_undo_redo_and_save_through_symlink(self):
        w = self.win
        desk = w.doc.profiles[0]
        tall = desk.outputs[1]
        w._canvas_select(tall)
        self.assertEqual(w.x_row.get_value(), 3440)
        w._canvas_move(tall, 3440, 50)
        w._canvas_move(tall, 3440, 100)
        w._canvas_move_done()
        self.assertTrue(w.dirty)
        self.assertEqual(len(w.undo_stack), 1)  # one drag, one undo step
        w.undo()
        self.assertEqual(w.current, FIXTURE)
        self.assertFalse(w.dirty)
        w.redo()
        w.save()
        self.assertTrue(self.link.is_symlink())
        saved = self.real.read_text()
        self.assertIn("position 3440,100 transform 90  # portrait", saved)
        self.assertEqual(len(saved.splitlines()), len(FIXTURE.splitlines()))
        self.assertFalse(w.dirty)

    def test_panel_edits(self):
        w = self.win
        w.profile_index = 1  # docked
        w.refresh()
        w._canvas_select(w.doc.profiles[1].outputs[0])
        w.scale_row.set_value(2)
        w.transform_combo.set_selected(1)  # 90
        w.enabled_row.set_active(False)
        out = w.current
        self.assertIn("\toutput eDP-1 disable mode 2880x1800@120Hz position 0,0 scale 2 transform 90\n", out)
        self.assertEqual(len(w.undo_stack), 3)

    def test_profile_operations(self):
        w = self.win
        w.new_profile(from_connected=False)
        self.assertEqual(w.doc.profiles[1].name, "new")
        self.assertEqual(w.profile_index, 1)
        w.add_output("eDP-1")
        w.add_output("...*")
        w.move_profile(-1)
        self.assertEqual(w.doc.profiles[0].name, "new")
        w.duplicate_profile()
        self.assertEqual(w.doc.profiles[1].name, "new-copy")
        w.delete_profile()
        self.assertEqual([p.name for p in w.doc.profiles][:2], ["new", "desk"])
        text = w.current
        self.assertIn("profile new {\n    output eDP-1 position 0,0\n    ...output *\n}\n\n# Desk", text)
        self.assertTrue(text.startswith("# File header"))
        from kanshi_editor.config import parse

        self.assertEqual(parse(text).render(), text)

    def test_exec_rows(self):
        w = self.win
        prof = w.doc.profiles[0]
        new_row = w._exec_rows[-1]
        new_row.set_text("swaymsg workspace 1")
        w._exec_added(new_row)
        self.assertEqual([e.command for e in prof.execs][-1], "swaymsg workspace 1")
        w._exec_removed(None, w.doc.profiles[0].execs[0])
        self.assertNotIn("wallpaper reload", w.current)

    def test_conflicting_change_on_disk_is_not_overwritten_silently(self):
        w = self.win
        self.real.write_text(FIXTURE + "\n# edited elsewhere\n")
        w.doc.profiles[0].rename("x")
        w.changed()
        w.save()  # opens a dialog instead of writing
        self.assertIn("# edited elsewhere", self.real.read_text())
        self.assertTrue(w.dirty)

    def _connect(self, *monitors):
        self.win.db.merge_connected(list(monitors))

    def test_status_line_parsing(self):
        from kanshi_editor import window as winmod

        m = winmod._STATUS.search("Current profile: docked\nPending profile: desk\n")
        self.assertEqual(m.group(1), "docked")

    def test_active_profile_from_kanshi(self):
        from kanshi_editor.monitors import Monitor

        w = self.win
        self._connect(
            Monitor("BOE", "X", "", "eDP-1", connected=True, active=True),
            Monitor("A", "B", "C", "DP-1", connected=True, active=True),
        )
        w.kanshi_running, w.kanshi_current = True, "docked"
        w.profile_index = 1
        w.refresh()
        self.assertEqual(w.active()[0].name, "docked")
        self.assertTrue(w.status_pill.get_visible())
        self.assertEqual(w.status_pill.get_label(), "● Active profile")
        self.assertIn("Active", w.match_row.get_subtitle())
        pill = w.profile_list.get_row_at_index(1).get_child()
        self.assertIsNotNone(pill)
        w.profile_index = 0
        w.refresh()
        self.assertFalse(w.status_pill.get_visible())

    def test_expected_profile_without_kanshi(self):
        from kanshi_editor.monitors import Monitor

        w = self.win
        self._connect(
            Monitor("BOE", "X", "", "eDP-1", connected=True, active=True),
            Monitor("A", "B", "C", "DP-1", connected=True, active=True),
        )
        w.kanshi_running, w.kanshi_current = False, None
        w.profile_index = 1
        w.refresh()
        active, kind = w.active()
        self.assertEqual((active.name, kind), ("docked", "expected"))
        self.assertEqual(w.status_pill.get_label(), "● Expected profile")

    def test_thumbnail_is_drawn_for_assigned_output(self):
        import cairo

        from kanshi_editor.monitors import Monitor

        w = self.win
        dp1 = Monitor("A", "B", "C", "DP-1", connected=True, active=True)
        self._connect(Monitor("BOE", "X", "", "eDP-1", connected=True, active=True), dp1)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 32, 18)
        ctx = cairo.Context(surface)
        ctx.set_source_rgb(1, 0, 0)
        ctx.paint()
        w.thumbs.surfaces["DP-1"] = surface
        w.profile_index = 1
        w.refresh()
        star = w.doc.profiles[1].outputs[1]
        self.assertIs(w.canvas.assigned[id(star)], dp1)
        # Render the canvas offscreen and check the red picture landed inside the "*" tile.
        target = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 500)
        w.canvas._view = None
        w.canvas._draw(w.canvas, cairo.Context(target), 800, 500)
        rect = next(r for r, p in w.canvas._hit if p.output is star)
        x, y = int(rect.x + 10), int(rect.y + rect.h - 10)
        data = target.get_data()
        b, g, r, a = data[y * target.get_stride() + x * 4 : y * target.get_stride() + x * 4 + 4]
        self.assertGreater(r, 150)
        self.assertLess(g, 80)

    def test_broken_file_shows_error(self):
        self.real.write_text("profile {\n output A mode\n}\n")
        w = self.Window(self.app, self.link)
        self.addCleanup(w.destroy)
        self.assertEqual(w.stack.get_visible_child_name(), "error")
        self.assertIn("line 2", w.error_page.get_description())


if __name__ == "__main__":
    unittest.main()
