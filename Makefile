# Kanshi Editor: run, test, install and package.
#
#   make run | test | lint
#   make install PREFIX=/usr DESTDIR=...   install files (used by every package)
#   make install-dev                       symlink the checkout into ~/.local
#   make deb | rpm | archlinux             native packages via nfpm (podman/docker if not installed)
#   make flatpak                           Flatpak bundle via flatpak-builder
#   make appimage                          AppImage via appimage-builder (in a container)
#   make dist                              all of the above into dist/

APP_ID   := io.github.rene_langer.KanshiEditor
PREFIX   ?= /usr/local
DESTDIR  ?=
VERSION  := $(shell python3 -c 'import kanshi_editor; print(kanshi_editor.__version__)')
PKGDATA  := $(DESTDIR)$(PREFIX)/share/kanshi-editor
NFPM_VERSION := v2.48.0
CONTAINER ?= $(shell command -v podman 2>/dev/null || command -v docker 2>/dev/null)
PYFILES  := $(wildcard kanshi_editor/*.py)

.PHONY: run test lint install install-dev uninstall-dev stage deb rpm archlinux packages flatpak appimage sdist dist screenshots clean

run:
	./bin/kanshi-editor

test:
	python3 -m unittest discover -s tests -t . -v

lint:
	ruff check .
	ruff format --check .

install:
	install -d $(PKGDATA)/kanshi_editor $(DESTDIR)$(PREFIX)/bin
	install -m 644 $(PYFILES) $(PKGDATA)/kanshi_editor/
	install -m 755 bin/kanshi-editor $(DESTDIR)$(PREFIX)/bin/kanshi-editor
	install -Dm 644 data/$(APP_ID).desktop $(DESTDIR)$(PREFIX)/share/applications/$(APP_ID).desktop
	install -Dm 644 data/$(APP_ID).metainfo.xml $(DESTDIR)$(PREFIX)/share/metainfo/$(APP_ID).metainfo.xml
	install -Dm 644 data/icons/hicolor/scalable/apps/$(APP_ID).svg \
		$(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/$(APP_ID).svg
	install -Dm 644 data/icons/hicolor/symbolic/apps/$(APP_ID)-symbolic.svg \
		$(DESTDIR)$(PREFIX)/share/icons/hicolor/symbolic/apps/$(APP_ID)-symbolic.svg

# Links the checkout into ~/.local, so `git pull` updates the installed app.
install-dev:
	install -d $(HOME)/.local/bin $(HOME)/.local/share/applications $(HOME)/.local/share/icons/hicolor/scalable/apps
	ln -sf $(CURDIR)/bin/kanshi-editor $(HOME)/.local/bin/kanshi-editor
	install -m 644 data/$(APP_ID).desktop $(HOME)/.local/share/applications/$(APP_ID).desktop
	install -m 644 data/icons/hicolor/scalable/apps/$(APP_ID).svg $(HOME)/.local/share/icons/hicolor/scalable/apps/

uninstall-dev:
	rm -f $(HOME)/.local/bin/kanshi-editor $(HOME)/.local/share/applications/$(APP_ID).desktop \
		$(HOME)/.local/share/icons/hicolor/scalable/apps/$(APP_ID).svg

# ---------------------------------------------------------------- packages

stage:
	rm -rf build/root
	$(MAKE) install DESTDIR=$(CURDIR)/build/root PREFIX=/usr

dist/:
	mkdir -p dist

ifeq ($(shell command -v nfpm 2>/dev/null),)
NFPM = $(CONTAINER) run --rm -v $(CURDIR):/src:z -w /src -e VERSION docker.io/goreleaser/nfpm:$(NFPM_VERSION)
else
NFPM = nfpm
endif

deb rpm archlinux: stage dist/
	VERSION=$(VERSION) $(NFPM) package --config packaging/nfpm.yaml --packager $@ --target dist/

packages: deb rpm archlinux

flatpak: dist/
	flatpak-builder --user --force-clean --install-deps-from=flathub --repo=build/flatpak-repo \
		build/flatpak packaging/flatpak/$(APP_ID).yml
	flatpak build-bundle build/flatpak-repo dist/kanshi-editor-$(VERSION).flatpak $(APP_ID)

appimage: stage dist/
	$(CONTAINER) run --rm -v $(CURDIR):/src:z -w /src -e VERSION=$(VERSION) docker.io/library/debian:trixie \
		sh packaging/appimage/build.sh

sdist: dist/
	python3 -m build --sdist --wheel --outdir dist/

dist: packages appimage flatpak sdist
	cd dist && sha256sum -- * > SHA256SUMS

# Regenerates docs/screenshots/*.png from docs/demo in a headless sway.
screenshots:
	sh docs/demo/screenshots.sh

clean:
	rm -rf build dist .flatpak-builder *.egg-info
	find . -name __pycache__ -prune -exec rm -rf {} +
