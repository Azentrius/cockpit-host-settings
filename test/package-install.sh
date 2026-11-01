#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# The install test of the packages, as root in a throw-away container (CI, test workflow: Debian 13
# and Fedora 43 in job test, Rocky Linux and AlmaLinux 9 and 10 in job install):
#
#   sh test/package-install.sh deb|rpm
#
# This build's package is in /dist, tools/dnf.sh is /dnf.sh (dnf with time limits and three
# tries). Installs it with the distribution's own dependencies (the versions it took are
# printed) and checks: the files and their modes, the token file and the
# watchdog's (empty: no watchdog), the notifier's own user and state folder, a run of the notifier as that user without a channel, the
# status verb as nobody; then removes the package: its files and state go, the token file stays.
# No Cockpit or systemd runs there.
set -eu
kind=${1:?deb or rpm}
name=cockpit-host-settings

case $kind in
    deb)
        apt-get update -qq
        apt-get install -y -qq /dist/*.deb > /dev/null
        dpkg-query -W cockpit-bridge python3 systemd iproute2 ;;
    rpm)
        sh /dnf.sh install -y -q /dist/*.rpm
        rpm -q cockpit-bridge python3 systemd iproute ;;
    *) echo "deb or rpm, not $kind" >&2; exit 64 ;;
esac
test -x /usr/lib/cockpit-host-settings/host-settings.py
test -x /usr/lib/cockpit-host-settings/host-settings-notify.py
test -f /usr/lib/systemd/system/cockpit-host-settings-notify.timer
test -f "/usr/lib/systemd/system/cockpit-host-settings-test@.service"
test -f /usr/share/cockpit/host-settings/manifest.json
test "$(stat -c %a /usr/lib/cockpit-host-settings/hslib.py)" = 644
test "$(stat -c "%a %U" /etc/cockpit-host-settings/ntfy.token)" = "600 root"
test "$(stat -c "%a %U %s" /etc/cockpit-host-settings/heartbeat.conf)" = "600 root 0"
test "$(stat -c %a /usr/lib/cockpit-host-settings/hsheartbeat.py)" = 644
test "$(stat -c %a /usr/lib/cockpit-host-settings/hschannels.py)" = 644
test "$(stat -c %a /usr/lib/cockpit-host-settings/hsmenu.py)" = 644
# Cockpit's menu as the helper reads it: this add-on's own entry is in it.
/usr/lib/cockpit-host-settings/host-settings.py menu show | python3 -c 'import json, sys
assert any(e["dir"] == "host-settings" and e["key"] == "index" for e in json.load(sys.stdin)["entries"])'
uid=$(getent passwd cockpit-host-settings | cut -d: -f3)
test -n "$uid"
test "$(stat -c %U /var/lib/cockpit-host-settings)" = cockpit-host-settings
# A run as the notifier user, without a channel: it reads, tells nobody, writes its state.
python3 -c "import os, sys; u = int(sys.argv[1]); os.setgroups([]); os.setgid(u); os.setuid(u); os.execv(sys.argv[2], sys.argv[2:])" \
    "$uid" /usr/lib/cockpit-host-settings/host-settings-notify.py run
test "$(stat -c %a /var/lib/cockpit-host-settings/status.json)" = 644
test "$(stat -c %a /var/lib/cockpit-host-settings/last.json)" = 600
# The status verb as nobody (65534 in all the images; the Fedora image has no su).
python3 -c "import os, sys; os.setgroups([]); os.setgid(65534); os.setuid(65534); os.execv(sys.argv[1], sys.argv[1:])" \
    /usr/lib/cockpit-host-settings/host-settings.py status --lang de |
    python3 -c 'import json, sys; d = json.load(sys.stdin); print(d["state"], d["summary"]); assert d["schema"] == 1 and d["version"] != "unknown" and d["state"] == "none"'
case $kind in
    deb) apt-get remove -y -qq "$name" > /dev/null ;;
    rpm) dnf remove -y -q "$name" ;;
esac
test ! -e /usr/lib/cockpit-host-settings
test ! -e /var/lib/cockpit-host-settings
test -e /etc/cockpit-host-settings/ntfy.token
test -e /etc/cockpit-host-settings/heartbeat.conf
echo "ok: $name installed, checked and removed"
