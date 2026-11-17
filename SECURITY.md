# Security policy

## Supported versions

Only the latest release gets security fixes.

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately through GitHub's
[private vulnerability reporting](https://github.com/rene-langer/kanshi-editor/security/advisories/new).
Expect an answer within a week. Once a fix is released, you'll be credited unless you'd
rather not be.

## What the app does on your system

To judge an issue, it helps to know what Kanshi Editor touches:

- It reads and writes the kanshi config you open, following symlinks, and writes files in
  `~/.local/state/kanshi-editor/`.
- It runs `swaymsg`, `wlr-randr`, `kanshictl` and `grim` with fixed arguments. Nothing
  from the config file is passed to a shell. `exec` commands in your config are saved as
  text and run by kanshi, never by the editor.
- Screen captures stay in memory.
- The Flatpak can read and write your home directory and run commands on the host
  (`org.freedesktop.Flatpak`). This is needed to reach the tools above and dotfiles
  symlinks; see the README.
