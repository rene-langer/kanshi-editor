#!/bin/sh
# Install a built package with the distro's own package manager and check it runs.
# Usage (inside a container of the target distro): sh packaging/smoke-test.sh dist/<package>
set -eu
pkg="$1"
case "$pkg" in
  *.deb)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq "./$pkg" xvfb xauth >/dev/null ;;
  *.rpm)
    dnf install -y -q "./$pkg" xorg-x11-server-Xvfb xorg-x11-xauth >/dev/null ;;
  *.pkg.tar.zst)
    pacman -Syu --noconfirm --needed xorg-server-xvfb xorg-xauth >/dev/null
    pacman -U --noconfirm "./$pkg" >/dev/null ;;
  *) echo "unknown package type: $pkg" >&2; exit 2 ;;
esac

kanshi-editor --version
test -f /usr/share/applications/io.github.rene_langer.KanshiEditor.desktop
test -f /usr/share/metainfo/io.github.rene_langer.KanshiEditor.metainfo.xml

# Open the real window on a virtual X display, render it, and quit.
cfg="$(mktemp -d)"
cp docs/demo/config "$cfg/config"
HOME="$cfg" GDK_BACKEND=x11 KANSHI_EDITOR_DEMO="$PWD/docs/demo" KANSHI_EDITOR_SCREENSHOT="$cfg/shot.png" \
  timeout 60 xvfb-run -a -s "-screen 0 1400x900x24" kanshi-editor "$cfg/config"
test -s "$cfg/shot.png"
[ "$(sha256sum < docs/demo/config)" = "$(sha256sum < "$cfg/config")" ]  # opening must never modify the file
echo "smoke test passed: $pkg"
