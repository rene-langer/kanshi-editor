"""Lossless parser and editor for kanshi(5) configuration files.

The guiding rule: text that was not edited is written back byte for byte.

Every node (profile, output, exec, include, directive inside an output block, or
an unrecognised line) records

* ``leading``: the trivia before it (whitespace, newlines and comments), and
* ``src``: its own source text, from its first token to its last, and
* ``trailer``: the rest of its last line (spaces and a comment, if any).

Rendering a node yields ``leading + text + trailer``. Text is ``src`` until the node is
edited, then it is regenerated from the parsed fields. Comments always sit in
some node's ``leading`` (or a container's ``closing``), so editing a node never
touches the comments around it, including a comment at the end of its line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_INDENT = "    "

# Output directives and how many arguments each takes.
_ARITY = {
    "enable": 0,
    "disable": 0,
    "mode": 1,  # 2 with --custom
    "position": 1,
    "scale": 1,
    "transform": 1,
    "adaptive_sync": 1,
    "alias": 1,
}
OUTPUT_KEYS = tuple(_ARITY)

TRANSFORMS = (
    "normal",
    "90",
    "180",
    "270",
    "flipped",
    "flipped-90",
    "flipped-180",
    "flipped-270",
)


class ParseError(ValueError):
    def __init__(self, message: str, src: str, pos: int):
        line = src.count("\n", 0, pos) + 1
        col = pos - (src.rfind("\n", 0, pos) + 1) + 1
        super().__init__(f"line {line}, column {col}: {message}")
        self.line = line
        self.column = col


# --------------------------------------------------------------------------- quoting


_BARE = re.compile(r'^[^\s"{}#\\]+$')


def quote(arg: str) -> str:
    """Quote an argument the way kanshi expects, only when needed."""
    if arg and _BARE.match(arg):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------- nodes


def split_leading(leading: str) -> tuple[str, str]:
    """Split trivia into (gap, attached).

    ``attached`` is the run of comment lines directly above the node (no blank
    line in between) plus the node's own indentation; it belongs to the node and
    travels with it when the node moves. ``gap`` is everything before that.
    """
    lines = leading.split("\n")
    k = len(lines) - 1
    while k > 0 and lines[k - 1].strip().startswith("#"):
        k -= 1
    gap = "\n".join(lines[:k]) + ("\n" if k > 0 else "")
    return gap, "\n".join(lines[k:])


def _indent_of(leading: str) -> str:
    tail = leading.rsplit("\n", 1)[-1]
    return tail if tail.strip() == "" else ""


@dataclass(eq=False)
class Node:
    leading: str = ""
    src: str | None = None  # None: generated or edited, render from fields
    trailer: str = ""  # rest of the node's last line: spaces and a comment

    def text(self) -> str:
        raise NotImplementedError

    def render(self) -> str:
        return self.leading + self.text() + self.trailer

    def touch(self) -> None:
        self.src = None


@dataclass(eq=False)
class Opaque(Node):
    """A line the editor does not understand. Always kept verbatim."""

    def text(self) -> str:
        return self.src or ""


@dataclass(eq=False)
class Include(Node):
    path: str = ""

    def text(self) -> str:
        return self.src if self.src is not None else f"include {self.path}"


@dataclass(eq=False)
class Exec(Node):
    command: str = ""

    def text(self) -> str:
        return self.src if self.src is not None else f"exec {self.command}"

    def set_command(self, command: str) -> None:
        if command != self.command:
            self.command = command
            self.touch()


@dataclass(eq=False)
class Directive(Node):
    """One output directive, e.g. ``mode 1920x1080@60Hz`` or ``enable``."""

    key: str = ""
    args: list[str] = field(default_factory=list)

    def text(self) -> str:
        if self.src is not None:
            return self.src
        return " ".join([self.key, *(quote(a) for a in self.args)])


@dataclass(eq=False)
class Output(Node):
    keyword: str = "output"  # or "...output"
    criteria: str = ""
    directives: list[Directive] = field(default_factory=list)
    block: bool = False
    # Block form only: source of "output <criteria> {" and the trivia before "}".
    header_src: str | None = None
    closing: str = ""

    # -- rendering

    def _header(self) -> str:
        return f"{self.keyword} {quote(self.criteria)}"

    def text(self) -> str:
        if not self.block:
            if self.src is not None:
                return self.src
            return " ".join([self._header(), *(d.text() for d in self.directives)])
        header = self.header_src if self.header_src is not None else self._header() + " {"
        body = "".join(d.render() for d in self.directives)
        return header + body + self.closing + "}"

    def touch(self) -> None:
        self.src = None

    # -- directive access

    def get(self, key: str) -> Directive | None:
        for d in self.directives:
            if d.key == key:
                return d
        return None

    def arg(self, key: str) -> str | None:
        d = self.get(key)
        return d.args[-1] if d and d.args else None

    @property
    def enabled(self) -> bool | None:
        """True/False if set explicitly, None if not mentioned."""
        state = None
        for d in self.directives:
            if d.key == "enable":
                state = True
            elif d.key == "disable":
                state = False
        return state

    def set_criteria(self, criteria: str) -> None:
        if criteria == self.criteria:
            return
        self.criteria = criteria
        if self.block:
            self.header_src = None
        else:
            self.touch()

    def set(self, key: str, *args: str) -> None:
        """Set a directive, editing it in place, or appending it if absent."""
        if key in ("enable", "disable"):
            self.set_enabled(key == "enable")
            return
        d = self.get(key)
        if d is not None:
            if d.args == list(args):
                return
            d.args = list(args)
            d.touch()
        else:
            self._append(Directive(key=key, args=list(args)))
        if not self.block:
            self.touch()

    def unset(self, key: str) -> None:
        before = len(self.directives)
        self.directives = [d for d in self.directives if d.key != key]
        if len(self.directives) != before and not self.block:
            self.touch()

    def set_enabled(self, enabled: bool | None) -> None:
        if enabled == self.enabled:
            return
        want = None if enabled is None else ("enable" if enabled else "disable")
        existing = [d for d in self.directives if d.key in ("enable", "disable")]
        if want is None:
            self.directives = [d for d in self.directives if d not in existing]
        elif existing:
            first = existing[0]
            first.key = want
            first.args = []
            first.touch()
            self.directives = [d for d in self.directives if d not in existing[1:]]
        else:
            # Put enable/disable first, where people usually write it.
            d = Directive(key=want)
            if self.block:
                d.leading = self._child_leading()
            self.directives.insert(0, d)
        if not self.block:
            self.touch()

    def _child_leading(self) -> str:
        for d in self.directives:
            if "\n" in d.leading:
                return "\n" + _indent_of(d.leading)
        base = _indent_of(self.leading)
        return "\n" + base + DEFAULT_INDENT

    def _append(self, d: Directive) -> None:
        if self.block:
            d.leading = self._child_leading()
            if "\n" not in self.closing:
                self.closing = "\n" + _indent_of(self.leading)
        else:
            d.leading = ""
        self.directives.append(d)


@dataclass(eq=False)
class Profile(Node):
    name: str | None = None
    children: list[Node] = field(default_factory=list)
    header_src: str | None = None
    closing: str = ""

    def text(self) -> str:
        if self.header_src is not None:
            header = self.header_src
        else:
            header = f"profile {quote(self.name)} {{" if self.name else "profile {"
        body = "".join(c.render() for c in self.children)
        return header + body + self.closing + "}"

    def rename(self, name: str | None) -> None:
        name = name or None
        if name != self.name:
            self.name = name
            self.header_src = None

    @property
    def outputs(self) -> list[Output]:
        return [c for c in self.children if isinstance(c, Output)]

    @property
    def execs(self) -> list[Exec]:
        return [c for c in self.children if isinstance(c, Exec)]

    def child_indent(self) -> str:
        for c in self.children:
            if "\n" in c.leading:
                return _indent_of(c.leading)
        return _indent_of(self.leading) + DEFAULT_INDENT

    def add(self, node: Node, after: Node | None = None) -> Node:
        """Insert a child, after ``after`` or after the last child of its kind."""
        node.leading = "\n" + self.child_indent()
        if "\n" not in self.closing:
            self.closing = "\n" + _indent_of(self.leading)
        if after is None:
            # Outputs go after the last output, execs at the end.
            same = [i for i, c in enumerate(self.children) if type(c) is type(node)]
            if isinstance(node, Output) and same:
                index = same[-1] + 1
            elif isinstance(node, Output):
                index = 0
            else:
                index = len(self.children)
        else:
            index = self.children.index(after) + 1
        self.children.insert(index, node)
        return node

    def remove(self, node: Node) -> None:
        index = self.children.index(node)
        del self.children[index]
        # Keep a blank-line gap that separated the removed node from the next one.
        if index < len(self.children):
            gap, _ = split_leading(node.leading)
            nxt = self.children[index]
            ngap, nattached = split_leading(nxt.leading)
            if gap.count("\n") > ngap.count("\n"):
                nxt.leading = gap + nattached


@dataclass(eq=False)
class Document:
    children: list[Node] = field(default_factory=list)
    trailing: str = ""

    def render(self) -> str:
        return "".join(c.render() for c in self.children) + self.trailing

    @property
    def profiles(self) -> list[Profile]:
        return [c for c in self.children if isinstance(c, Profile)]

    @property
    def defaults(self) -> list[Output]:
        """Global ``output`` directives: defaults and aliases."""
        return [c for c in self.children if isinstance(c, Output)]

    # -- profile list operations (comments directly above a profile travel with it)

    def add_profile(self, profile: Profile, after: Profile | None = None) -> Profile:
        if after is None:
            index = len(self.children)
        else:
            index = self.children.index(after) + 1
        gap = "\n\n" if self.children else ""
        _, attached = split_leading(profile.leading)
        profile.leading = gap + attached
        if index < len(self.children):
            # Inserted in the middle: the new profile takes the gap of the node
            # that was there, and that node gets a fresh blank line.
            nxt = self.children[index]
            ngap, nattached = split_leading(nxt.leading)
            profile.leading = ngap + attached
            nxt.leading = "\n\n" + nattached
        if not self.trailing and index == len(self.children):
            self.trailing = "\n"
        self.children.insert(index, profile)
        return profile

    def remove_profile(self, profile: Profile) -> None:
        index = self.children.index(profile)
        del self.children[index]
        if index < len(self.children):
            # The next node inherits the removed node's gap (e.g. the file header).
            gap, _ = split_leading(profile.leading)
            nxt = self.children[index]
            _, nattached = split_leading(nxt.leading)
            nxt.leading = gap + nattached

    def move_profile(self, profile: Profile, delta: int) -> bool:
        """Swap with the previous (-1) or next (+1) profile. Gaps stay in place."""
        profiles = self.profiles
        i = profiles.index(profile)
        j = i + delta
        if not 0 <= j < len(profiles):
            return False
        other = profiles[j]
        a, b = self.children.index(profile), self.children.index(other)
        agap, aatt = split_leading(profile.leading)
        bgap, batt = split_leading(other.leading)
        profile.leading, other.leading = bgap + aatt, agap + batt
        self.children[a], self.children[b] = other, profile
        return True


# --------------------------------------------------------------------------- parser


class _Parser:
    def __init__(self, src: str):
        self.s = src
        self.n = len(src)
        self.i = 0

    # -- low level

    def error(self, message: str, pos: int | None = None) -> ParseError:
        return ParseError(message, self.s, self.i if pos is None else pos)

    def peek(self) -> str:
        return self.s[self.i] if self.i < self.n else ""

    def skip_trivia(self, newlines: bool) -> None:
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c in " \t\r":
                self.i += 1
            elif c == "\n" and newlines:
                self.i += 1
            elif c == "#":
                while self.i < n and s[self.i] != "\n":
                    self.i += 1
            elif c == "\\" and self.i + 1 < n and s[self.i + 1] == "\n":
                self.i += 2  # line continuation
            else:
                break

    def at_eol(self) -> bool:
        """True if only trivia remains on this line (does not consume)."""
        save = self.i
        self.skip_trivia(newlines=False)
        c = self.peek()
        self.i = save
        return c in ("", "\n", "}")

    def trailer(self) -> str:
        """Consume same-line trivia up to (not including) the newline, if the line ends there."""
        save = self.i
        self.skip_trivia(newlines=False)
        if self.peek() in ("\n", ""):
            return self.s[save : self.i]
        self.i = save
        return ""

    def word(self) -> str:
        """Read one argument: a bare word or a quoted string."""
        s, n = self.s, self.n
        if self.peek() == '"':
            start = self.i
            self.i += 1
            out = []
            while True:
                if self.i >= n:
                    raise self.error("unterminated quoted string", start)
                c = s[self.i]
                if c == "\\" and self.i + 1 < n:
                    out.append(s[self.i + 1])
                    self.i += 2
                elif c == '"':
                    self.i += 1
                    return "".join(out)
                elif c == "\n":
                    raise self.error("unterminated quoted string", start)
                else:
                    out.append(c)
                    self.i += 1
        start = self.i
        while self.i < n and s[self.i] not in ' \t\r\n"{}#':
            self.i += 1
        if self.i == start:
            raise self.error(f"expected a word, found {self.peek()!r}" if self.peek() else "unexpected end of file")
        return s[start : self.i]

    def rest_of_line(self) -> str:
        start = self.i
        while self.i < self.n and self.s[self.i] != "\n":
            self.i += 1
        return self.s[start : self.i].rstrip(" \t\r")

    def _end_rest_of_line(self, start: int) -> None:
        # Leave trailing whitespace for the next node's leading.
        self.i = start + len(self.s[start : self.i].rstrip(" \t\r"))

    # -- grammar

    def parse(self) -> Document:
        doc = Document()
        while True:
            start = self.i
            self.skip_trivia(newlines=True)
            leading = self.s[start : self.i]
            if self.i >= self.n:
                doc.trailing = leading
                return doc
            node = self.top_level()
            node.leading = leading
            node.trailer = self.trailer()
            doc.children.append(node)

    def top_level(self) -> Node:
        start = self.i
        if self.peek() in ('"', "{", "}"):
            return self.opaque(start)
        save = self.i
        kw = self.word()
        if kw == "profile":
            return self.profile(start)
        if kw == "output":
            return self.output(start, kw)
        if kw == "include":
            self.skip_trivia(newlines=False)
            path = self.rest_of_line()
            self._end_rest_of_line(start)
            return Include(path=path, src=self.s[start : self.i])
        self.i = save
        return self.opaque(start)

    def opaque(self, start: int) -> Opaque:
        """Consume a line we do not understand, plus any block it opens."""
        depth = 0
        while self.i < self.n:
            c = self.s[self.i]
            if c == '"':
                self.word()
                continue
            if c == "#":
                if depth == 0:
                    break
                self.skip_trivia(newlines=False)
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if depth == 0:
                    break
                depth -= 1
                if depth == 0:
                    self.i += 1
                    break
            elif c == "\n" and depth == 0:
                break
            self.i += 1
        end = start + len(self.s[start : self.i].rstrip(" \t\r"))
        self.i = end
        return Opaque(src=self.s[start:end])

    def profile(self, start: int) -> Profile:
        self.skip_trivia(newlines=False)
        name = None
        if self.peek() != "{":
            name = self.word()
            self.skip_trivia(newlines=False)
        if self.peek() != "{":
            raise self.error("expected '{' after profile name")
        self.i += 1
        prof = Profile(name=name, header_src=self.s[start : self.i])
        while True:
            cstart = self.i
            self.skip_trivia(newlines=True)
            leading = self.s[cstart : self.i]
            c = self.peek()
            if c == "":
                raise self.error("unterminated profile: missing '}'", start)
            if c == "}":
                prof.closing = leading
                self.i += 1
                break
            node = self.profile_directive()
            node.leading = leading
            node.trailer = self.trailer()
            prof.children.append(node)
        prof.src = self.s[start : self.i]
        return prof

    def profile_directive(self) -> Node:
        start = self.i
        if self.peek() in ('"', "{"):
            return self.opaque(start)
        save = self.i
        kw = self.word()
        if kw in ("output", "...output"):
            return self.output(start, kw)
        if kw == "exec":
            self.skip_trivia(newlines=False)
            command = self.rest_of_line()
            self._end_rest_of_line(start)
            return Exec(command=command, src=self.s[start : self.i])
        self.i = save
        return self.opaque(start)

    def output(self, start: int, keyword: str) -> Output:
        self.skip_trivia(newlines=False)
        if self.at_eol():
            raise self.error("output needs a criteria")
        criteria = self.word()
        out = Output(keyword=keyword, criteria=criteria)
        self.skip_trivia(newlines=False)
        if self.peek() == "{":
            self.i += 1
            out.block = True
            out.header_src = self.s[start : self.i]
            while True:
                dstart = self.i
                self.skip_trivia(newlines=True)
                leading = self.s[dstart : self.i]
                c = self.peek()
                if c == "":
                    raise self.error("unterminated output block: missing '}'", start)
                if c == "}":
                    out.closing = leading
                    self.i += 1
                    break
                d = self.directive()
                d.leading = leading
                d.trailer = self.trailer()
                out.directives.append(d)
        else:
            while not self.at_eol():
                self.skip_trivia(newlines=False)
                lead_start = self.i
                d = self.directive()
                d.leading = ""
                d.src = self.s[lead_start : self.i]
                out.directives.append(d)
        end = self.i
        out.src = self.s[start:end]
        return out

    def directive(self) -> Directive:
        start = self.i
        key = self.word()
        arity = _ARITY.get(key)
        args: list[str] = []
        if arity is None:
            # Unknown directive: keep the rest of its line as arguments.
            while not self.at_eol():
                self.skip_trivia(newlines=False)
                args.append(self.word())
        else:
            for _ in range(arity):
                self.skip_trivia(newlines=False)
                if self.at_eol():
                    raise self.error(f"'{key}' needs an argument")
                args.append(self.word())
                if key == "mode" and args == ["--custom"]:
                    self.skip_trivia(newlines=False)
                    if self.at_eol():
                        raise self.error("'mode --custom' needs a mode")
                    args.append(self.word())
        return Directive(key=key, args=args, src=self.s[start : self.i])


def parse(src: str) -> Document:
    return _Parser(src).parse()


# --------------------------------------------------------------------------- values


_MODE = re.compile(r"^(\d+)x(\d+)(?:@(\d+(?:\.\d+)?)(?:Hz)?)?$")


@dataclass(frozen=True)
class Mode:
    width: int
    height: int
    refresh: float | None = None  # Hz

    @classmethod
    def parse(cls, text: str) -> Mode | None:
        m = _MODE.match(text.strip())
        if not m:
            return None
        refresh = float(m.group(3)) if m.group(3) else None
        return cls(int(m.group(1)), int(m.group(2)), refresh)

    def __str__(self) -> str:
        if self.refresh is None:
            return f"{self.width}x{self.height}"
        r = f"{self.refresh:.3f}".rstrip("0").rstrip(".")
        return f"{self.width}x{self.height}@{r}Hz"


def parse_position(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    m = re.match(r"^(-?\d+),(-?\d+)$", text.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None
