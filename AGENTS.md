# AGENTS.md

Guidance for AI coding agents (and humans in a hurry) working on Kanshi Editor.

## What this is

A GTK 4 / libadwaita desktop app in plain Python that edits kanshi(5) configuration
files visually. It has no runtime dependencies from pip: PyGObject, pycairo, GTK and
libadwaita come from the distro, Flatpak runtime or AppImage bundle.

## Layout

| Path | What lives there |
|---|---|
| `kanshi_editor/config.py` | Lossless parser and editing model for kanshi configs. No GTK. |
| `kanshi_editor/layout.py` | Geometry, defaults/alias resolution, snapping, issues, profile matching. No GTK. |
| `kanshi_editor/monitors.py` | Connected-monitor detection (swaymsg / wlr-randr) and the remembered-monitor DB. No GTK. |
| `kanshi_editor/host.py` | Running host tools: directly, through `flatpak-spawn --host`, or faked by demo mode. |
| `kanshi_editor/thumbnails.py` | Async `grim` screen captures, kept in memory. |
| `kanshi_editor/canvas.py` | The drawing and drag area (cairo). |
| `kanshi_editor/window.py` | Main window: sidebar, panel, history, saving, polling. |
| `kanshi_editor/app.py` | `Adw.Application`, CSS, the screenshot hook. |
| `bin/kanshi-editor` | Relocatable launcher (checkout, `/usr`, `/app`, AppImage). |
| `tests/` | `unittest` tests. `test_window.py` drives the real window and skips without a display. |
| `docs/demo/` | Fake hardware for screenshots, smoke tests and development. |
| `packaging/` | nfpm (deb/rpm/Arch), Flatpak manifest, AppImage recipe, CI helper scripts. |
| `.github/workflows/` | `ci.yml` → `build.yml` (reusable) → `release.yml` on `v*` tags. |

## Rules that matter

1. **Never reformat what the user did not edit.** Untouched config text must render
   byte for byte (`parse(src).render() == src`). Each node has `leading`, `src` and
   `trailer`. Edit through the model's methods (`Output.set`, `Profile.add`,
   `Document.move_profile`, …), which regenerate only the node they change. Add a
   round-trip test for any new syntax.
2. **Every edit goes through `Window.changed()`.** That one call creates the undo step and
   refreshes the UI. Live drags call `_canvas_move` and commit once in `_canvas_move_done`.
3. **Run external tools through `host.run` / `host.argv` / `host.which`,** never
   `subprocess` directly. Otherwise the Flatpak and demo mode break.
4. **Keep `config.py`, `layout.py` and `monitors.py` free of GTK** so they stay fast to test.
5. **Support libadwaita 1.5 (Ubuntu 24.04).** Guard newer APIs with `hasattr` and give
   them a fallback. `Adw.ButtonRow` and the accent colour are examples.
6. **No pip dependencies.** Anything new must be packaged by Debian, Fedora, Arch and the
   GNOME Flatpak runtime, and be added to `packaging/nfpm.yaml`,
   `packaging/ci/install-deps.sh` and the AppImage recipe.
7. **Saving writes through symlinks** (`os.path.realpath`) atomically, and refuses to
   overwrite a file changed on disk. Keep both behaviours.

## Commands

```sh
python3 -m unittest discover -s tests -t .           # all tests (window tests need a display)
pipx run ruff==0.17.3 check . && pipx run ruff==0.17.3 format --check .
./bin/kanshi-editor docs/demo/config                  # careful: edits the file you open
KANSHI_EDITOR_DEMO=docs/demo XDG_STATE_HOME=/tmp/ke ./bin/kanshi-editor /tmp/copy.conf
sh docs/demo/screenshots.sh                            # regenerate docs/screenshots (needs sway)
make deb rpm archlinux | make appimage | make flatpak  # packages (podman/docker for nfpm/AppImage)
```

Debug hooks: `KANSHI_EDITOR_SCREENSHOT=out.png` renders the window to a PNG, then quits
with a non-zero status if any Python exception happened. `KANSHI_EDITOR_SELECT=P:O` picks
the profile and output indices to select first.

## Conventions

- Python 3.10+, ruff for linting and formatting (config in `pyproject.toml`), line length 130.
- UI text: sentence case for labels and descriptions, title case for buttons and menu items
  (GNOME HIG). Address the user as "you", and keep messages short and specific.
- Update `CHANGELOG.md` (Unreleased section) in the same change as the code.
- A release bumps `kanshi_editor/__version__`, adds a `<release>` to the metainfo, moves the
  changelog entries under the new version, and tags `vX.Y.Z`. `release.yml` checks all three.
