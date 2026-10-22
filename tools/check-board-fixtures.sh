#!/bin/sh
# test/fixtures/board holds the test answers and manifests of Status Board
# (Azentrius/cockpit-status-board, test/fixtures/status and test/fixtures/manifests), unchanged:
# the notifier must judge them as the board does (test/notify_test.py). tools/board-fixtures.sha256
# pins them, and every file there must be in the list. Take new versions from the board's main
# (a checkout next to this one) and update the list:
#   rm -rf test/fixtures/board && mkdir -p test/fixtures/board
#   git -C ../cockpit-status-board archive origin/main test/fixtures/status test/fixtures/manifests |
#       tar -x --strip-components=2 -C test/fixtures/board
#   find test/fixtures/board -type f | LC_ALL=C sort | xargs sha256sum > tools/board-fixtures.sha256
#   sh tools/check-board-fixtures.sh
# SPDX-License-Identifier: Apache-2.0
set -u
cd "$(dirname "$0")/.." || exit 2
if ! sha256sum --strict --quiet -c tools/board-fixtures.sha256; then
    echo "board fixtures: differ from tools/board-fixtures.sha256" >&2
    exit 1
fi
listed=$(sed -E 's/^[0-9a-f]+ [ *]//' tools/board-fixtures.sha256 | LC_ALL=C sort)
present=$(find test/fixtures/board -type f | LC_ALL=C sort)
if [ "$listed" != "$present" ]; then
    echo "board fixtures: files that are not in tools/board-fixtures.sha256, or listed ones that are gone" >&2
    exit 1
fi
echo "board fixtures: identical to the pinned versions of Status Board"
