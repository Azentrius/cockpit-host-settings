#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A stand-in watchdog for the end-to-end test (test/e2e/run.sh, CI only), speaking both
protocols Host Settings knows, on 127.0.0.1:

  Healthchecks   /ping/ID (success), /ping/ID/fail, /ping/ID/log; an unknown ID is answered
                 200 "OK (not found)" (404 for /log), as Healthchecks does
  Uptime Kuma    /api/push/ID?status=up|down&msg=...; an unknown ID is answered 404
                 {"ok": false, "msg": "Monitor not found or not active."}
  /redirect/ping/ID  302 to /ping/ID: whoever follows it reaches a real check
  /_status       what the stand-in saw, as JSON (also in DIR/status.json)

  watchdog.py PORT DIR PERIOD

DIR/ids holds the IDs that exist, one per line (a file, not arguments: the test looks for the
IDs in every process's arguments). Like a real watchdog, each ID is "up" after a success, "down"
after a failure or when no success came for PERIOD seconds ("missed"); a success after "down" is
a recovery. The log (DIR/requests.log) names the IDs only by their order in DIR/ids.
"""

import http.server
import json
import os
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

LOCK = threading.Lock()


def main(argv):
    port, folder, period = int(argv[0]), argv[1], float(argv[2])
    with open(os.path.join(folder, "ids"), encoding="utf-8") as f:
        ids = [line.strip() for line in f if line.strip()]
    checks = {i: {"name": f"check-{n}", "state": "new", "last_success": None, "success": 0, "failure": 0, "log": 0,
                  "missed": 0, "recoveries": 0, "events": []} for n, i in enumerate(ids, 1)}

    def save():
        doc = {c["name"]: {k: v for k, v in c.items() if k != "last_success"} for c in checks.values()}
        tmp = os.path.join(folder, ".status.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        os.replace(tmp, os.path.join(folder, "status.json"))
        return doc

    def signal(cid, kind):
        c = checks[cid]
        now = time.time()
        c[kind] += 1
        c["events"].append({"t": round(now, 1), "kind": kind})
        if kind == "success":
            if c["state"] == "down":
                c["recoveries"] += 1
                c["events"].append({"t": round(now, 1), "kind": "recovery"})
            c["state"], c["last_success"] = "up", now
        elif kind == "failure":
            c["state"] = "down"
        save()

    def watch():
        while True:
            time.sleep(0.5)
            with LOCK:
                now = time.time()
                for c in checks.values():
                    if c["state"] == "up" and now - c["last_success"] > period:
                        c["state"] = "down"
                        c["missed"] += 1
                        c["events"].append({"t": round(now, 1), "kind": "missed"})
                        save()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self, code, body, location=None):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            if location:
                self.send_header("Location", location)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def handle_any(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(min(length, 65536))
            url = urlsplit(self.path)
            parts = [p for p in url.path.split("/") if p]
            with LOCK:
                shown = "/".join(checks[p]["name"] if p in checks else p for p in parts)
                with open(os.path.join(folder, "requests.log"), "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} {self.command} /{shown}\n")
                if parts[:1] == ["_status"]:
                    return self.answer(200, json.dumps(save()))
                if parts[:2] == ["redirect", "ping"] and len(parts) == 3:
                    # To the check itself, named from the stand-in's own list, never from the request.
                    known = next((i for i in checks if i == parts[2]), None)
                    return self.answer(302, "", "/ping/" + known) if known else self.answer(404, "not found")
                if len(parts) in (2, 3) and parts[0] == "ping":
                    kind = {2: "success"}.get(len(parts)) or {"fail": "failure", "log": "log"}.get(parts[2])
                    if parts[1] not in checks:
                        return self.answer(404, "not found") if kind == "log" else self.answer(200, "OK (not found)")
                    if kind is None:
                        return self.answer(400, "invalid url format")
                    signal(parts[1], kind)
                    return self.answer(200, "OK")
                if len(parts) == 3 and parts[:2] == ["api", "push"]:
                    if parts[2] not in checks:
                        return self.answer(404, json.dumps({"ok": False, "msg": "Monitor not found or not active."}))
                    status = parse_qs(url.query).get("status", ["up"])[0]
                    signal(parts[2], "success" if status == "up" else "failure")
                    return self.answer(200, json.dumps({"ok": True}))
                return self.answer(404, "not found")

        do_GET = do_POST = do_HEAD = handle_any

    with LOCK:
        save()
    threading.Thread(target=watch, daemon=True).start()
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
