#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A listener for E2E-1 (test/e2e-suite/core.py): accepts TCP connections on PUBLIC:PORT and
closes each at once, until it is stopped. core.py starts it with systemd-run as a unit of its own,
so that the scan sees a real socket of a real process, and stops the unit to close the port again.

PUBLIC is the public address that setup.sh puts on the dummy interface pub0, the scan's
"internet". A socket on the loopback (127.0.0.1) is one the scan rightly rates "this host only",
so the core path would test nothing; the public address keeps the socket reachable from the
internet without binding to every interface.

    python3 -I listen.py PORT
"""
import socket
import sys

PUBLIC = "203.0.113.10"

port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((PUBLIC, port))
s.listen(16)
while True:
    conn, _ = s.accept()
    conn.close()
