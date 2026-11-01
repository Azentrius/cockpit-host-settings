#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A mail server for E2E-1, step 13 (test/e2e-suite/core.py): takes every message on
127.0.0.1:25 and keeps each in a file of its own in DIR, until it is stopped. It speaks just
enough SMTP for Python's smtplib, as Host Settings' notifier uses it (no TLS, no login). core.py
starts it with systemd-run as a unit of its own once e-mail has failed, so that a test message
can succeed. Never installed on a server.

    python3 -I smtp_sink.py DIR
"""
import os
import socketserver
import sys
import time

DIR = sys.argv[1]


class Handler(socketserver.StreamRequestHandler):
    def reply(self, *lines):
        self.wfile.write("".join(line + "\r\n" for line in lines).encode("ascii"))
        self.wfile.flush()

    def handle(self):
        self.reply("220 e2e-suite ESMTP")
        while True:
            raw = self.rfile.readline(65536)
            if not raw:
                return
            verb = raw.decode("utf-8", "replace").strip()[:4].upper()
            if verb == "EHLO":
                self.reply("250-e2e-suite", "250 8BITMIME")
            elif verb == "HELO":
                self.reply("250 e2e-suite")
            elif verb in ("MAIL", "RCPT", "RSET", "NOOP"):
                self.reply("250 OK")
            elif verb == "DATA":
                self.reply("354 End data with <CR><LF>.<CR><LF>")
                lines = []
                while True:
                    line = self.rfile.readline(65536)
                    if not line or line in (b".\r\n", b".\n"):
                        break
                    lines.append(line[1:] if line.startswith(b"..") else line)
                name = os.path.join(DIR, "%d.eml" % time.time_ns())
                with open(name + ".part", "wb") as f:
                    f.writelines(lines)
                os.replace(name + ".part", name)
                self.reply("250 OK")
            elif verb == "QUIT":
                self.reply("221 Bye")
                return
            else:
                self.reply("502 Not implemented")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


os.makedirs(DIR, exist_ok=True)
Server(("127.0.0.1", 25), Handler).serve_forever()
