import unittest
from pathlib import Path

from kanshi_editor.config import (
    Exec,
    Include,
    Mode,
    Opaque,
    Output,
    ParseError,
    Profile,
    parse,
    quote,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "full.conf").read_text()


def profile(doc, name):
    return next(p for p in doc.profiles if p.name == name)


class RoundTrip(unittest.TestCase):
    def test_fixture_is_byte_identical(self):
        self.assertEqual(parse(FIXTURE).render(), FIXTURE)

    def test_odd_inputs_round_trip(self):
        for src in [
            "",
            "\n\n",
            "# only a comment",
            "profile{}",
            "profile x {\n}\n",
            "profile x {output A enable}",
            "profile x {\r\n  output A enable\r\n}\r\n",
            "profile x {\n  output A {\n  }\n}",
            "unknown thing { with { nested } braces }\nprofile {}\n",
            "profile x {\n  output A enable   \n}   \n   ",
        ]:
            with self.subTest(src=src):
                self.assertEqual(parse(src).render(), src)

    def test_real_config_round_trips(self):
        path = Path.home() / ".config" / "kanshi" / "config"
        if not path.exists():
            self.skipTest("no kanshi config on this machine")
        src = path.read_text()
        self.assertEqual(parse(src).render(), src)


class Structure(unittest.TestCase):
    def setUp(self):
        self.doc = parse(FIXTURE)

    def test_top_level_nodes(self):
        kinds = [type(c).__name__ for c in self.doc.children]
        self.assertEqual(kinds, ["Include", "Output", "Output"] + ["Profile"] * 5)
        self.assertIsInstance(self.doc.children[0], Include)
        self.assertEqual([p.name for p in self.doc.profiles], ["desk", "docked", "block-form", None, "quoted name"])
        self.assertEqual(self.doc.defaults[0].arg("alias"), "$desk")

    def test_outputs_and_directives(self):
        desk = profile(self.doc, "desk")
        self.assertEqual([o.criteria for o in desk.outputs], ["$desk", "Acme Tall 0002", "eDP-1"])
        tall = desk.outputs[1]
        self.assertEqual(tall.arg("mode"), "1920x1080@60Hz")
        self.assertEqual(tall.arg("transform"), "90")
        self.assertIs(desk.outputs[2].enabled, False)
        self.assertIsNone(desk.outputs[0].enabled)
        self.assertEqual(desk.execs[0].command, "$HOME/.local/bin/wallpaper reload")

    def test_block_form_and_multi_output(self):
        p = profile(self.doc, "block-form")
        block, multi = p.outputs
        self.assertTrue(block.block)
        self.assertEqual(block.arg("position"), "0,0")
        self.assertEqual(multi.keyword, "...output")

    def test_exec_keeps_hash_and_quotes(self):
        p = profile(self.doc, "quoted name")
        self.assertIn("'\"Some Other Company GTBZ 2525\"'  # not a comment", p.execs[0].command)
        self.assertEqual(p.outputs[0].get("mode").args, ["--custom", "1280x720@60Hz"])
        self.assertIsInstance(p.children[-1], Opaque)

    def test_errors(self):
        for src, where in [
            ("profile x {\n  output A\n", "missing '}'"),
            ('profile x {\n  output "A\n}', "unterminated quoted"),
            ("profile x {\n  output A mode\n}", "needs an argument"),
            ("profile x y z {}", "expected '{'"),
        ]:
            with self.subTest(src=src):
                with self.assertRaises(ParseError) as cm:
                    parse(src)
                self.assertIn(where, str(cm.exception))
                self.assertIn("line", str(cm.exception))


class Editing(unittest.TestCase):
    def setUp(self):
        self.doc = parse(FIXTURE)

    def changed_lines(self, after):
        a, b = FIXTURE.splitlines(), after.splitlines()
        import difflib

        diff = difflib.unified_diff(a, b, lineterm="", n=0)
        return [line for line in diff if line[:1] in "+-" and line[:3] not in ("+++", "---")]

    def test_edit_inline_touches_only_that_line(self):
        tall = profile(self.doc, "desk").outputs[1]
        tall.set("position", "3440,100")
        diff = self.changed_lines(self.doc.render())
        self.assertEqual(
            diff,
            [
                '-    output "Acme Tall 0002" mode 1920x1080@60Hz position 3440,0 transform 90  # portrait',
                '+    output "Acme Tall 0002" mode 1920x1080@60Hz position 3440,100 transform 90  # portrait',
            ],
        )

    def test_unedited_spacing_is_kept_until_edited(self):
        out = profile(self.doc, "desk").outputs[0]
        out.set("scale", "1")  # same value: no change at all
        self.assertEqual(self.doc.render(), FIXTURE)

    def test_add_and_remove_directive(self):
        tall = profile(self.doc, "desk").outputs[1]
        tall.unset("transform")
        tall.set("adaptive_sync", "on")
        self.assertIn(
            'output "Acme Tall 0002" mode 1920x1080@60Hz position 3440,0 adaptive_sync on  # portrait',
            self.doc.render(),
        )

    def test_block_form_edits_one_line_and_keeps_inner_comment(self):
        block = profile(self.doc, "block-form").outputs[0]
        block.set("position", "10,20")
        block.set("scale", "2")
        block.set_enabled(False)
        out = self.doc.render()
        self.assertIn(
            '    output "Some Company ASDF 4242" {\n'
            "        disable\n"
            "        mode 1600x900\n"
            "        # inside a block\n"
            "        position 10,20\n"
            "        scale 2\n"
            "    }\n",
            out,
        )
        self.assertEqual(parse(out).render(), out)

    def test_enable_toggle(self):
        edp = profile(self.doc, "desk").outputs[2]
        edp.set_enabled(True)
        self.assertIn("    output eDP-1 enable\n", self.doc.render())
        edp.set_enabled(None)
        self.assertIn("    output eDP-1\n", self.doc.render())

    def test_criteria_with_spaces_is_quoted(self):
        edp = profile(self.doc, "desk").outputs[2]
        edp.set_criteria("Dell Inc. DELL U4320Q 7BL3Y13")
        self.assertIn('    output "Dell Inc. DELL U4320Q 7BL3Y13" disable\n', self.doc.render())

    def test_add_output_uses_profile_indent(self):
        docked = profile(self.doc, "docked")
        o = docked.add(Output(criteria="DP-1", directives=[]))
        o.set("position", "0,1800")
        self.assertIn('\toutput "*" enable position 1920,0\n\toutput DP-1 position 0,1800\n}', self.doc.render())

    def test_add_exec_to_one_line_profile(self):
        p = self.doc.profiles[3]
        p.add(Exec(command="notify-send hi"))
        self.assertIn("profile { output LVDS-1 enable scale 2\n    exec notify-send hi\n}", self.doc.render())
        self.assertEqual(len(parse(self.doc.render()).profiles[3].execs), 1)

    def test_remove_output(self):
        desk = profile(self.doc, "desk")
        desk.remove(desk.outputs[2])
        self.assertNotIn("output eDP-1 disable", self.doc.render())
        self.assertIn("transform 90  # portrait\n    exec", self.doc.render())

    def test_rename_profile(self):
        p = profile(self.doc, "desk")
        p.rename("home office")
        self.assertIn('# The wide one is on the left.\nprofile "home office" {\n', self.doc.render())

    def test_exec_edit(self):
        e = profile(self.doc, "desk").execs[0]
        e.set_command("true")
        self.assertIn("    exec true\n}", self.doc.render())


class ProfileOrder(unittest.TestCase):
    def setUp(self):
        self.doc = parse(FIXTURE)

    def test_move_keeps_attached_comment_and_gaps(self):
        desk = profile(self.doc, "desk")
        self.assertTrue(self.doc.move_profile(desk, +1))
        out = self.doc.render()
        self.assertEqual([p.name for p in parse(out).profiles][:2], ["docked", "desk"])
        self.assertIn("output eDP-1 scale 1.5\n\nprofile docked {", out)
        self.assertIn("}\n\n# Desk: two monitors.\n# The wide one is on the left.\nprofile desk {", out)
        self.assertTrue(out.startswith("# File header"))
        self.assertTrue(self.doc.move_profile(desk, -1))
        self.assertEqual(self.doc.render(), FIXTURE)

    def test_move_out_of_range(self):
        self.assertFalse(self.doc.move_profile(self.doc.profiles[0], -1))
        self.assertFalse(self.doc.move_profile(self.doc.profiles[-1], +1))

    def test_remove_first_node_keeps_file_header(self):
        doc = parse("# header\n\n# about a\nprofile a {\n}\n\nprofile b {\n}\n")
        doc.remove_profile(doc.profiles[0])
        self.assertEqual(doc.render(), "# header\n\nprofile b {\n}\n")

    def test_add_profile_at_end_and_after(self):
        doc = parse("profile a {\n}\n")
        doc.add_profile(Profile(name="b"))
        self.assertEqual(doc.render(), "profile a {\n}\n\nprofile b {}\n")
        doc.add_profile(Profile(name="mid"), after=doc.profiles[0])
        self.assertEqual([p.name for p in parse(doc.render()).profiles], ["a", "mid", "b"])

    def test_add_profile_to_empty_file(self):
        doc = parse("")
        p = doc.add_profile(Profile(name="x"))
        p.add(Output(criteria="eDP-1"))
        self.assertEqual(doc.render(), "profile x {\n    output eDP-1\n}\n")


class Values(unittest.TestCase):
    def test_mode(self):
        self.assertEqual(Mode.parse("2880x1800@120Hz"), Mode(2880, 1800, 120.0))
        self.assertEqual(str(Mode(2880, 1800, 120.0)), "2880x1800@120Hz")
        self.assertEqual(str(Mode(1920, 1080, 59.951)), "1920x1080@59.951Hz")
        self.assertEqual(str(Mode(1920, 1080)), "1920x1080")
        self.assertIsNone(Mode.parse("preferred"))

    def test_quote(self):
        self.assertEqual(quote("eDP-1"), "eDP-1")
        self.assertEqual(quote("$desk"), "$desk")
        self.assertEqual(quote("*"), "*")
        self.assertEqual(quote("A B"), '"A B"')
        self.assertEqual(quote('a"b'), '"a\\"b"')
        self.assertEqual(quote(""), '""')


if __name__ == "__main__":
    unittest.main()
