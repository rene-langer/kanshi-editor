#!/bin/sh
# Install what the tests need (GTK 4, libadwaita, PyGObject, pycairo, Xvfb) on the
# distro this runs on. Used by CI; handy for a fresh container too.
set -eu
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends \
    python3 python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
    librsvg2-common adwaita-icon-theme fonts-dejavu-core make xvfb xauth >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q python3 python3-gobject python3-cairo gtk4 libadwaita librsvg2 \
    adwaita-icon-theme dejavu-sans-fonts make xorg-x11-server-Xvfb xorg-x11-xauth >/dev/null
elif command -v pacman >/dev/null; then
  pacman -Syu --noconfirm --needed python python-gobject python-cairo gtk4 libadwaita librsvg \
    adwaita-icon-theme ttf-dejavu make xorg-server-xvfb xorg-xauth >/dev/null
else
  echo "unsupported distro" >&2
  exit 2
fi
python3 -c 'import gi; gi.require_version("Adw", "1"); from gi.repository import Adw; print("libadwaita", Adw.MAJOR_VERSION, Adw.MINOR_VERSION, Adw.MICRO_VERSION)'
