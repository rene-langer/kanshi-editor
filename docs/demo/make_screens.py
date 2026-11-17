"""Draw fake screen contents for the demo (python3 docs/demo/make_screens.py)."""

import math
from pathlib import Path

import cairo

OUT = Path(__file__).parent / "screens"


def screen(name, w, h, hue, windows):
    s = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    c = cairo.Context(s)
    g = cairo.LinearGradient(0, 0, w, h)
    r, gg, b = hue
    g.add_color_stop_rgb(0, r, gg, b)
    g.add_color_stop_rgb(1, r * 0.35, gg * 0.35, b * 0.5)
    c.set_source(g)
    c.paint()
    c.set_source_rgba(0, 0, 0, 0.55)  # top bar
    c.rectangle(0, 0, w, h * 0.05)
    c.fill()
    for x, y, ww, hh, kind in windows:
        x, y, ww, hh = x * w, y * h, ww * w, hh * h
        c.set_source_rgba(0, 0, 0, 0.3)
        c.rectangle(x + 3, y + 4, ww, hh)
        c.fill()
        c.set_source_rgb(*((0.12, 0.12, 0.14) if kind == "term" else (0.96, 0.96, 0.97)))
        c.rectangle(x, y, ww, hh)
        c.fill()
        c.set_source_rgb(*((0.2, 0.2, 0.23) if kind == "term" else (0.85, 0.86, 0.88)))
        c.rectangle(x, y, ww, hh * 0.08)
        c.fill()
        lines = int(hh / 9)
        for i in range(1, lines):
            ly = y + hh * 0.08 + i * 8
            if ly > y + hh - 6:
                break
            length = ww * (0.3 + 0.6 * abs(math.sin(i * 1.7 + x)))
            if kind == "term":
                c.set_source_rgb(0.35, 0.85, 0.45) if i % 4 == 0 else c.set_source_rgb(0.7, 0.7, 0.72)
            else:
                c.set_source_rgb(0.2, 0.4, 0.8) if i % 5 == 0 else c.set_source_rgb(0.55, 0.56, 0.6)
            c.rectangle(x + 8, ly, length - 16, 3)
            c.fill()
    s.write_to_png(str(OUT / f"{name}.png"))


OUT.mkdir(exist_ok=True)
screen("DP-5", 320, 180, (0.2, 0.45, 0.85), [(0.04, 0.09, 0.44, 0.86, "term"), (0.52, 0.09, 0.44, 0.86, "term")])
screen("DP-6", 320, 180, (0.85, 0.45, 0.2), [(0.06, 0.09, 0.88, 0.86, "web")])
screen("eDP-1", 320, 200, (0.45, 0.25, 0.75), [(0.1, 0.12, 0.8, 0.8, "web")])
