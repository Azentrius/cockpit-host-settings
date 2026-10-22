#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Runs every offline test: no root, no network, no add-on of this machine (the notifier and the
# helper read stand-ins: test/fixtures/board, recorded answers of an ntfy server, a fake SMTP).
#   cd test && npm ci && ./run-all.sh
# The end-to-end test with real systemd units, a real ntfy server and a real mail server is
# separate: CI, job e2e (test/e2e/run.sh).
set -u
cd "$(dirname "$0")" || exit 2

if [ ! -d node_modules/jsdom ] && [ -z "${NODE_PATH:-}" ]; then
    echo "jsdom missing - run first: (cd test && npm ci)" >&2
    exit 2
fi

fail=0
run() {
    local name=$1; shift
    local log
    log=$(mktemp)
    if "$@" >"$log" 2>&1; then
        echo "PASS  $name"
    else
        echo "FAIL  $name"
        sed 's/^/      | /' "$log"
        fail=1
    fi
    rm -f "$log"
}

run "python: the board's fixtures, changes, messages, channels"  python3 -B notify_test.py
run "python: whole runs of the notifier, its own status"          python3 -B run_test.py
run "python: the verbs of the page and of the board"              python3 -B helper_test.py
run "python: syntax lib/*.py"                                      python3 -B -c 'import ast, glob; [ast.parse(open(f).read(), f) for f in glob.glob("../lib/*.py")]'
run "shell:  syntax of the shell scripts"                          bash -c 'for f in ../tools/*.sh ../packaging/scripts/* *.sh e2e/*.sh; do [ -e "$f" ] || continue; sh -n "$f" || exit 1; done'
run "page:   overview, ntfy, e-mail, dialogs, escaping, menu"       timeout 300 node page.test.js
run "page:   texts in English and German"                          node i18n.test.js
run "page:   files, calls, manifest"                               node files.test.js
run "design: contrast (light + dark)"                              python3 -B contrast_test.py
run "design: fonts ship with the add-on"                           python3 -B fonts_test.py
run "repo:   version + changelog"                                  sh ../tools/check-version.sh
run "repo:   shared design files"                                  sh ../tools/check-design-files.sh
run "repo:   Status Board's fixtures, unchanged"                   sh ../tools/check-board-fixtures.sh

echo
if [ $fail = 0 ]; then echo "ALL TESTS PASSED"; else echo "THERE ARE FAILURES"; fi
exit $fail
