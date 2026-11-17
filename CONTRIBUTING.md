# Contributing

Thanks for helping. Bug reports, ideas and pull requests are all welcome.

## Reporting a bug

Open an [issue](https://github.com/rene-langer/kanshi-editor/issues/new/choose) and include:

- how you installed Kanshi Editor (AppImage, Flatpak, deb, rpm, Arch or source) and its
  version (`kanshi-editor --version`);
- your compositor and kanshi version;
- the profile that misbehaves, if it's about your config. Replace serial numbers if you prefer.

If the editor changed something in your file that you didn't edit, that's a serious bug.
Please attach the file before and after (or the *Show Changes* diff).

## Development setup

Everything comes from your distro; there is no virtualenv or pip step.

```sh
sudo sh packaging/ci/install-deps.sh   # Fedora, Debian/Ubuntu or Arch: GTK, libadwaita, PyGObject, Xvfb
./bin/kanshi-editor                     # runs straight from the checkout
```

### Demo mode: no monitors needed

`docs/demo/` holds a fake desk (two identical monitors and a laptop) with fake
`swaymsg`, `kanshictl` and `grim` output:

```sh
cp docs/demo/config /tmp/demo.conf
KANSHI_EDITOR_DEMO=docs/demo XDG_STATE_HOME=/tmp/demo-state ./bin/kanshi-editor /tmp/demo.conf
```

Point it at a copy, as above. The editor saves to whatever file it opens.

## Tests and lint

```sh
python3 -m unittest discover -s tests -t . -v            # window tests run when a display is available
xvfb-run -a python3 -m unittest discover -s tests -t .    # headless, like CI (GDK_BACKEND=x11)
pipx run ruff==0.17.3 check . && pipx run ruff==0.17.3 format --check .
```

CI runs the tests on Ubuntu 24.04 (the oldest supported libadwaita, 1.5), Debian 13,
Fedora and Arch. It then builds every package and installs each on the distros it targets.

Things worth a test:

- **Parser changes:** a round trip (`parse(src).render() == src`) and a test that an edit
  changes only the intended line.
- **Layout and matching:** a case in `tests/test_layout.py`.
- **UI behaviour:** `tests/test_window.py` drives the real window. Follow its pattern.

## Code tour

See [AGENTS.md](AGENTS.md) for the module layout and the rules that keep the editor
trustworthy. In short: never reformat text the user didn't edit, send every edit through
`Window.changed()`, run host tools through `kanshi_editor/host.py`, and keep supporting
libadwaita 1.5.

## Pull requests

- Keep each pull request focused on one thing, and describe what changed and why.
- Add or update tests, and an entry under *Unreleased* in `CHANGELOG.md`.
- For UI changes, attach a screenshot. `sh docs/demo/screenshots.sh` makes clean ones and
  refreshes `docs/screenshots/` when the main window changes.
- By contributing you agree that your work is released under the [MIT license](LICENSE).

## Packaging

| Target | Command | Notes |
|---|---|---|
| deb, rpm, Arch | `make deb rpm archlinux` | nfpm from `packaging/nfpm.yaml`, using podman/docker if nfpm isn't installed |
| AppImage | `make appimage` | appimage-builder in a `debian:trixie` container, see `packaging/appimage/` |
| Flatpak | `make flatpak` | needs `flatpak-builder` and Flathub's GNOME 51 SDK |
| sdist and wheel | `make sdist` | needs `python3 -m build` |

`packaging/smoke-test.sh` and `packaging/appimage/test.sh` install or run a built
package in a bare container and render the window. CI uses the same scripts.

## Releasing (maintainers)

1. Set `__version__` in `kanshi_editor/__init__.py`.
2. Add a `<release version="X.Y.Z" date="YYYY-MM-DD">` entry to
   `data/io.github.rene_langer.KanshiEditor.metainfo.xml`.
3. In `CHANGELOG.md`, move *Unreleased* entries under `## [X.Y.Z] - YYYY-MM-DD` and update
   the links at the bottom.
4. Commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`.

`release.yml` checks that all three agree, builds and tests every package, and publishes
a GitHub release with the changelog section as its notes, plus `SHA256SUMS`.
