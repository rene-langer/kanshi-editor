# Demo setup

A made-up desk used for screenshots, the AppImage and package smoke tests, and for
trying the editor without the hardware: two identical LG monitors plus a laptop.

- `config`: the kanshi configuration that gets opened.
- `outputs.json`: what `swaymsg -t get_outputs` would print.
- `status.txt`: what `kanshictl status` would print.
- `screens/*.png`: what each screen shows, as `grim` would capture it
  (regenerate with `python3 docs/demo/make_screens.py`).

Run the editor against it, without touching your real setup:

```sh
cp docs/demo/config /tmp/demo.conf
KANSHI_EDITOR_DEMO=docs/demo XDG_STATE_HOME=/tmp/demo-state ./bin/kanshi-editor /tmp/demo.conf
```

`make screenshots` (or `sh docs/demo/screenshots.sh`) regenerates
`docs/screenshots/` in a private headless sway.
