#!/bin/sh
# Regenerate docs/screenshots/*.png from the demo setup in docs/demo.
# Runs the app in a private headless sway at a fixed size, with a throwaway $HOME,
# so the pictures never show anything from your own desktop.  Needs sway.
set -eu
repo="$(cd "$(dirname "$0")/../.." && pwd)"
runtime="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/ke-shots-XXXX")"   # short path: sockets have a length limit
home="$(mktemp -d)"
trap 'kill "$sway" 2>/dev/null || true; rm -rf "$runtime" "$home"' EXIT
chmod 700 "$runtime"

printf 'output HEADLESS-1 resolution 1400x880 scale 1\ndefault_border none\n' > "$home/sway.conf"
mkdir -p "$home/.config/kanshi"
cp "$repo/docs/demo/config" "$home/.config/kanshi/config"

env -i HOME="$home" PATH="$PATH" XDG_RUNTIME_DIR="$runtime" WLR_BACKENDS=headless \
  WLR_LIBINPUT_NO_DEVICES=1 WLR_RENDERER=pixman sway -c "$home/sway.conf" >/dev/null 2>&1 &
sway=$!
for _ in $(seq 1 100); do
  display="$(ls "$runtime" | grep -E '^wayland-[0-9]+$' | head -n1 || true)"
  [ -n "$display" ] && break
  timeout 0.1 tail -f /dev/null 2>/dev/null || true   # short wait without sleep(1)
done
[ -n "$display" ] || { echo "headless sway did not start" >&2; exit 1; }

shot() {  # shot NAME PROFILE:OUTPUT [color-scheme]
  env -i HOME="$home" PATH="$PATH" XDG_RUNTIME_DIR="$runtime" WAYLAND_DISPLAY="$display" \
    XDG_STATE_HOME="$home/.local/state" KANSHI_EDITOR_DEMO="$repo/docs/demo" \
    KANSHI_EDITOR_SELECT="$2" KANSHI_EDITOR_SCREENSHOT="$repo/docs/screenshots/$1.png" \
    ADW_DEBUG_COLOR_SCHEME="${3:-prefer-dark}" \
    "$repo/bin/kanshi-editor"
  echo "docs/screenshots/$1.png"
}
mkdir -p "$repo/docs/screenshots"
shot desk 0:1
shot docked 1:1
shot desk-light 0:0 prefer-light
