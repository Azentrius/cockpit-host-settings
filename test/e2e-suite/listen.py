#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A listener for E2E-1 (test/e2e-suite/core.py): accepts TCP connections on 0.0.0.0:PORT and
closes each at once, until it is stopped. core.py starts it with systemd-run as a unit of its own,
so that the scan sees a real socket of a real process, and stops the unit to close the port again.

    python3 -I listen.py PORT
"""
import socket
import sys

port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", port))
s.listen(16)
while True:
    conn, _ = s.accept()
    conn.close()
