"""Geometry of a profile: where each output sits in layout (logical) pixels."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Document, Mode, Output, Profile, parse_position
from .monitors import Monitor, MonitorDB

PLACEHOLDER = Mode(1920, 1080)
ROTATED = {"90", "270", "flipped-90", "flipped-270"}


@dataclass
class Effective:
    """An output's settings after applying global defaults and aliases."""

    criteria: str  # alias resolved
    enabled: bool
    mode: Mode | None
    mode_preferred: bool
    position: tuple[int, int] | None
    scale: float
    transform: str
    adaptive_sync: str | None
    inherited: set[str] = field(default_factory=set)  # keys that came from defaults


def resolve(doc: Document, out: Output) -> Effective:
    criteria = out.criteria
    defaults: list[Output] = []
    for d in doc.defaults:
        if d.criteria == out.criteria or (out.criteria.startswith("$") and d.arg("alias") == out.criteria):
            defaults.append(d)
            if d.arg("alias") == out.criteria:
                criteria = d.criteria
    values: dict[str, list[str]] = {}
    inherited: set[str] = set()
    for src in [*defaults, out]:
        for directive in src.directives:
            key = directive.key
            if key in ("enable", "disable"):
                key = "enabled"
            if key == "alias":
                continue
            values[key] = [directive.key, *directive.args]
            if src is out:
                inherited.discard(key)
            else:
                inherited.add(key)

    def last(key: str) -> str | None:
        v = values.get(key)
        return v[-1] if v and len(v) > 1 else None

    mode_text = last("mode")
    scale_text = last("scale")
    try:
        scale = float(scale_text) if scale_text else 1.0
    except ValueError:
        scale = 1.0
    return Effective(
        criteria=criteria,
        enabled=values.get("enabled", ["enable"])[0] == "enable",
        mode=Mode.parse(mode_text) if mode_text and mode_text != "preferred" else None,
        mode_preferred=mode_text in (None, "preferred"),
        position=parse_position(last("position")),
        scale=scale if scale > 0 else 1.0,
        transform=last("transform") or "normal",
        adaptive_sync=last("adaptive_sync"),
        inherited=inherited,
    )


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def overlaps(self, o: Rect) -> bool:
        return self.x < o.right and o.x < self.right and self.y < o.bottom and o.y < self.bottom

    def touches(self, o: Rect) -> bool:
        """Shares a stretch of edge (corners alone do not count)."""
        h_edge = (self.right == o.x or o.right == self.x) and self.y < o.bottom and o.y < self.bottom
        v_edge = (self.bottom == o.y or o.bottom == self.y) and self.x < o.right and o.x < self.right
        return h_edge or v_edge


@dataclass
class Placed:
    output: Output
    eff: Effective
    monitor: Monitor | None
    mode: Mode  # the mode used for drawing (placeholder if unknown)
    size_known: bool
    rect: Rect
    auto_position: bool  # no position directive; placed the way sway would

    @property
    def enabled(self) -> bool:
        return self.eff.enabled


def logical_size(mode: Mode, scale: float, transform: str) -> tuple[float, float]:
    w, h = mode.width, mode.height
    if transform in ROTATED:
        w, h = h, w
    return round(w / scale), round(h / scale)


def place(doc: Document, profile: Profile, db: MonitorDB) -> list[Placed]:
    placed: list[Placed] = []
    for out in profile.outputs:
        eff = resolve(doc, out)
        mon = db.find(eff.criteria)
        mode = eff.mode
        known = mode is not None
        if mode is None and mon is not None and mon.best_mode is not None:
            mode, known = mon.best_mode, True
        if mode is None:
            mode = PLACEHOLDER
        w, h = logical_size(mode, eff.scale, eff.transform)
        x, y = eff.position if eff.position else (0, 0)
        placed.append(Placed(out, eff, mon, mode, known, Rect(x, y, w, h), eff.position is None))
    # Outputs without a position go to the right of everything else, top-aligned,
    # which is what sway does with unpositioned outputs.
    fixed = [p for p in placed if not p.auto_position and p.enabled]
    edge = max((p.rect.right for p in fixed), default=0)
    for p in placed:
        if p.auto_position and p.enabled:
            p.rect.x, p.rect.y = edge, 0
            edge += p.rect.w
    return placed


def snap(rect: Rect, others: list[Rect], threshold: float) -> tuple[float, float]:
    """Snap rect's position to edges of the other rects, per axis."""

    def best(value: float, size: float, edges: list[tuple[float, float]]) -> float:
        candidates = []
        for lo, hi in edges:
            candidates += [lo - size, hi, lo, hi - size]
        close = [(abs(c - value), c) for c in candidates if abs(c - value) <= threshold]
        return min(close)[1] if close else value

    x = best(rect.x, rect.w, [(o.x, o.right) for o in others])
    y = best(rect.y, rect.h, [(o.y, o.bottom) for o in others])
    return round(x), round(y)


def issues(placed: list[Placed], profile: Profile) -> list[tuple[Output | None, str]]:
    """Problems worth showing: things kanshi or sway will not like."""
    found: list[tuple[Output | None, str]] = []
    on = [p for p in placed if p.enabled]
    for i, a in enumerate(on):
        for b in on[i + 1 :]:
            if a.rect.overlaps(b.rect):
                found.append((a.output, f"{label(a)} overlaps {label(b)}."))
    if len(on) > 1:
        for p in on:
            if not any(p.rect.touches(o.rect) for o in on if o is not p):
                found.append((p.output, f"{label(p)} doesn't touch any other display, so the cursor can't move onto it."))
    seen: dict[str, Output] = {}
    for out in profile.outputs:
        if out.criteria in seen and out.criteria != "*":
            found.append((out, f"{out.criteria} is listed twice."))
        seen[out.criteria] = out
    for p in placed:
        if not p.size_known and p.output.criteria != "*":
            found.append((p.output, f"Size of {label(p)} is unknown: set a mode, or connect it once so the editor learns it."))
    if not profile.outputs:
        found.append((None, "This profile has no outputs, so kanshi can never match it."))
    return found


def criteria_matches(criteria: str, monitor: Monitor) -> bool:
    import fnmatch

    if criteria == "*":
        return True
    if criteria == monitor.connector or criteria == monitor.description:
        return True
    return any(c in criteria for c in "*?[") and fnmatch.fnmatchcase(monitor.description, criteria)


def matches_connected(doc: Document, profile: Profile, connected: list[Monitor]) -> bool:
    """Would kanshi accept this profile for these monitors (ignoring profile order)?"""
    return assign_connected(doc, profile, connected) is not None


def assign_connected(doc: Document, profile: Profile, connected: list[Monitor]) -> dict[int, Monitor] | None:
    """Which connected monitor each output of the profile would drive.

    Returns {id(output): monitor}, or None if the profile does not match. Every
    output in the profile needs its own connected monitor, and every connected
    monitor must be claimed, unless a ``...output`` takes the rest. This is how
    two identical monitors on one desk are told apart.
    """
    if not connected:
        return None
    singles = [o for o in profile.outputs if o.keyword == "output"]
    multis = [o for o in profile.outputs if o.keyword == "...output"]
    single = [resolve(doc, o).criteria for o in singles]
    multi = [resolve(doc, o).criteria for o in multis]
    # Bipartite matching (tiny sizes, so simple augmenting paths are plenty).
    owner: dict[int, int] = {}

    def assign(i: int, seen: set[int]) -> bool:
        for j, m in enumerate(connected):
            if j in seen or not criteria_matches(single[i], m):
                continue
            seen.add(j)
            if j not in owner or assign(owner[j], seen):
                owner[j] = i
                return True
        return False

    if not all(assign(i, set()) for i in range(len(single))):
        return None
    result = {id(singles[i]): connected[j] for j, i in owner.items()}
    for j, m in enumerate(connected):
        if j in owner:
            continue
        taker = next((o for o, c in zip(multis, multi, strict=True) if criteria_matches(c, m)), None)
        if taker is None:
            return None
        result.setdefault(id(taker), m)  # a ...output may take several; show the first
    return result


def predicted_profile(doc: Document, connected: list[Monitor]) -> Profile | None:
    """The profile kanshi picks on hotplug: the first one that matches."""
    return next((p for p in doc.profiles if matches_connected(doc, p, connected)), None)


def label(p: Placed) -> str:
    c = p.eff.criteria
    if c == "*":
        return "any display (*)"
    if p.monitor and p.monitor.model:
        return p.monitor.model
    return c
