#!/usr/bin/python3 -IB
# SPDX-License-Identifier: Apache-2.0
"""A stand-in for Security Monitor's status verb in E2E-1 (test/e2e-suite/core.py), for Host
Settings' notifier only: a drop-in in /run bind-mounts it over
/usr/lib/cockpit-security-monitor/security.py inside cockpit-host-settings-notify.service, for a
single run, and is removed right after. It prints the answer the test wrote: a real answer of
Security Monitor with its findings in another order (step 4), or one broken on purpose (step 9),
whatever language is asked for; or, when the test wrote an exit code instead, it ends with that
code and prints nothing (step 11: 77, no access). The scan, Cockpit and the board never see it.
Never installed on a server.
"""
import os
import sys

ANSWER = "/usr/local/lib/e2e-suite/stub-answer.json"
EXIT = "/usr/local/lib/e2e-suite/stub-exit"

if sys.argv[1:2] != ["status"]:
    sys.exit(64)
if os.path.exists(EXIT):
    with open(EXIT, encoding="utf-8") as f:
        sys.exit(int(f.read().strip()))
with open(ANSWER, encoding="utf-8") as f:
    sys.stdout.write(f.read())
