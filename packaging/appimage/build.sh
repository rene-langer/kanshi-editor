#!/bin/sh
# Build the AppImage inside a debian:trixie container: `make appimage`.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  make python3 python3-pip python3-venv git file binutils patchelf squashfs-tools \
  desktop-file-utils fakeroot gtk-update-icon-cache librsvg2-bin zsync wget ca-certificates \
  libglib2.0-bin libgdk-pixbuf2.0-bin gpg >/dev/null
# appimage-builder still looks for apt-key, which Debian 13 removed. The recipe
# adds no keys (it uses the container's own trusted Debian archive), so a no-op is enough.
printf '#!/bin/sh\nexit 0\n' > /usr/local/bin/apt-key && chmod +x /usr/local/bin/apt-key
python3 -m venv /opt/aib
/opt/aib/bin/pip install -q "git+https://github.com/AppImageCrafters/appimage-builder.git@${APPIMAGE_BUILDER_REF:-b1d6ca4b6e39cb7bced3dccfde95a83bef62a500}"
# appimage-builder bug (still present upstream): APPDIR_LIBC_LINKER_PATH is stored as a
# Python set, and the environment writer prints sets as "{'lib64/ld-linux...'}", so
# the bundled-glibc mode used on older distros cannot find its linker. Store a list.
apprun2="$(/opt/aib/bin/python -c 'import appimagebuilder.modules.setup.apprun_2.apprun2 as m; print(m.__file__)')"
sed -i 's/set(patcher.binary_interpreters_paths.values())/sorted(set(patcher.binary_interpreters_paths.values()))/' "$apprun2"
grep -q 'sorted(set(patcher.binary_interpreters_paths' "$apprun2"
mkdir -p build/appimage dist
recipe=packaging/appimage/AppImageBuilder.yml
# Phase 1: build the AppDir.
/opt/aib/bin/appimage-builder --recipe "$recipe" --build-dir build/appimage --skip-tests --skip-appimage
# AppRun starts binaries from runtime/compat (bundled glibc, for hosts with an older
# glibc) with the relative interpreter path lib64/ld-linux-x86-64.so.2, but the pinned
# appimage-builder only puts the linker in runtime/compat/usr/lib64. Link it.
test -e AppDir/runtime/compat/usr/lib64/ld-linux-x86-64.so.2
ln -sfn usr/lib64 AppDir/runtime/compat/lib64
# appimage-builder leaves the SVG loader (libpixbufloader_svg.so) out of loaders.cache,
# so every SVG icon would show as a broken image. Regenerate the cache with the
# relative module names the AppImage expects.
pixbuf=AppDir/usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/2.10.0
LD_LIBRARY_PATH="$PWD/AppDir/usr/lib/x86_64-linux-gnu" \
  /usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/gdk-pixbuf-query-loaders "$PWD/$pixbuf"/loaders/*.so \
  | sed "s|$PWD/$pixbuf/loaders/||" > "$pixbuf/loaders.cache"
grep -q '"libpixbufloader_svg.so"' "$pixbuf/loaders.cache"
# Phase 2: pack the AppImage.
/opt/aib/bin/appimage-builder --recipe "$recipe" --build-dir build/appimage --skip-script --skip-build --skip-tests
mv kanshi-editor-*-x86_64.AppImage* dist/
