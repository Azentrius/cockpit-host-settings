#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A small client of Uptime Kuma's own API, for the end-to-end test (test/e2e/run.sh, CI only):
Socket.IO 4 over HTTP long-polling with the standard library, as Uptime Kuma's page speaks it.
It creates the first user and a push monitor in a throw-away container, and reads the monitor's
heartbeats. The user's password and the push token are made up here and stay in SESSION, a file
of root's (0600) on the throw-away machine: nothing of it leaves CI, and nothing of it is printed.

  kuma.py wait URL [SECONDS]
  kuma.py setup URL SESSION        the first user and a push monitor (heartbeat interval 20 seconds,
                                   no retries); prints Uptime Kuma's version and the monitor's id
  kuma.py last URL SESSION         the id of the monitor's newest heartbeat (0 without one)
  kuma.py wait-beat URL SESSION AFTER STATUS [SECONDS]
                                   the first heartbeat after the id AFTER with this status (0 down,
                                   1 up), as JSON
  kuma.py beats URL SESSION AFTER  the heartbeats after the id AFTER, one JSON per line

Written against Uptime Kuma 2.5.7 (server/server.js: the events setup, login, add and
getMonitorBeats; the monitor as src/pages/EditMonitor.vue sends it).
"""

import json
import os
import re
import secrets
import string
import sys
import time
import urllib.parse
import urllib.request

# What Uptime Kuma's page sends for a new monitor (monitorDefaults of 2.5.7), made a push
# monitor: 20 seconds is the shortest heartbeat interval it takes; no retries, so one missed
# heartbeat or one failure is "down" at once.
MONITOR = {
    "type": "push", "name": "Host Settings e2e", "parent": None, "url": "https://", "wsSubprotocol": "",
    "method": "GET", "protocol": None, "location": "world", "ipFamily": None, "interval": 20,
    "retryInterval": 20, "resendInterval": 0, "maxretries": 0, "retryOnlyOnStatusCodeFailure": False,
    "notificationIDList": {}, "ignoreTls": False, "upsideDown": False, "expiryNotification": False,
    "domainExpiryNotification": True, "maxredirects": 10, "accepted_statuscodes": ["200-299"], "saveResponse": False,
    "saveErrorResponse": True, "responseMaxLength": 1024, "dns_resolve_type": "A", "dns_resolve_server": "",
    "docker_container": "", "docker_host": None, "proxyId": None, "basic_auth_user": "", "basic_auth_pass": "",
    "bearer_token": "", "mqttUsername": "", "mqttPassword": "", "mqttTopic": "", "mqttWebsocketPath": "",
    "mqttSuccessMessage": "", "mqttCheckType": "keyword", "authMethod": None, "oauth_auth_method": "client_secret_basic",
    "httpBodyEncoding": "json", "kafkaProducerBrokers": [], "kafkaProducerSaslOptions": {"mechanism": "None"},
    "cacheBust": False, "kafkaProducerSsl": False, "kafkaProducerAllowAutoTopicCreation": False,
    "gamedigGivenPortOnly": True, "gamedigToken": "", "remote_browser": None, "screenshot_delay": 0,
    "rabbitmqNodes": [], "rabbitmqUsername": "", "rabbitmqPassword": "", "conditions": [],
    "system_service_name": "", "sshAuthMethod": "password", "ntpStratumThreshold": 5,
    "ntpTimeOffsetThreshold": 1000, "ntpRootDispersionThreshold": 500,
}
# No proxy from the environment: everything here is on the loopback.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Client:
    """Socket.IO 4 (Engine.IO 4) over long-polling: one GET at a time, a POST per packet."""

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.sid = None
        self.connected = False
        self.events = []
        self.acks = {}
        self.next_id = 0

    def _url(self):
        q = {"EIO": "4", "transport": "polling", "t": str(time.time_ns())}
        if self.sid:
            q["sid"] = self.sid
        return self.base + "/socket.io/?" + urllib.parse.urlencode(q)

    def _request(self, data=None, timeout=40):
        headers = {"Content-Type": "text/plain;charset=UTF-8"} if data is not None else {}
        req = urllib.request.Request(self._url(), data=data, method="POST" if data is not None else "GET", headers=headers)
        with OPENER.open(req, timeout=timeout) as r:
            return r.read().decode("utf-8")

    def connect(self):
        body = self._request(timeout=10)
        if not body.startswith("0"):
            raise RuntimeError("no Engine.IO handshake")
        self.sid = json.loads(body[1:])["sid"]
        self._send("40")
        self._until(lambda: self.connected, 30)
        return self

    def _send(self, packet):
        if self._request(packet.encode("utf-8"), timeout=20) != "ok":
            raise RuntimeError("Engine.IO did not take a packet")

    def _poll(self):
        # A long poll returns with data, or with the server's ping after 25 seconds at the latest.
        for packet in self._request(timeout=40).split("\x1e"):
            self._packet(packet)

    def _packet(self, packet):
        if not packet:
            return
        kind = packet[0]
        if kind == "2":
            self._send("3")
        elif kind == "1":
            raise RuntimeError("closed by Uptime Kuma")
        elif kind == "4":
            self._message(packet[1:])

    def _message(self, msg):
        kind, rest = msg[:1], msg[1:]
        if rest.startswith("/"):
            return
        if kind == "0":
            self.connected = True
        elif kind in ("1", "4"):
            raise RuntimeError("Socket.IO refused or ended the connection")
        elif kind in ("2", "3"):
            m = re.match(r"([0-9]*)(.*)", rest, re.S)
            data = json.loads(m.group(2))
            if kind == "2":
                self.events.append((data[0], data[1:]))
            elif m.group(1):
                self.acks[int(m.group(1))] = data

    def _until(self, done, seconds):
        end = time.monotonic() + seconds
        while not done():
            if time.monotonic() > end:
                raise TimeoutError("no answer from Uptime Kuma in time")
            self._poll()
        return done()

    def call(self, event, *args, seconds=60):
        aid = self.next_id
        self.next_id += 1
        self._send("42" + str(aid) + json.dumps([event, *args]))
        return self._until(lambda: self.acks.get(aid), seconds)[0]

    def version(self, seconds=30):
        def found():
            return next((a[0]["version"] for e, a in reversed(self.events)
                         if e == "info" and a and isinstance(a[0], dict) and a[0].get("version")), None)
        return self._until(found, seconds)


def login(base, session):
    c = Client(base).connect()
    r = c.call("login", {"username": session["user"], "password": session["password"], "token": ""})
    if not isinstance(r, dict) or not r.get("ok"):
        raise RuntimeError("login refused: " + str((r or {}).get("msg")))
    return c


def beats(c, session):
    r = c.call("getMonitorBeats", session["monitor"], 1)
    if not r.get("ok"):
        raise RuntimeError("getMonitorBeats refused: " + str(r.get("msg")))
    return sorted(({"id": b["id"], "status": b["status"], "important": bool(b["important"]), "time": b["time"],
                    "msg": b.get("msg")} for b in r["data"]), key=lambda b: b["id"])


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    if len(argv) >= 2 and argv[0] == "wait":
        end = time.monotonic() + (int(argv[2]) if len(argv) > 2 else 180)
        while time.monotonic() < end:
            try:
                Client(argv[1]).connect()
                print("Uptime Kuma answers")
                return 0
            except Exception:  # noqa: BLE001 - not up yet
                time.sleep(2)
        print("Uptime Kuma did not answer", file=sys.stderr)
        return 1
    if len(argv) == 3 and argv[0] == "setup":
        c = Client(argv[1]).connect()
        session = {"user": "e2e", "password": "Hs" + secrets.token_hex(16) + "9a"}
        r = c.call("setup", session["user"], session["password"])
        if not r.get("ok"):
            raise RuntimeError("setup refused: " + str(r.get("msg")))
        c = login(argv[1], session)
        version = c.version()
        token = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
        r = c.call("add", dict(MONITOR, pushToken=token))
        if not r.get("ok"):
            raise RuntimeError("add refused: " + str(r.get("msg")))
        session.update(monitor=r["monitorID"], version=version, push_url=argv[1].rstrip("/") + "/api/push/" + token)
        fd = os.open(argv[2], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(session, f)
        print(f"Uptime Kuma {version}, push monitor {session['monitor']}")
        return 0
    if len(argv) == 3 and argv[0] == "last":
        session = load(argv[2])
        found = beats(login(argv[1], session), session)
        print(found[-1]["id"] if found else 0)
        return 0
    if len(argv) in (5, 6) and argv[0] == "wait-beat":
        session = load(argv[2])
        after, status = int(argv[3]), int(argv[4])
        end = time.monotonic() + (int(argv[5]) if len(argv) > 5 else 60)
        c = login(argv[1], session)
        while time.monotonic() < end:
            hit = next((b for b in beats(c, session) if b["id"] > after and b["status"] == status), None)
            if hit:
                print(json.dumps(hit))
                return 0
            time.sleep(2)
        print(f"no heartbeat with status {status} after {after}", file=sys.stderr)
        return 1
    if len(argv) == 4 and argv[0] == "beats":
        session = load(argv[2])
        for b in beats(login(argv[1], session), session):
            if b["id"] > int(argv[3]):
                print(json.dumps(b))
        return 0
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
