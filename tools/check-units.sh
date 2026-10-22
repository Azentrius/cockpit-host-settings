#!/bin/sh
# Checks the units in systemd/ with systemd-analyze verify, and the hardening of every service
# with systemd-analyze security (exposure at most 5.0 of 10). The test workflow runs it with the
# other lint steps.
#   sh tools/check-units.sh
# systemd-analyze verify exits 0 even for unknown keys, so any output counts as a finding; a
# template (NAME@.service) is checked as its instance NAME@ntfy.service. The copies get mode 644:
# a checkout may carry other modes, which systemd-analyze would report as well. Messages about the
# user cockpit-host-settings, which the package creates, do not count where it does not exist.
# SPDX-License-Identifier: Apache-2.0
set -u
cd "$(dirname "$0")/.." || exit 2
tmp=$(mktemp -d) || exit 2
for f in systemd/*.service systemd/*.timer; do
    name=$(basename "$f")
    case $name in
        *@.service) name=${name%@.service}@ntfy.service ;;
    esac
    cp "$f" "${tmp:?}/$name"
done
chmod 644 "${tmp:?}"/*
out=$(systemd-analyze verify "${tmp:?}"/* 2>&1 | grep -v 'cockpit-host-settings.*user' || true)
fail=0
if [ -n "$out" ]; then
    printf '%s\n' "$out"
    fail=1
fi
for s in "${tmp:?}"/*.service; do
    if ! systemd-analyze security --offline=yes --threshold=50 --no-pager "$s" > "${tmp:?}/security.txt" 2>&1; then
        tail -n 3 "${tmp:?}/security.txt"
        echo "$(basename "$s"): exposure above 5.0"
        fail=1
    fi
done
rm -rf "${tmp:?}"
if [ "$fail" != 0 ]; then
    echo "UNITS: FINDINGS"
    exit 1
fi
echo "UNITS OK"
