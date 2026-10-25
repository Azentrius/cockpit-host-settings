#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The watchdog (lib/hsheartbeat.py and its place in host-settings-notify.py): the addresses each
adapter takes, the requests of both protocols, what an answer means (a 2xx alone is not enough),
the bounded tries; then whole runs with stand-ins: success only once the state is saved, failure
when a message did not go out although the run exits 0, when the settings cannot be read, the
add-ons cannot be collected or the state cannot be written; no signal when another run holds the
lock, no request at all without a watchdog; a signal that does not get through stays apart from
the findings; the URL in no file but its own, no output, no status and no argument. And
hsnotify.http against real sockets: a redirect is not followed, a timeout and a TLS error are
answers of their own."""

import http.server
import io
import json
import os
import socket
import socketserver
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "lib")
sys.dont_write_bytecode = True
sys.path.insert(0, LIB)
sys.path.insert(0, HERE)
import hsheartbeat as hb  # noqa: E402
import hslib  # noqa: E402
import hsnotify  # noqa: E402
import hsproc  # noqa: E402
from notify_test import Runner, Smtp  # noqa: E402
from run_test import CRON, GAPS, NOW, Host, notify  # noqa: E402

HC = "https://hc.example.net/ping/2f9a2c51-7a1b-4c0e-9d2e-5b8d3c1a9e77"
KUMA = "http://10.8.0.2:3001/api/push/Xq3vT8pLmN2bR7sK"
SECRET_HC = "2f9a2c51-7a1b-4c0e-9d2e-5b8d3c1a9e77"
SECRET_KUMA = "Xq3vT8pLmN2bR7sK"


class Normalize(unittest.TestCase):
    def test_what_each_adapter_takes(self):
        good = [
            ("healthchecks", HC, HC),
            ("healthchecks", HC + "/", HC),
            ("healthchecks", "https://hc-ping.com/2f9a2c51-7a1b-4c0e-9d2e-5b8d3c1a9e77", "https://hc-ping.com/2f9a2c51-7a1b-4c0e-9d2e-5b8d3c1a9e77"),
            ("healthchecks", "https://hc-ping.com/pingkeyAbC123/backup-db", "https://hc-ping.com/pingkeyAbC123/backup-db"),
            ("healthchecks", "http://192.168.1.20:8000/ping/abc", "http://192.168.1.20:8000/ping/abc"),
            # Uptime Kuma's page shows the push URL with this query: taken, and dropped.
            ("uptime-kuma", KUMA + "?status=up&msg=OK&ping=", KUMA),
            ("uptime-kuma", KUMA, KUMA),
            ("uptime-kuma", "https://kuma.example.net/api/push/abc_DEF-123", "https://kuma.example.net/api/push/abc_DEF-123"),
            ("uptime-kuma", "https://example.net/kuma/api/push/abc", "https://example.net/kuma/api/push/abc"),
            ("uptime-kuma", "http://100.64.12.7/api/push/abc", "http://100.64.12.7/api/push/abc"),
            ("uptime-kuma", "http://[fd00::2]:3001/api/push/abc", "http://[fd00::2]:3001/api/push/abc"),
            ("uptime-kuma", "http://127.0.0.1:3001/api/push/abc", "http://127.0.0.1:3001/api/push/abc"),
        ]
        for adapter, url, want in good:
            with self.subTest(url):
                self.assertEqual(hb.normalize(adapter, url), want)

    def test_what_is_refused(self):
        bad = [
            ("healthchecks", "http://hc-ping.com/abc", "http-public"),        # a name over plain http
            ("healthchecks", "http://203.0.113.5/ping/abc", "http-public"),   # a public address
            ("healthchecks", "http://172.32.0.1/ping/abc", "http-public"),
            ("uptime-kuma", "http://[2001:db8::1]/api/push/abc", "http-public"),
            ("uptime-kuma", "http://localhost:3001/api/push/abc", "http-public"),
            ("healthchecks", "https://user:pw@hc-ping.com/abc", "form"),
            ("healthchecks", "https://hc-ping.com/abc#x", "form"),
            ("healthchecks", "https://hc-ping.com", "form"),
            ("healthchecks", "ftp://hc-ping.com/abc", "form"),
            ("healthchecks", "https://hc-ping.com:0/abc", "form"),
            ("healthchecks", "https://hc-ping.com:70000/abc", "form"),
            ("healthchecks", "https://hc-ping.com/a b", "form"),
            ("healthchecks", "https://hc-ping.com/" + "a" * 500, "form"),
            ("healthchecks", HC + "?create=1", "query"),
            ("healthchecks", HC + "/fail", "hc-signal"),
            ("healthchecks", HC + "/start", "hc-signal"),
            ("healthchecks", HC + "/log", "hc-signal"),
            ("healthchecks", HC + "/1", "hc-signal"),
            ("uptime-kuma", "https://kuma.example.net/api/push/", "kuma-path"),
            ("uptime-kuma", "https://kuma.example.net/dashboard/3", "kuma-path"),
            ("uptime-kuma", "https://kuma.example.net/api/push/abc/fail", "kuma-path"),
            ("uptime-kuma", "https://kuma.example.net/api/push/abc?status=up&x=1", "query"),
            ("pushover", HC, "adapter"),
        ]
        for adapter, url, why in bad:
            with self.subTest(url):
                with self.assertRaises(hb.Invalid) as e:
                    hb.normalize(adapter, url)
                self.assertEqual(e.exception.reason, why)
        for value in (None, 7, ""):
            with self.assertRaises(hb.Invalid):
                hb.normalize("healthchecks", value)

    def test_describe_and_the_file(self):
        self.assertEqual(hb.describe(HC), {"encrypted": True, "local": False})
        self.assertEqual(hb.describe(KUMA), {"encrypted": False, "local": False})
        self.assertEqual(hb.describe("http://127.0.0.1:3001/api/push/abc"), {"encrypted": False, "local": True})
        text = hb.format_conf("uptime-kuma", KUMA)
        self.assertEqual(hb.parse_conf(text), {"adapter": "uptime-kuma", "url": KUMA})
        self.assertIsNone(hb.parse_conf(""))
        self.assertIsNone(hb.parse_conf("# nothing\n\n"))
        for broken in ("adapter = uptime-kuma\n", "url = " + KUMA + "\n", "adapter = sms\nurl = " + HC + "\n",
                       "adapter = healthchecks\nurl = http://example.net/x\n", "garbage\n",
                       "adapter = healthchecks\nurl = " + HC + "\nother = 1\n"):
            self.assertEqual(hb.parse_conf(broken), {"invalid": True}, broken)


class Requests(unittest.TestCase):
    def test_healthchecks(self):
        self.assertEqual(hb.request_of("healthchecks", HC, "success")[:2], ("POST", HC))
        method, url, body, headers = hb.request_of("healthchecks", HC, "failure", "delivery", ["ntfy timeout"])
        self.assertEqual((method, url), ("POST", HC + "/fail"))
        self.assertEqual(body, b"Host Settings: a message could not be sent (ntfy timeout)")
        self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(hb.request_of("healthchecks", HC, "test")[1], HC + "/log", "a test changes nothing there")

    def test_uptime_kuma_never_fail(self):
        method, url, body, _ = hb.request_of("uptime-kuma", KUMA, "success")
        self.assertEqual((method, body), ("GET", None))
        self.assertEqual(url, KUMA + "?status=up&msg=Host%20Settings%3A%20run%20done")
        url = hb.request_of("uptime-kuma", KUMA, "failure", "state")[1]
        self.assertEqual(url, KUMA + "?status=down&msg=Host%20Settings%3A%20the%20state%20could%20not%20be%20saved")
        self.assertIn("status=up", hb.request_of("uptime-kuma", KUMA, "test")[1])
        for signal in hb.SIGNALS:
            self.assertNotIn("/fail", hb.request_of("uptime-kuma", KUMA, signal)[1])


class Judge(unittest.TestCase):
    def test_answers(self):
        cases = [
            ("healthchecks", 200, b"OK", (True, "accepted")),
            ("healthchecks", 200, b"OK\n", (True, "accepted")),
            ("healthchecks", 201, b"Created", (True, "accepted")),
            ("healthchecks", 200, b"OK (not found)", (False, "not-found")),
            ("healthchecks", 200, b"OK (rate limited)", (False, "rate-limited")),
            ("healthchecks", 200, b"<html>captive portal</html>", (False, "unexpected")),
            ("healthchecks", 404, b"not found", (False, "not-found")),
            ("healthchecks", 409, b"ambiguous", (False, "ambiguous")),
            ("healthchecks", 429, b"", (False, "rate-limited")),
            ("healthchecks", 400, b"", (False, "invalid-url")),
            ("healthchecks", 502, b"", (False, "http-502")),
            ("healthchecks", 302, b"", (False, "redirect")),
            ("uptime-kuma", 200, b'{"ok":true}', (True, "accepted")),
            ("uptime-kuma", 200, b'{"ok":false,"msg":"Monitor not found or not active."}', (False, "refused")),
            ("uptime-kuma", 404, b'{"ok":false,"msg":"Monitor not found or not active."}', (False, "not-found")),
            ("uptime-kuma", 200, b"OK", (False, "unexpected")),
            ("uptime-kuma", 200, b'{"ok":"yes"}', (False, "unexpected")),
            ("uptime-kuma", 404, b"<html>", (False, "http-404")),
            ("uptime-kuma", 301, b"", (False, "redirect")),
            ("uptime-kuma", 500, b"", (False, "http-500")),
        ]
        for adapter, code, body, want in cases:
            with self.subTest((adapter, code, body)):
                self.assertEqual(hb.judge(adapter, code, body), want)


class Stub:
    """A request function that answers from a list (status, body), a NetError reason, or an
    exception; records every call."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, method, url, body=None, headers=None, timeout=20, limit=65536):
        self.calls.append({"method": method, "url": url, "body": body, "headers": dict(headers or {}), "timeout": timeout})
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, BaseException):
            raise a
        if isinstance(a, str):
            raise hsnotify.NetError(a)
        return a


class Send(unittest.TestCase):
    def send(self, adapter, url, signal, stub, clock=None):
        pauses = []
        r = hb.send(adapter, url, signal, request=stub, sleep=pauses.append, clock=clock or time.monotonic)
        return r, pauses

    def test_success_and_failure(self):
        r, pauses = self.send("healthchecks", HC, "success", Stub((200, b"OK")))
        self.assertEqual((r, pauses), ({"ok": True, "code": 200, "reason": "accepted", "attempts": 1}, []))
        stub = Stub((200, b'{"ok":true}'))
        r, _ = self.send("uptime-kuma", KUMA, "failure", stub)
        self.assertTrue(r["ok"])
        self.assertIn("status=down", stub.calls[0]["url"])

    def test_no_answer_is_tried_three_times_at_most(self):
        stub = Stub("timeout")
        r, pauses = self.send("healthchecks", HC, "success", stub)
        self.assertEqual((r["ok"], r["reason"], r["attempts"]), (False, "timeout", hb.ATTEMPTS))
        self.assertEqual(pauses, list(hb.BACKOFF))
        self.assertEqual(len(stub.calls), 3)
        self.assertTrue(all(c["timeout"] <= hb.REQUEST_TIMEOUT for c in stub.calls))
        r, _ = self.send("healthchecks", HC, "success", Stub("network", (503, b""), (200, b"OK")))
        self.assertEqual((r["ok"], r["attempts"]), (True, 3))

    def test_final_answers_are_not_tried_again(self):
        for answer, reason in (("tls", "tls"), ((302, b""), "redirect"), ((200, b"OK (not found)"), "not-found"),
                               ((200, b"OK (rate limited)"), "rate-limited"), ((404, b""), "not-found")):
            with self.subTest(reason):
                stub = Stub(answer)
                r, pauses = self.send("healthchecks", HC, "success", stub)
                self.assertEqual((r["reason"], r["attempts"], pauses), (reason, 1, []))
        r, _ = self.send("uptime-kuma", KUMA, "success", Stub((200, b'{"ok":false,"msg":"x"}')))
        self.assertEqual((r["reason"], r["attempts"]), ("refused", 1))

    def test_the_budget(self):
        now = [0.0]

        def clock():
            return now[0]

        def slow(method, url, body=None, headers=None, timeout=20, limit=65536):
            now[0] += timeout
            raise hsnotify.NetError("timeout")
        pauses = []
        r = hb.send("healthchecks", HC, "success", request=slow, sleep=lambda s: (pauses.append(s), now.__setitem__(0, now[0] + s)),
                    clock=clock)
        self.assertLessEqual(now[0], hb.BUDGET)
        self.assertEqual(r["reason"], "timeout")

    def test_anything_else_is_no_answer_and_no_text(self):
        r, _ = self.send("healthchecks", HC, "success", Stub(RuntimeError("secret " + HC)))
        self.assertEqual((r["ok"], r["reason"]), (False, "network"))
        self.assertNotIn(HC, json.dumps(r))


# ---------------------------------------------------------------------------
# Real sockets: what hsnotify.http, the request of every signal, makes of them

class Quiet(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(handler):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class RealSockets(unittest.TestCase):
    def test_a_redirect_is_not_followed(self):
        hits = []

        class Elsewhere(Quiet):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
        other, other_port = serve(Elsewhere)

        class Redirect(Quiet):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{other_port}/api/push/stolen")
                self.end_headers()
        srv, port = serve(Redirect)
        try:
            r = hb.send("uptime-kuma", f"http://127.0.0.1:{port}/api/push/abc", "success", sleep=lambda s: None)
            self.assertEqual((r["ok"], r["code"], r["reason"]), (False, 302, "redirect"))
            self.assertEqual(hits, [], "the other server was never asked")
        finally:
            for s in (srv, other):
                s.shutdown()
                s.server_close()

    def test_a_timeout(self):
        gate = threading.Event()

        class Silent(socketserver.BaseRequestHandler):
            def handle(self):
                gate.wait(5)
        srv, port = serve(Silent)
        saved = hb.REQUEST_TIMEOUT, hb.BUDGET
        hb.REQUEST_TIMEOUT, hb.BUDGET = 1, 3
        try:
            started = time.monotonic()
            r = hb.send("healthchecks", f"http://127.0.0.1:{port}/ping/abc", "success", sleep=lambda s: None)
            self.assertEqual((r["ok"], r["reason"]), (False, "timeout"))
            self.assertLess(time.monotonic() - started, 4.5)
        finally:
            hb.REQUEST_TIMEOUT, hb.BUDGET = saved
            gate.set()
            srv.shutdown()
            srv.server_close()

    def test_a_tls_error(self):
        # https:// to a server that speaks plain HTTP: the handshake fails, as with a bad certificate.
        class Plain(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    self.request.recv(1024)
                    self.request.sendall(b"HTTP/1.0 200 OK\r\n\r\nOK")
                except OSError:
                    pass
        srv, port = serve(Plain)
        try:
            with self.assertRaises(hsnotify.NetError) as e:
                hsnotify.http("POST", f"https://127.0.0.1:{port}/ping/abc", b"x", timeout=3)
            self.assertEqual(e.exception.reason, "tls")
            r = hb.send("healthchecks", f"https://127.0.0.1:{port}/ping/abc", "success", sleep=lambda s: None)
            self.assertEqual((r["reason"], r["attempts"]), ("tls", 1))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_nobody_listening(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        r = hb.send("healthchecks", f"http://127.0.0.1:{port}/ping/abc", "success", sleep=lambda s: None)
        self.assertEqual((r["ok"], r["reason"], r["attempts"]), (False, "network", 3))


# ---------------------------------------------------------------------------
# Whole runs

class Watchdog:
    """The watchdog behind the notifier's HTTP function: every request to it is recorded with
    what the state folder held at that moment; the rest goes to the host's own stand-in."""

    def __init__(self, host, base, answers=None):
        self.host, self.base, self.calls = host, base, []
        self.inner = host.http
        self.answers = list(answers or [])

    def __call__(self, method, url, body=None, headers=None, timeout=20, limit=65536):
        if not url.startswith(self.base):
            return self.inner(method, url, body, headers, timeout, limit)
        state = {}
        for n in os.listdir(self.host.state):
            if not n.startswith(".") and os.path.isfile(os.path.join(self.host.state, n)):
                with open(os.path.join(self.host.state, n), encoding="utf-8") as f:
                    state[n] = f.read()
        self.calls.append({"method": method, "url": url, "body": body, "state": state})
        a = (self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]) if self.answers else \
            ((200, b'{"ok":true}') if "/api/push/" in url else (200, b"OK"))
        if isinstance(a, str):
            raise hsnotify.NetError(a)
        return a


class Runs(unittest.TestCase):
    def host(self, adapter="healthchecks", url=HC, answers=None, **kw):
        host = Host(**kw)
        host.beat = os.path.join(host.dir, "heartbeat.conf")
        with open(host.beat, "w", encoding="utf-8") as f:
            f.write(hb.format_conf(adapter, url) if adapter else "")
        host.dog = Watchdog(host, url.split("/api/push/")[0] if adapter == "uptime-kuma" else url, answers)
        return host

    def run_host(self, host, *argv, **kw):
        err = io.StringIO()
        saved, sys.stderr = sys.stderr, err
        try:
            code, out = host.run(*argv, heartbeat_file=host.beat, http=host.dog, sleep=lambda s: None, **kw)
        finally:
            sys.stderr = saved
        return code, out, err.getvalue()

    def beat(self, host):
        return host.file("heartbeat.json")

    def test_success_only_once_the_state_is_saved(self):
        host = self.host()
        code, out, _ = self.run_host(host)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(host.dog.calls), 1)
        call = host.dog.calls[0]
        self.assertEqual((call["method"], call["url"]), ("POST", HC))
        saved = json.loads(call["state"]["last.json"])
        self.assertEqual(saved["run"]["at"], hslib.iso(NOW), "last.json of this run was written before the signal")
        self.assertIn("status.json", call["state"])
        self.assertEqual(self.beat(host)["signal"], "success")
        self.assertEqual(self.beat(host)["result"], {"ok": True, "code": 200, "reason": "accepted", "attempts": 1})
        items = {i["id"]: i["state"] for i in host.file("status.json")["items"]}
        self.assertEqual(items["heartbeat"], "ok")
        self.assertIn("watchdog: success, accepted", out)

    def test_a_message_that_did_not_go_out_is_a_failure_although_the_run_exits_0(self):
        host = self.host()
        host.http.answers[("POST", "https://ntfy.example.com/", "auth")] = "network"
        host.items["cronjobs"][0]["state"] = "danger"
        code, _, _ = self.run_host(host)
        self.assertEqual(code, 0, "unchanged: a message that could not be sent is no error of the run")
        self.assertEqual([c["url"] for c in host.dog.calls], [HC + "/fail"])
        self.assertEqual(host.dog.calls[0]["body"], b"Host Settings: a message could not be sent (ntfy network)")
        beat = self.beat(host)
        self.assertEqual((beat["signal"], beat["why"]), ("failure", "delivery"))
        self.assertNotIn("baseline_at", host.file("last.json"), "and the message is sent again, as before")
        host.http.answers[("POST", "https://ntfy.example.com/", "auth")] = (200, "{}")
        host.now += 300
        self.run_host(host)
        self.assertEqual(host.posts()[-1]["title"], "h: messages set up")
        self.assertEqual(host.dog.calls[-1]["url"], HC)

    def test_one_channel_of_two_failing_is_a_failure_and_nothing_is_sent_twice(self):
        host = self.host(conf="ntfy_url = https://ntfy.example.com\nntfy_topic = cockpit-h\nmail_to = ops@example.com\n")
        host.smtp = Smtp(ConnectionRefusedError())
        self.run_host(host, smtp=host.smtp)
        self.assertEqual(host.dog.calls[-1]["url"], HC + "/fail")
        self.assertIn(b"mail no-mta", host.dog.calls[-1]["body"])
        self.assertIn("baseline_at", host.file("last.json"), "ntfy took it: told, as before")
        host.now += 300
        self.run_host(host, smtp=host.smtp)
        self.assertEqual(len(host.posts()), 1, "nothing is sent again")
        self.assertEqual(host.dog.calls[-1]["url"], HC, "a run with nothing to send is a success")

    def test_another_run_holds_the_lock_no_signal(self):
        host = self.host()
        held = notify.lock(host.env())
        try:
            code, _, err = self.run_host(host)
        finally:
            os.close(held)
        self.assertEqual(code, 75)
        self.assertIn("another run holds the lock", err)
        self.assertEqual(host.dog.calls, [])
        self.assertFalse(os.path.exists(os.path.join(host.state, "heartbeat.json")))

    def test_without_a_watchdog_no_request_at_all(self):
        for text in ("", None):
            host = self.host(adapter=None, conf="language = en\n")
            if text is None:
                os.unlink(host.beat)
            code, out, _ = self.run_host(host)
            self.assertEqual(code, 0)
            self.assertEqual(host.http.calls, [])
            self.assertEqual(host.dog.calls, [])
            self.assertFalse(os.path.exists(os.path.join(host.state, "heartbeat.json")))
            self.assertNotIn("heartbeat", {i["id"] for i in host.file("status.json")["items"]})
            self.assertNotIn("watchdog", out)

    def test_uptime_kuma_up_and_down(self):
        host = self.host("uptime-kuma", KUMA)
        self.run_host(host)
        self.assertEqual(host.dog.calls[-1]["method"], "GET")
        self.assertTrue(host.dog.calls[-1]["url"].startswith(KUMA + "?status=up&"))
        host.http.answers[("POST", "https://ntfy.example.com/", "auth")] = "timeout"
        host.items["cronjobs"][0]["state"] = "warning"
        host.now += 300
        self.run_host(host)
        self.assertTrue(host.dog.calls[-1]["url"].startswith(KUMA + "?status=down&"))
        self.assertTrue(all("/fail" not in c["url"] for c in host.dog.calls))

    def test_settings_that_cannot_be_read(self):
        host = self.host()
        os.unlink(host.conf)
        os.mkdir(host.conf)           # cannot be read, also as root
        code, _, err = self.run_host(host)
        self.assertEqual(code, 1)
        self.assertIn("ConfigError", err)
        self.assertEqual([c["url"] for c in host.dog.calls], [HC + "/fail"])
        self.assertEqual(host.dog.calls[0]["body"], b"Host Settings: the settings could not be read")
        self.assertFalse(os.path.exists(os.path.join(host.state, "last.json")), "the previous files stay")
        self.assertEqual(self.beat(host)["why"], "config")

    def test_collecting_aborts(self):
        host = self.host()

        def boom(argv, limit=60, max_output=0):
            raise RuntimeError("boom")
        code, _, _ = self.run_host(host, runner=boom)
        self.assertEqual(code, 1)
        self.assertEqual(host.dog.calls[0]["body"], b"Host Settings: reading the add-ons failed")

    def test_the_state_cannot_be_written(self):
        host = self.host()
        os.mkdir(os.path.join(host.state, "last.json"))
        code, _, err = self.run_host(host)
        self.assertEqual(code, 1, err)
        self.assertEqual([c["url"] for c in host.dog.calls], [HC + "/fail"])
        self.assertEqual(host.dog.calls[0]["body"], b"Host Settings: the state could not be saved")
        self.assertFalse(os.path.exists(os.path.join(host.state, "status.json")))

    def test_a_signal_that_does_not_get_through_stays_apart(self):
        host = self.host(answers=["timeout"])
        host.items["cronjobs"][0]["state"] = "danger"
        code, out, _ = self.run_host(host)
        self.assertEqual(code, 0)
        self.assertEqual(len(host.dog.calls), hb.ATTEMPTS)
        beat = self.beat(host)
        self.assertEqual((beat["signal"], beat["result"]["reason"], beat["result"]["attempts"]), ("success", "timeout", 3))
        self.assertIn("watchdog: success, not accepted: timeout", out)
        sent = host.file("last.json")["sent"]
        self.assertEqual(sent["cronjobs#failed"]["level"], "danger", "the findings as without a watchdog")
        status = host.file("status.json")
        heartbeat = [i for i in status["items"] if i["id"] == "heartbeat"][0]
        self.assertEqual((heartbeat["state"], heartbeat["detail"]), ("warning", "The last signal did not reach the watchdog: no answer."))
        self.assertEqual(status["i18n"]["de"]["items"]["heartbeat"]["detail"], "Das letzte Signal erreichte den Wächter nicht: keine Antwort.")
        host.dog.answers = [(200, b"OK")]
        host.now += 300
        self.run_host(host)
        self.assertEqual(len(host.posts()), 1, "no message went out again")
        self.assertEqual(host.file("last.json")["sent"], sent)
        self.assertEqual({i["id"]: i["state"] for i in host.file("status.json")["items"]}["heartbeat"], "ok")

    def test_findings_and_gaps_are_no_failure(self):
        host = self.host()
        with open(os.path.join(host.dir, "profile.conf"), "w", encoding="utf-8") as f:
            f.write("roles = web\n")
        runner = Runner({CRON: hsproc.Result(1, "", "boom"), GAPS: host.answer("update-gaps")})
        code, _, _ = self.run_host(host, runner=runner)
        self.assertEqual(code, 0)
        body = host.posts()[0]["message"]
        self.assertIn("Check no longer possible · Security Monitor", body)
        self.assertIn("Status not readable", body)
        self.assertEqual(host.dog.calls[-1]["url"], HC, "told correctly: a success")

    def test_a_watchdog_alone(self):
        host = self.host("uptime-kuma", KUMA, conf="language = de\n")
        code, _, _ = self.run_host(host)
        self.assertEqual(code, 0)
        self.assertEqual(len(host.dog.calls), 1)
        self.assertEqual([c for c in host.http.calls if "ntfy" in c["url"]], [])
        status = host.file("status.json")
        self.assertEqual(status["i18n"]["de"]["summary"], "Kein Kanal: Nur ein Wächter bekommt nach jedem Lauf ein Signal.")

    def test_settings_that_do_not_hold(self):
        host = self.host()
        with open(host.beat, "w", encoding="utf-8") as f:
            f.write("adapter = healthchecks\nurl = http://hc-ping.com/abc\n")
        code, _, _ = self.run_host(host)
        self.assertEqual(code, 0)
        self.assertEqual(host.dog.calls, [])
        self.assertEqual(self.beat(host)["result"]["reason"], "invalid")
        self.assertEqual({i["id"]: i["state"] for i in host.file("status.json")["items"]}["heartbeat"], "warning")

    def test_semantic_errors_reach_the_status(self):
        for adapter, url, answer, reason in (("healthchecks", HC, (200, b"OK (not found)"), "not-found"),
                                             ("healthchecks", HC, (200, b"OK (rate limited)"), "rate-limited"),
                                             ("uptime-kuma", KUMA, (200, b'{"ok":false}'), "refused"),
                                             ("uptime-kuma", KUMA, (404, b'{"ok":false,"msg":"Monitor not found or not active."}'), "not-found")):
            with self.subTest(reason):
                host = self.host(adapter, url, answers=[answer])
                self.run_host(host)
                self.assertEqual(len(host.dog.calls), 1)
                self.assertEqual(self.beat(host)["result"]["reason"], reason)

    def test_the_test_signal(self):
        host = self.host()
        code, out, _ = self.run_host(host, "test", "heartbeat")
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual((result["channel"], result["adapter"], result["result"]["reason"]), ("heartbeat", "healthchecks", "accepted"))
        self.assertEqual([c["url"] for c in host.dog.calls], [HC + "/log"])
        self.assertEqual(host.file("test-heartbeat.json"), result)
        self.assertFalse(os.path.exists(os.path.join(host.state, "heartbeat.json")), "a test is no run")
        kuma = self.host("uptime-kuma", KUMA)
        self.run_host(kuma, "test", "heartbeat")
        self.assertIn("status=up", kuma.dog.calls[0]["url"])
        none = self.host(adapter=None)
        self.assertEqual(json.loads(self.run_host(none, "test", "heartbeat")[1])["error"], "not-set-up")
        self.assertEqual(none.dog.calls, [])

    def test_the_secret_stays_in_its_own_file(self):
        for adapter, url, secret in (("healthchecks", HC, SECRET_HC), ("uptime-kuma", KUMA, SECRET_KUMA)):
            with self.subTest(adapter):
                host = self.host(adapter, url, answers=["network"])
                outputs = []
                for argv in ((), ("test", "heartbeat"), ("test", "ntfy")):
                    code, out, err = self.run_host(host, *argv)
                    outputs += [out, err]
                    host.now += 300
                e = host.env(heartbeat_file=host.beat, http=host.dog, sleep=lambda s: None)
                notify.main(["run"], e)
                outputs.append(e.out.getvalue())
                for text in outputs:
                    self.assertNotIn(secret, text)
                for name in os.listdir(host.state):
                    with open(os.path.join(host.state, name), encoding="utf-8") as f:
                        self.assertNotIn(secret, f.read(), name)
                for path in (host.conf,):
                    with open(path, encoding="utf-8") as f:
                        self.assertNotIn(secret, f.read())
                self.assertTrue(all(secret not in " ".join(c) for c in e.runner.calls), "no argument holds it")
                self.assertTrue(any(secret in c["url"] for c in host.dog.calls), "it was used")


class OwnStatus(unittest.TestCase):
    """The item heartbeat of the own status: only with a watchdog, and by the board's rules."""

    def info(self, beat, channels=("ntfy",)):
        return {"channels": list(channels), "delivery": None, "access": {"state": "ok", "anon_read": "ok", "anon_write": "ok"},
                "run": {"at": "2026-10-19T19:00:00Z", "addons": 2}, "heartbeat": beat}

    def test_items_and_the_boards_rules(self):
        ok = {"ok": True, "code": 200, "reason": "accepted", "attempts": 1}
        cases = [
            (None, None, None),
            ({"invalid": False, "at": None, "signal": None, "result": None}, "none", "Set up; no signal sent yet."),
            ({"invalid": False, "at": "2026-10-19T19:00:01Z", "signal": "success", "result": ok}, "ok",
             "The watchdog took the last signal on 19 Oct at 19:00 UTC."),
            ({"invalid": False, "at": "2026-10-19T19:00:01Z", "signal": "failure", "result": ok}, "ok",
             "The watchdog was told on 19 Oct at 19:00 UTC that a run failed."),
            ({"invalid": False, "at": "2026-10-19T19:00:01Z", "signal": "success",
              "result": {"ok": False, "code": 200, "reason": "not-found", "attempts": 1}}, "warning",
             "The last signal did not reach the watchdog: unknown check."),
            ({"invalid": False, "at": "2026-10-19T19:00:01Z", "signal": "success",
              "result": {"ok": False, "code": 503, "reason": "http-503", "attempts": 3}}, "warning",
             "The last signal did not reach the watchdog: HTTP 503."),
            ({"invalid": True, "at": None, "signal": None, "result": None}, "warning",
             "The watchdog's settings do not hold: set it up again in Host Settings."),
        ]
        import hsstatus
        for beat, state, detail in cases:
            with self.subTest(detail):
                for lang in ("en", "de"):
                    doc = hsstatus.localize(hsstatus.status_file(self.info(beat), "0.1.0"), lang, NOW)
                    good, st = hslib.validate_status(json.dumps(doc), "host-settings")
                    self.assertTrue(good, st)
                    self.assertEqual(hsstatus.problems(doc), [])
                items = {i["id"]: i for i in hsstatus.document(self.info(beat), "en", "0.1.0")["items"]}
                if state is None:
                    self.assertNotIn("heartbeat", items)
                else:
                    self.assertEqual((items["heartbeat"]["state"], items["heartbeat"]["detail"]), (state, detail))
                    self.assertNotIn("http", json.dumps(items["heartbeat"]).replace("HTTP 503", ""), "no address")
        self.assertEqual(hsstatus.document(self.info(cases[2][0], ()), "en", "0.1.0")["summary"],
                         "No channel: only a watchdog gets a signal after every run.")


if __name__ == "__main__":
    unittest.main(verbosity=1)
