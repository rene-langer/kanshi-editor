import unittest
from pathlib import Path

from kanshi_editor.config import Mode, parse
from kanshi_editor.layout import (
    Rect,
    assign_connected,
    issues,
    logical_size,
    matches_connected,
    place,
    predicted_profile,
    resolve,
    snap,
)
from kanshi_editor.monitors import Monitor, MonitorDB, parse_sway_outputs, parse_wlr_randr

FIXTURE = (Path(__file__).parent / "fixtures" / "full.conf").read_text()


def db_with(*monitors):
    db = MonitorDB(Path("/nonexistent/monitors.json"))
    db.merge_connected(list(monitors))
    return db


class Geometry(unittest.TestCase):
    def test_logical_size(self):
        self.assertEqual(logical_size(Mode(2880, 1800), 1.5, "normal"), (1920, 1200))
        self.assertEqual(logical_size(Mode(1920, 1080), 1, "90"), (1080, 1920))
        self.assertEqual(logical_size(Mode(1920, 1080), 1, "flipped-270"), (1080, 1920))

    def test_snap_to_edges(self):
        other = Rect(0, 0, 1920, 1080)
        self.assertEqual(snap(Rect(1925, 7, 100, 100), [other], 10), (1920, 0))
        self.assertEqual(snap(Rect(1950, 7, 100, 100), [other], 10), (1950, 0))
        # bottom alignment
        self.assertEqual(snap(Rect(1920, 975, 100, 100), [other], 10), (1920, 980))

    def test_touch_and_overlap(self):
        a = Rect(0, 0, 100, 100)
        self.assertTrue(a.touches(Rect(100, 50, 10, 10)))
        self.assertFalse(a.touches(Rect(100, 100, 10, 10)))  # corner only
        self.assertTrue(a.overlaps(Rect(99, 0, 10, 10)))
        self.assertFalse(a.overlaps(Rect(100, 0, 10, 10)))


class Resolve(unittest.TestCase):
    def setUp(self):
        self.doc = parse(FIXTURE)

    def test_alias_and_defaults(self):
        desk = self.doc.profiles[0]
        eff = resolve(self.doc, desk.outputs[0])
        self.assertEqual(eff.criteria, "Acme Wide 0001")
        docked = self.doc.profiles[1]
        edp = resolve(self.doc, docked.outputs[0])
        self.assertEqual(edp.scale, 1.5)
        self.assertIn("scale", edp.inherited)
        self.assertEqual(edp.mode, Mode(2880, 1800, 120.0))

    def test_place_desk(self):
        placed = place(self.doc, self.doc.profiles[0], db_with())
        wide, tall, edp = placed
        self.assertEqual((wide.rect.x, wide.rect.w, wide.rect.h), (0, 3440, 1440))
        self.assertEqual((tall.rect.x, tall.rect.w, tall.rect.h), (3440, 1080, 1920))
        self.assertFalse(edp.enabled)
        self.assertFalse(edp.size_known)

    def test_monitor_db_supplies_size(self):
        mon = Monitor("Some Company", "ASDF", "4242", "DP-1", modes=[Mode(2560, 1440, 60)], connected=True)
        doc = parse('profile p {\n  output "Some Company ASDF 4242" scale 2\n  output DP-9\n}\n')
        a, b = place(doc, doc.profiles[0], db_with(mon))
        self.assertTrue(a.size_known)
        self.assertEqual((a.rect.w, a.rect.h), (1280, 720))
        self.assertTrue(b.auto_position)
        self.assertEqual(b.rect.x, 1280)  # auto-placed to the right

    def test_issues(self):
        doc = parse(
            "profile p {\n"
            "  output A mode 100x100 position 0,0\n"
            "  output B mode 100x100 position 50,0\n"
            "  output C mode 10x10 position 500,500\n"
            "}\n"
        )
        msgs = [m for _, m in issues(place(doc, doc.profiles[0], db_with()), doc.profiles[0])]
        self.assertTrue(any("overlaps" in m for m in msgs))
        self.assertTrue(any("C doesn't touch" in m for m in msgs))


class Matching(unittest.TestCase):
    """Which profile kanshi would pick, and which physical screen each output drives."""

    def setUp(self):
        self.left = Monitor("Dell Inc.", "DELL P2419H", "AAA", "DP-1", connected=True, active=True)
        self.right = Monitor("Dell Inc.", "DELL P2419H", "BBB", "DP-2", connected=True, active=True)
        self.laptop = Monitor("BOE", "0x0BCA", "", "eDP-1", connected=True, active=True)
        self.doc = parse(
            "profile twins {\n"
            '  output "Dell Inc. DELL P2419H BBB" position 1920,0\n'
            '  output "Dell Inc. DELL P2419H AAA" position 0,0\n'
            "}\n"
            "profile laptop-plus {\n  output eDP-1\n  output * position 1920,0\n}\n"
            "profile laptop {\n  output eDP-1\n}\n"
            "profile any-number {\n  output eDP-1\n  ...output * enable\n}\n"
        )

    def profile(self, name):
        return next(p for p in self.doc.profiles if p.name == name)

    def test_identical_models_are_told_apart_by_serial(self):
        twins = self.profile("twins")
        got = assign_connected(self.doc, twins, [self.left, self.right])
        b, a = twins.outputs
        self.assertIs(got[id(a)], self.left)
        self.assertIs(got[id(b)], self.right)

    def test_count_must_match(self):
        self.assertFalse(matches_connected(self.doc, self.profile("laptop"), [self.laptop, self.left]))
        self.assertTrue(matches_connected(self.doc, self.profile("laptop-plus"), [self.laptop, self.left]))
        self.assertFalse(matches_connected(self.doc, self.profile("laptop-plus"), [self.laptop]))

    def test_wildcard_gets_the_leftover_monitor(self):
        p = self.profile("laptop-plus")
        got = assign_connected(self.doc, p, [self.left, self.laptop])
        self.assertIs(got[id(p.outputs[0])], self.laptop)
        self.assertIs(got[id(p.outputs[1])], self.left)

    def test_multi_output_takes_any_number(self):
        p = self.profile("any-number")
        self.assertTrue(matches_connected(self.doc, p, [self.laptop]))
        self.assertTrue(matches_connected(self.doc, p, [self.laptop, self.left, self.right]))

    def test_predicted_is_first_match(self):
        self.assertEqual(predicted_profile(self.doc, [self.laptop]).name, "laptop")
        self.assertEqual(predicted_profile(self.doc, [self.laptop, self.left]).name, "laptop-plus")
        self.assertEqual(predicted_profile(self.doc, [self.left, self.right]).name, "twins")
        self.assertIsNone(predicted_profile(self.doc, []))


class Detection(unittest.TestCase):
    def test_sway(self):
        data = [
            {
                "type": "output",
                "name": "eDP-1",
                "make": "BOE",
                "model": "0x0BCA",
                "serial": "",
                "modes": [{"width": 2880, "height": 1800, "refresh": 120000}, {"width": 2880, "height": 1800, "refresh": 60000}],
                "current_mode": {"width": 2880, "height": 1800, "refresh": 120000},
            }
        ]
        (m,) = parse_sway_outputs(data)
        self.assertEqual(m.description, "BOE 0x0BCA Unknown")
        self.assertEqual(m.best_mode, Mode(2880, 1800, 120.0))
        self.assertTrue(m.is_internal)

    def test_wlr_randr(self):
        data = [
            {
                "name": "DP-3",
                "make": "Dell Inc.",
                "model": "DELL U4320Q",
                "serial": "7BL3Y13",
                "modes": [
                    {"width": 3840, "height": 2160, "refresh": 59.997, "preferred": True, "current": True},
                    {"width": 1920, "height": 1080, "refresh": 60.0, "preferred": False, "current": False},
                ],
            }
        ]
        (m,) = parse_wlr_randr(data)
        self.assertEqual(m.preferred, Mode(3840, 2160, 59.997))
        db = db_with(m)
        self.assertIs(db.find("Dell Inc. DELL U4320Q 7BL3Y13"), m)
        self.assertIs(db.find("Dell Inc. DELL U4320Q *"), m)
        self.assertIs(db.find("DP-3"), m)
        self.assertIsNone(db.find("*"))

    def test_db_round_trip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "m.json"
            db = MonitorDB(path)
            db.merge_connected([Monitor("A", "B", "C", "DP-1", modes=[Mode(1, 2, 3.5)], preferred=Mode(1, 2, 3.5))])
            db.save()
            db2 = MonitorDB(path)
            db2.load()
            m = db2.find("A B C")
            self.assertEqual(m.preferred, Mode(1, 2, 3.5))
            self.assertFalse(m.connected)


if __name__ == "__main__":
    unittest.main()
