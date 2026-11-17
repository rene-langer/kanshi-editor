#!/bin/sh
# Run the AppImage on a bare distro container (no GTK installed) and render a window.
# Usage: sh packaging/appimage/test.sh dist/kanshi-editor-*-x86_64.AppImage
set -eu
img="$1"
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq --no-install-recommends xvfb xauth >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q xorg-x11-server-Xvfb xorg-x11-xauth >/dev/null
elif command -v pacman >/dev/null; then
  pacman -Syu --noconfirm --needed xorg-server-xvfb xorg-xauth >/dev/null
fi
chmod +x "$img"
# Containers have no FUSE, so let the AppImage unpack itself.
export APPIMAGE_EXTRACT_AND_RUN=1
"$img" --version
cfg="$(mktemp -d)"
cp docs/demo/config "$cfg/config"
HOME="$cfg" GDK_BACKEND=x11 KANSHI_EDITOR_DEMO="$PWD/docs/demo" KANSHI_EDITOR_SCREENSHOT="$cfg/shot.png" \
  timeout 120 xvfb-run -a -s "-screen 0 1400x900x24" "$img" "$cfg/config"
test -s "$cfg/shot.png"
cp "$cfg/shot.png" "build/appimage-$(. /etc/os-release && echo "$ID-${VERSION_ID:-rolling}").png" 2>/dev/null || true
echo "AppImage test passed on $(. /etc/os-release && echo "$PRETTY_NAME")"
