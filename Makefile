# Host Settings, a Cockpit add-on: messages through ntfy or e-mail about the Azentrius add-ons
# SPDX-License-Identifier: Apache-2.0
#
# The paths are fixed (the page, the helpers and the units refer to each other by them);
# DESTDIR only stages. The usual way to install is the package (make packages).

DESTDIR ?=
PAGE = $(DESTDIR)/usr/share/cockpit/host-settings
HELPERS = $(DESTDIR)/usr/lib/cockpit-host-settings
UNITS = $(DESTDIR)/usr/lib/systemd/system
SYSUSERS = $(DESTDIR)/usr/lib/sysusers.d
CONF = $(DESTDIR)/etc/cockpit-host-settings
VERSION := $(shell sed -n 's/^ *const VERSION = "\(.*\)";.*/\1/p' plugin/settings.js)
# Reproducible archives and packages: file times from the last commit.
SOURCE_DATE_EPOCH ?= $(shell git log -1 --format=%ct 2>/dev/null || date +%s)

.PHONY: all install uninstall check lint dist stage packages

all:
	@echo "Nothing to build. Try: make check, make lint, make packages, sudo make install"

install:
	install -d $(PAGE) $(HELPERS) $(UNITS) $(SYSUSERS)
	install -d -m 755 $(CONF)
	install -m 644 plugin/* $(PAGE)/
	install -m 755 lib/host-settings.py lib/host-settings-notify.py $(HELPERS)/
	install -m 644 lib/hslib.py lib/hsnotify.py lib/hsstatus.py lib/hsproc.py lib/hsheartbeat.py $(HELPERS)/
	install -m 644 systemd/*.service systemd/*.timer $(UNITS)/
	install -m 644 packaging/cockpit-host-settings.sysusers $(SYSUSERS)/cockpit-host-settings.conf
	if [ -z "$(DESTDIR)" ]; then sh packaging/scripts/configure; fi

uninstall:
	sh packaging/scripts/remove
	rm -rf $(PAGE) $(HELPERS)
	rm -f $(UNITS)/cockpit-host-settings-notify.service $(UNITS)/cockpit-host-settings-notify.timer \
		'$(UNITS)/cockpit-host-settings-test@.service' $(SYSUSERS)/cockpit-host-settings.conf
	sh packaging/scripts/removed

# Every offline test (the notifier and the helper with stand-ins, the page under jsdom, design, version).
check:
	cd test && npm ci && bash run-all.sh

# As in CI (the tools: pip install -r test/requirements.txt).
lint:
	shellcheck --severity=warning tools/*.sh test/*.sh test/e2e/*.sh test/e2e-suite/*.sh packaging/scripts/*
	ruff check .
	sh tools/check-units.sh

# Source archive of the last commit.
dist:
	git archive --format=tar.gz --prefix=cockpit-host-settings-$(VERSION)/ \
		-o cockpit-host-settings-$(VERSION).tar.gz HEAD
	@echo cockpit-host-settings-$(VERSION).tar.gz

# The files of the .deb and .rpm, as installed, in build/root.
stage:
	rm -rf build/root
	$(MAKE) install DESTDIR=build/root
	install -D -m 644 packaging/de.azentrius.host_settings.metainfo.xml \
		build/root/usr/share/metainfo/de.azentrius.host_settings.metainfo.xml
	install -D -m 644 NOTICE build/root/usr/share/doc/cockpit-host-settings/copyright
	install -m 644 LICENSE build/root/usr/share/doc/cockpit-host-settings/LICENSE
	install -m 644 plugin/CC-BY-4.0-Font-Awesome.txt build/root/usr/share/doc/cockpit-host-settings/

# cockpit-host-settings_VERSION-1_all.deb and cockpit-host-settings-VERSION-1.noarch.rpm in dist/
# (needs dpkg-deb and rpmbuild), reproducible.
packages: stage
	SOURCE_DATE_EPOCH=$(SOURCE_DATE_EPOCH) sh tools/build-packages.sh build/root $(VERSION) dist
