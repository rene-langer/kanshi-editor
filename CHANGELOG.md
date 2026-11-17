# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-17

First release.

### Added

- Visual editing of kanshi profiles: drag displays into place with edge snapping, nudge
  them with the arrow keys, and set what each display matches, on/off, resolution, scale,
  rotation, position and adaptive sync.
- Profile list in kanshi's matching order: add (empty or from the connected displays),
  duplicate, rename, reorder and delete. Edit the commands a profile runs.
- The active profile is marked: *Active* when `kanshictl status` reports it, *Expected*
  (the first matching profile) when kanshi isn't running.
- Live screen miniatures (via `grim`) and connector badges tell identical monitors apart.
  Can be turned off with *Show Screen Contents*.
- A remembered-monitor database, so profiles for disconnected monitors keep their sizes and
  modes.
- Warnings for overlapping or unreachable displays, duplicates and unknown sizes.
- Lossless editing: comments and formatting are kept, and only edited lines change.
  *Show Changes* displays the diff.
- Saving writes atomically through symlinks, detects changes made by other programs, and
  runs `kanshictl reload`. *Switch Now* runs `kanshictl switch`.
- Unlimited undo and redo.
- Packages: AppImage, Flatpak, deb, rpm, Arch, sdist and wheel, built and install-tested in CI.

[Unreleased]: https://github.com/rene-langer/kanshi-editor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rene-langer/kanshi-editor/releases/tag/v0.1.0
