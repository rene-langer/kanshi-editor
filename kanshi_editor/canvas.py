"""The arrangement view: outputs drawn to scale, dragged with the mouse."""

from __future__ import annotations

import math
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, Gdk, Gtk, Pango, PangoCairo  # noqa: E402

from .config import Output  # noqa: E402
from .layout import Placed, Rect, label, snap  # noqa: E402
from .monitors import Monitor  # noqa: E402

FALLBACK_ACCENT = Gdk.RGBA(red=0.21, green=0.52, blue=0.89, alpha=1.0)  # libadwaita < 1.6

MARGIN = 32  # screen pixels around the layout
SNAP_PX = 12  # snap distance in screen pixels
STRIP_GAP = 40  # gap between the layout and the strip of disabled outputs


class Canvas(Gtk.DrawingArea):
    """Callbacks:

    on_select(output | None)
    on_move(output, x, y) while dragging (live), on_move_done() once at the end
    on_delete() when Delete is pressed with a display selected
    """

    def __init__(self, on_select: Callable, on_move: Callable, on_move_done: Callable, on_delete: Callable):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_can_focus(True)
        self.set_draw_func(self._draw)
        self.on_select = on_select
        self.on_move = on_move
        self.on_move_done = on_move_done
        self.on_delete = on_delete

        self.placed: list[Placed] = []
        self.selected: Output | None = None
        self.problem_outputs: set[int] = set()
        self.assigned: dict[int, Monitor] = {}  # id(output) -> the connected monitor it drives
        self.thumbs: dict = {}  # connector -> cairo.ImageSurface
        self._view: tuple[float, float, float] | None = None  # scale, ox, oy
        self._view_size: tuple[int, int] = (0, 0)
        self._frozen = False  # keep the view still while dragging
        self._drag: tuple[Placed, float, float] | None = None
        self._hit: list[tuple[Rect, Placed]] = []

        drag = Gtk.GestureDrag()
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

        sm = Adw.StyleManager.get_default()
        if hasattr(sm, "get_accent_color_rgba"):
            sm.connect("notify::accent-color", lambda *_: self.queue_draw())
        sm.connect("notify::dark", lambda *_: self.queue_draw())

    # ------------------------------------------------------------------ model

    def set_layout(
        self,
        placed: list[Placed],
        selected: Output | None,
        problems: set[int],
        assigned: dict[int, Monitor] | None = None,
    ) -> None:
        self.placed = placed
        self.selected = selected
        self.problem_outputs = problems
        self.assigned = assigned or {}
        if not self._frozen:
            self._view = None
        self.queue_draw()

    # ------------------------------------------------------------------ view transform

    def _enabled(self) -> list[Placed]:
        return [p for p in self.placed if p.enabled]

    def _disabled(self) -> list[Placed]:
        return [p for p in self.placed if not p.enabled]

    def _compute_view(self, width: int, height: int) -> tuple[float, float, float]:
        on = self._enabled()
        off = self._disabled()
        if on:
            x0 = min(p.rect.x for p in on)
            y0 = min(p.rect.y for p in on)
            x1 = max(p.rect.right for p in on)
            y1 = max(p.rect.bottom for p in on)
        else:
            x0 = y0 = 0
            x1, y1 = 1920, 1080
        # Leave room below for disabled outputs, drawn at a fixed small size.
        strip_h = 0.0
        if off:
            strip_h = max(p.rect.h for p in off) * 0.5
        lw = max(x1 - x0, 1)
        lh = max(y1 - y0, 1)
        avail_w = max(width - 2 * MARGIN, 50)
        avail_h = max(height - 2 * MARGIN - (STRIP_GAP if off else 0), 50)
        scale = min(avail_w / lw, avail_h / (lh + strip_h))
        ox = (width - lw * scale) / 2 - x0 * scale
        used_h = (lh + strip_h) * scale + (STRIP_GAP if off else 0)
        oy = (height - used_h) / 2 - y0 * scale
        return scale, ox, oy

    def _to_screen(self, r: Rect) -> tuple[float, float, float, float]:
        s, ox, oy = self._view
        return ox + r.x * s, oy + r.y * s, r.w * s, r.h * s

    def _strip_rects(self) -> list[tuple[Rect, Placed]]:
        """Screen rects for disabled outputs, in a row under the layout."""
        s, ox, oy = self._view
        on = self._enabled()
        bottom = max((oy + p.rect.bottom * s for p in on), default=oy + 1080 * s)
        left = min((ox + p.rect.x * s for p in on), default=ox)
        out = []
        x = left
        for p in self._disabled():
            w, h = p.rect.w * s * 0.5, p.rect.h * s * 0.5
            out.append((Rect(x, bottom + STRIP_GAP, w, h), p))
            x += w + 16
        return out

    # ------------------------------------------------------------------ drawing

    def _colors(self):
        sm = Adw.StyleManager.get_default()
        accent = sm.get_accent_color_rgba() if hasattr(sm, "get_accent_color_rgba") else FALLBACK_ACCENT
        fg = self.get_color()
        dark = sm.get_dark()
        return accent, fg, dark

    def _draw(self, area, cr, width, height):
        # Recompute the fit when the layout or the widget size changed (but never mid-drag).
        if self._view is None or (self._view_size != (width, height) and not self._frozen):
            self._view = self._compute_view(width, height)
            self._view_size = (width, height)
        accent, fg, dark = self._colors()
        self._hit = []

        # Faint origin cross so negative coordinates and alignment are visible.
        s, ox, oy = self._view
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.08)
        cr.set_line_width(1)
        cr.move_to(ox + 0.5, 0)
        cr.line_to(ox + 0.5, height)
        cr.move_to(0, oy + 0.5)
        cr.line_to(width, oy + 0.5)
        cr.stroke()

        if not self.placed:
            self._centered_text(cr, width, height, "No displays in this profile.\nUse “Add display” to add one.", fg)
            return

        for p in self._enabled():
            x, y, w, h = self._to_screen(p.rect)
            self._draw_output(cr, p, x, y, w, h, accent, fg, dark, disabled=False)
            self._hit.append((Rect(x, y, w, h), p))

        strip = self._strip_rects()
        if strip:
            r0 = strip[0][0]
            self._text(cr, r0.x, r0.y - 22, "Turned off in this profile", fg, 0.55, size=9)
        for r, p in strip:
            self._draw_output(cr, p, r.x, r.y, r.w, r.h, accent, fg, dark, disabled=True)
            self._hit.append((r, p))

    def _draw_output(self, cr, p: Placed, x, y, w, h, accent, fg, dark, disabled: bool):
        selected = p.output is self.selected
        problem = id(p.output) in self.problem_outputs
        inset = 2
        x, y, w, h = x + inset, y + inset, max(w - 2 * inset, 4), max(h - 2 * inset, 4)
        radius = min(8, w / 6, h / 6)
        self._round_rect(cr, x, y, w, h, radius)
        if disabled:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.05)
        elif selected:
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.35 if dark else 0.25)
        else:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.10)
        cr.fill_preserve()

        mon = self.assigned.get(id(p.output))
        thumb = self.thumbs.get(mon.connector) if (mon and not disabled) else None
        if thumb is not None:
            self._paint_thumb(cr, thumb, x, y, w, h, radius, selected)
            self._round_rect(cr, x, y, w, h, radius)

        if problem:
            cr.set_source_rgba(0.88, 0.11, 0.14, 0.9)
            cr.set_line_width(2)
        elif selected:
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
            cr.set_line_width(2.5)
        else:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35)
            cr.set_line_width(1)
        if not p.size_known or disabled:
            cr.set_dash([6, 4])
        cr.stroke()
        cr.set_dash([])

        # Which connector this tile is driving right now, e.g. "DP-3".
        if mon and not disabled and w > 50 and h > 30:
            self._badge(cr, x + 6, y + 6, mon.connector, accent, thumb is not None, fg)

        # Internal panels get a small "laptop" hint along the bottom edge.
        if p.monitor and p.monitor.is_internal and not disabled and h > 30 and thumb is None:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.25)
            cr.rectangle(x + w * 0.4, y + h - 5, w * 0.2, 2)
            cr.fill()

        alpha = 0.5 if disabled else 1.0
        title = label(p)
        if p.output.criteria == "*" and mon is not None and mon.model:
            title = f"{mon.model} (via *)"
        if p.output.criteria == "*" and p.output.keyword == "...output":
            title = "any other displays (...*)"
        lines = [(title, True)]
        if not disabled and h > 60:
            mode = f"{p.mode.width}×{p.mode.height}" + ("" if p.size_known else " (size unknown)")
            if p.mode.refresh:
                mode += f" @ {p.mode.refresh:g} Hz"
            details = [mode]
            if p.eff.scale != 1:
                details.append(f"scale {p.eff.scale:g}")
            if p.eff.transform != "normal":
                details.append(f"rotated {p.eff.transform}")
            lines.append((" · ".join(details), False))
            pos = f"{p.rect.x:g}, {p.rect.y:g}" + ("  (automatic)" if p.auto_position else "")
            lines.append((f"{p.rect.w:g}×{p.rect.h:g} at {pos}", False))
        self._block(cr, x, y, w, h, lines, fg, alpha, backdrop=thumb is not None)

    def _paint_thumb(self, cr, surface, x, y, w, h, radius, selected):
        """Paint a screen capture into the tile, cropped to fill it."""
        sw, sh = surface.get_width(), surface.get_height()
        if sw <= 0 or sh <= 0:
            return
        cr.save()
        self._round_rect(cr, x, y, w, h, radius)
        cr.clip()
        k = max(w / sw, h / sh)
        cr.translate(x + (w - sw * k) / 2, y + (h - sh * k) / 2)
        cr.scale(k, k)
        cr.set_source_surface(surface, 0, 0)
        cr.paint_with_alpha(1.0 if selected else 0.85)
        cr.restore()

    def _badge(self, cr, x, y, text, accent, on_picture, fg):
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(Pango.FontDescription.from_string("Sans Bold 8"))
        lay.set_text(text, -1)
        tw, th = lay.get_pixel_size()
        self._round_rect(cr, x, y, tw + 10, th + 4, (th + 4) / 2)
        if on_picture:
            cr.set_source_rgba(0, 0, 0, 0.65)
        else:
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.25)
        cr.fill()
        if on_picture:
            cr.set_source_rgba(1, 1, 1, 0.95)
        else:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.9)
        cr.move_to(x + 5, y + 2)
        PangoCairo.show_layout(cr, lay)

    def _block(self, cr, x, y, w, h, lines, fg, alpha, backdrop=False):
        layouts = []
        for text, bold in lines:
            lay = PangoCairo.create_layout(cr)
            fd = Pango.FontDescription.from_string("Sans Bold 10" if bold else "Sans 9")
            lay.set_font_description(fd)
            lay.set_text(text, -1)
            lay.set_width(int(max(w - 12, 10) * Pango.SCALE))
            if bold:  # long "Make Model Serial" titles wrap onto a second line
                lay.set_wrap(Pango.WrapMode.WORD_CHAR)
                lay.set_height(-2)
            lay.set_ellipsize(Pango.EllipsizeMode.END)
            lay.set_alignment(Pango.Alignment.CENTER)
            layouts.append((lay, bold))
        total = sum(lay.get_pixel_size()[1] for lay, _ in layouts) + 3 * (len(layouts) - 1)
        if total > h - 4:
            layouts = layouts[:1]
            total = layouts[0][0].get_pixel_size()[1]
        cy = y + (h - total) / 2
        if backdrop:
            # A dark plate under the text keeps it readable over a screen picture.
            widest = max(lay.get_pixel_extents()[1].width for lay, _ in layouts)
            bw, bh = min(widest + 16, w - 8), total + 10
            self._round_rect(cr, x + (w - bw) / 2, cy - 5, bw, bh, 6)
            cr.set_source_rgba(0, 0, 0, 0.6)
            cr.fill()
        for lay, bold in layouts:
            if backdrop:
                cr.set_source_rgba(1, 1, 1, 1 if bold else 0.85)
            else:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, alpha * (1 if bold else 0.7))
            cr.move_to(x + 6, cy)
            PangoCairo.show_layout(cr, lay)
            cy += lay.get_pixel_size()[1] + 3

    def _text(self, cr, x, y, text, fg, alpha, size=10):
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(Pango.FontDescription.from_string(f"Sans {size}"))
        lay.set_text(text, -1)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, alpha)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, lay)

    def _centered_text(self, cr, width, height, text, fg):
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(Pango.FontDescription.from_string("Sans 11"))
        lay.set_alignment(Pango.Alignment.CENTER)
        lay.set_text(text, -1)
        tw, th = lay.get_pixel_size()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.55)
        cr.move_to((width - tw) / 2, (height - th) / 2)
        PangoCairo.show_layout(cr, lay)

    @staticmethod
    def _round_rect(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    # ------------------------------------------------------------------ interaction

    def _hit_test(self, x: float, y: float) -> Placed | None:
        for r, p in reversed(self._hit):
            if r.x <= x <= r.right and r.y <= y <= r.bottom:
                return p
        return None

    def _drag_begin(self, gesture, x, y):
        self.grab_focus()
        p = self._hit_test(x, y)
        self.on_select(p.output if p else None)
        if p is not None and p.enabled:
            self._drag = (p, p.rect.x, p.rect.y)
            self._frozen = True
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        else:
            self._drag = None

    def _drag_update(self, gesture, dx, dy):
        if not self._drag or not self._view:
            return
        p, sx, sy = self._drag
        s = self._view[0]
        moving = Rect(sx + dx / s, sy + dy / s, p.rect.w, p.rect.h)
        state = gesture.get_current_event_state()
        if state & Gdk.ModifierType.SHIFT_MASK:
            nx, ny = round(moving.x), round(moving.y)  # Shift: no snapping
        else:
            others = [o.rect for o in self._enabled() if o is not p]
            nx, ny = snap(moving, others, SNAP_PX / s)
        if (nx, ny) != (p.rect.x, p.rect.y):
            self.on_move(p.output, nx, ny)

    def _drag_end(self, gesture, dx, dy):
        if self._drag:
            self._drag = None
            self._frozen = False
            self._view = None
            self.on_move_done()
            self.queue_draw()

    def _key(self, ctrl, keyval, keycode, state):
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete) and self.selected is not None:
            self.on_delete()
            return True
        p = next((q for q in self.placed if q.output is self.selected and q.enabled), None)
        if p is None:
            return False
        step = 100 if state & Gdk.ModifierType.SHIFT_MASK else 10 if state & Gdk.ModifierType.CONTROL_MASK else 1
        delta = {
            Gdk.KEY_Left: (-step, 0),
            Gdk.KEY_Right: (step, 0),
            Gdk.KEY_Up: (0, -step),
            Gdk.KEY_Down: (0, step),
        }.get(keyval)
        if not delta:
            return False
        self.on_move(p.output, int(p.rect.x + delta[0]), int(p.rect.y + delta[1]))
        self.on_move_done()
        return True
