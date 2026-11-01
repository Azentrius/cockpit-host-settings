#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""host-settings.py, the fixed verbs of the page and of the Status Board, with stand-ins for
systemctl and ss: every argument is checked before anything is written; the settings file keeps
its format; the token comes from standard input only, lands with mode 0600 and is never printed;
the status verb answers without root, also without a status.json; switching the timer off says
so in the status; "install timer" needs a channel or a watchdog; the watchdog's URL comes from
standard input only, lands with mode 0600 and is never printed, also not by "show". Cockpit's menu
(hsmenu.py) is read as the bridge reads it, and a plan changes only order, label and null in one
scope's override files, keeps everything else, and is refused whole when anything in it is wrong."""

import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "lib")
sys.dont_write_bytecode = True
sys.path.insert(0, LIB)
import hsheartbeat  # noqa: E402
import hslib  # noqa: E402
import hsmenu  # noqa: E402
import hsproc  # noqa: E402
import hsstatus  # noqa: E402

spec = importlib.util.spec_from_file_location("host_settings", os.path.join(LIB, "host-settings.py"))
hs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hs)
TOKEN = "tk_testtokentesttokentesttokent"


def call(fn, *args, **kw):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = fn(*args, **kw)
        except hs.Fail as f:
            code = f.code
            err.write(f.en)
    return code, out.getvalue(), err.getvalue()


class Systemctl:
    def __init__(self, active=False):
        self.calls, self.active = [], active

    def __call__(self, *args, check=False):
        self.calls.append(list(args))

        class P:
            returncode = 0
            stdout = ""
            stderr = ""
        p = P()
        if args[0] == "is-active":
            p.returncode = 0 if self.active else 3
            p.stdout = "active\n" if self.active else "inactive\n"
        if args[0] == "show":
            p.stdout = "Mon 2026-10-19 19:05:00 UTC\n"
        return p


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.conf = os.path.join(self.dir, "etc", "notify.conf")
        self.token = os.path.join(self.dir, "etc", "ntfy.token")
        self.lock = os.path.join(self.dir, "lock")
        self.state = os.path.join(self.dir, "state")
        os.makedirs(self.state)
        self.saved = {k: getattr(hs, k, None) for k in ("need_root", "systemctl", "unit_exists", "journal_tail", "STATE_DIR",
                                                         "PROFILE", "HEARTBEAT")}
        hs.PROFILE = os.path.join(self.dir, "etc", "profile.conf")
        hs.HEARTBEAT = self.heartbeat = os.path.join(self.dir, "etc", "heartbeat.conf")
        hs.need_root = lambda: None
        self.sc = Systemctl()
        hs.systemctl = self.sc
        hs.unit_exists = lambda unit: True
        hs.journal_tail = lambda unit, n=10: "4 add-ons\n"
        hs.STATE_DIR = self.state

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(hs, k, v)

    def conf_text(self):
        with open(self.conf, encoding="utf-8") as f:
            return f.read()


class Settings(Base):
    def test_set_and_unset(self):
        code, out, _ = call(hs.do_set, ["ntfy", "https://ntfy.example.com/", "cockpit-h"], self.conf, self.lock)
        self.assertEqual(code, 0)
        self.assertEqual(out, "ntfy set up: https://ntfy.example.com, topic cockpit-h.\n")
        call(hs.do_set, ["mail", "ops@example.com"], self.conf, self.lock)
        call(hs.do_set, ["language", "de"], self.conf, self.lock)
        cfg, bad = hslib.parse_config(self.conf_text())
        self.assertEqual(cfg, {"ntfy_url": "https://ntfy.example.com", "ntfy_topic": "cockpit-h", "mail_to": "ops@example.com",
                               "language": "de"})
        self.assertEqual(bad, [])
        self.assertEqual(stat.S_IMODE(os.stat(self.conf).st_mode), 0o644)
        call(hs.do_unset, ["ntfy"], self.conf, self.lock)
        self.assertEqual(hslib.channels(hslib.parse_config(self.conf_text())[0]), ["mail"])
        call(hs.do_unset, ["mail"], self.conf, self.lock)
        self.assertEqual(hslib.parse_config(self.conf_text())[0]["language"], "de")

    def test_bad_values_change_nothing(self):
        for rest in (["ntfy", "https://u:p@h", "t"], ["ntfy", "https://h", "a/b"], ["ntfy", "https://h"],
                     ["mail", "nobody"], ["language", "fr"], ["sms", "x"], [], ["ntfy", "https://h", "t", "extra"]):
            with self.subTest(rest):
                code, _, _ = call(hs.do_set, rest, self.conf, self.lock)
                self.assertEqual(code, 64)
                self.assertFalse(os.path.exists(self.conf))
        for rest in ([], ["token"], ["ntfy", "x"]):
            self.assertEqual(call(hs.do_unset, rest, self.conf, self.lock)[0], 64)

    def test_unknown_lines_go_when_the_page_saves(self):
        os.makedirs(os.path.dirname(self.conf))
        with open(self.conf, "w", encoding="utf-8") as f:
            f.write("ntfy_topic = keep\nbogus line\n")
        call(hs.do_set, ["mail", "ops@example.com"], self.conf, self.lock)
        self.assertEqual(hslib.parse_config(self.conf_text()), (dict(hslib.parse_config("")[0], ntfy_topic="keep",
                                                                     mail_to="ops@example.com"), []))
        self.assertNotIn("bogus", self.conf_text())

    def test_the_lock(self):
        held = hs.Locked(self.lock).__enter__()
        try:
            self.assertEqual(call(hs.do_set, ["mail", "ops@example.com"], self.conf, self.lock)[0], 75)
        finally:
            held.__exit__(None, None, None)


class Token(Base):
    def test_from_standard_input_only(self):
        code, out, err = call(hs.do_token, ["set"], self.token, self.lock, io.BytesIO((TOKEN + "\n").encode()))
        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, out + err)
        with open(self.token, encoding="utf-8") as f:
            self.assertEqual(f.read(), TOKEN + "\n")
        self.assertEqual(stat.S_IMODE(os.stat(self.token).st_mode), 0o600)
        self.assertEqual(call(hs.do_token, ["remove"], self.token, self.lock)[0], 0)
        self.assertEqual(os.path.getsize(self.token), 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.token).st_mode), 0o600)

    def test_bad_tokens(self):
        for data in (b"", b"two words\n", b"a\nb\n", b"x" * 5000, "ä".encode(), b"\xff\xfe"):
            with self.subTest(data[:10]):
                code, out, err = call(hs.do_token, ["set"], self.token, self.lock, io.BytesIO(data))
                self.assertEqual(code, 64)
                self.assertFalse(os.path.exists(self.token))
        self.assertEqual(call(hs.do_token, ["set", TOKEN], self.token, self.lock)[0], 64, "never as an argument")
        self.assertEqual(call(hs.do_token, [], self.token, self.lock)[0], 64)


class Status(Base):
    def test_without_a_file(self):
        code, out, _ = call(hs.do_status, ["--lang", "de"], os.path.join(self.state, "status.json"))
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual((doc["addon"], doc["state"], doc["summary"]), ("host-settings", "none", "Noch nicht eingerichtet."))
        self.assertTrue(hslib.validate_status(out, "host-settings")[0])

    def test_with_a_file_and_out_of_date(self):
        info = {"channels": ["ntfy"], "delivery": None, "access": None, "run": {"at": "2026-10-19T19:00:00Z", "addons": 2}}
        path = os.path.join(self.state, "status.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(hsstatus.status_file(info, "0.1.0"), f)
        now = hslib.parse_time("2026-10-19T19:01:00Z")
        doc = json.loads(call(hs.do_status, ["--lang=en"], path, now)[1])
        self.assertEqual(doc["summary"], "Messages go to ntfy.", "not checked yet is no finding")
        self.assertEqual({i["id"]: i["state"] for i in doc["items"]},
                         {"setup": "ok", "ntfy": "none", "access": "none", "run": "ok"}, "a channel not confirmed is no finding")
        old = json.loads(call(hs.do_status, ["--lang", "de_DE"], path, now + 3600)[1])
        self.assertEqual({i["id"]: i["state"] for i in old["items"]}["run"], "warning")
        self.assertEqual(old["version"], hsstatus.version())

    def test_broken_files_and_wrong_calls(self):
        path = os.path.join(self.state, "status.json")
        for text in ("{", "[]", '{"schema": 1}'):
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            doc = json.loads(call(hs.do_status, [], path)[1])
            self.assertEqual(doc["summary"], "The state of Host Settings could not be read.")
        for rest in (["--lang"], ["--lang", "d e"], ["-x"], ["--lang", "en", "x"]):
            self.assertEqual(call(hs.do_status, rest, path)[0], 64, rest)


class Services(Base):
    def test_install_timer_needs_a_channel(self):
        code, _, err = call(hs.do_timer, "install", ["timer"], self.conf, self.state)
        self.assertEqual(code, 1)
        self.assertIn("Set up ntfy, e-mail or a watchdog first.", err)
        self.assertEqual(self.sc.calls, [])
        call(hs.do_set, ["mail", "ops@example.com"], self.conf, self.lock)
        code, out, _ = call(hs.do_timer, "install", ["timer"], self.conf, self.state)
        self.assertEqual(code, 0)
        self.assertEqual(self.sc.calls, [["enable", "--now", hs.TIMER], ["start", hs.SERVICE]])

    def test_remove_timer_says_so_in_the_status(self):
        code, _, _ = call(hs.do_timer, "remove", ["timer"], self.conf, self.state)
        self.assertEqual(code, 0)
        self.assertEqual(self.sc.calls, [["disable", "--now", hs.TIMER]])
        path = os.path.join(self.state, "status.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)
        doc = json.loads(call(hs.do_status, ["--lang", "de"], path)[1])
        self.assertEqual(doc["summary"], "Die regelmäßige Prüfung ist ausgeschaltet.")

    def test_check_and_test_read_the_result_of_the_unit(self):
        with open(os.path.join(self.state, "test-ntfy.json"), "w", encoding="utf-8") as f:
            json.dump({"at": hslib.iso(__import__("time").time()), "channel": "ntfy", "steps": []}, f)
        code, out, _ = call(hs.do_check, ["ntfy"])
        self.assertEqual(code, 0)
        self.assertEqual(self.sc.calls, [["start", "cockpit-host-settings-test@ntfy.service"]])
        result = [line for line in out.splitlines() if line.startswith("RESULT ")]
        self.assertEqual(json.loads(result[0][7:])["channel"], "ntfy")
        with open(os.path.join(self.state, "test-mail.json"), "w", encoding="utf-8") as f:
            json.dump({"at": "2020-01-01T00:00:00Z", "channel": "mail"}, f)
        self.assertEqual(call(hs.do_test, ["mail"])[0], 1, "an old result is no result")
        for fn, rest in ((hs.do_check, ["mail"]), (hs.do_check, []), (hs.do_test, ["ntfy"]), (hs.do_run, ["x"]),
                         (hs.do_timer, ["install", ["timers"]])):
            args = rest if fn is not hs.do_timer else rest
            code = call(fn, *args)[0] if fn is hs.do_timer else call(fn, args)[0]
            self.assertEqual(code, 64)

    def test_main_routes_and_refuses(self):
        for argv in ([], ["bogus"], ["set"], ["install", "timers"], ["show", "x"]):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(hs.main(argv), 64, argv)


class Show(Base):
    def test_show(self):
        call(hs.do_set, ["ntfy", "https://ntfy.example.com", "cockpit-h"], self.conf, self.lock)
        call(hs.do_token, ["set"], self.token, self.lock, io.BytesIO(TOKEN.encode()))
        last = {"format": 1, "baseline_at": "x", "run": {"at": "2026-10-19T19:00:00Z", "addons": 2}, "sent": {"a#": {}},
                "access": {"state": "ok", "steps": [{"id": "reach"}]}, "seen": {}, "delivery": None}
        with open(os.path.join(self.state, "last.json"), "w", encoding="utf-8") as f:
            json.dump(last, f)
        ss = lambda argv, limit=60, max_output=0: hsproc.Result(0, "LISTEN 0 100 127.0.0.1:25 0.0.0.0:*\n")  # noqa: E731
        code, out, _ = call(hs.do_show, [], self.conf, self.token, self.state, ss)
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertNotIn(TOKEN, out)
        self.assertTrue(doc["token"])
        self.assertEqual(doc["config"]["ntfy_topic"], "cockpit-h")
        self.assertEqual(doc["smtp"]["listening"], True)
        self.assertNotIn("sent", doc["last"])
        self.assertNotIn("steps", doc["last"]["access"])
        self.assertEqual(doc["docs"], hslib.NTFY_DOCS)
        self.assertEqual(doc["channels"], {"ntfy": {"state": "unknown"}}, "never accepted anything: unknown")
        self.assertEqual(call(hs.do_show, ["x"], self.conf)[0], 64)

    def test_show_judges_each_channel_against_the_settings_set_now(self):
        call(hs.do_set, ["ntfy", "https://ntfy.example.com", "cockpit-h"], self.conf, self.lock)
        call(hs.do_set, ["mail", "ops@example.com"], self.conf, self.lock)
        ss = lambda argv, limit=60, max_output=0: hsproc.Result(0, "")  # noqa: E731
        # The last.json of 0.2.0: its delivery is taken in as the next run takes it, nothing written.
        last = {"format": 1, "baseline_at": "x", "run": {"at": "2026-10-19T19:00:00Z", "addons": 2},
                "delivery": {"at": "2026-10-19T18:50:00Z", "results": {"ntfy": {"ok": True, "reason": "sent"},
                                                                        "mail": {"ok": False, "reason": "no-mta"}}}}
        path = os.path.join(self.state, "last.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(last, f)
        doc = json.loads(call(hs.do_show, [], self.conf, self.token, self.state, ss)[1])
        self.assertEqual(doc["channels"], {"ntfy": {"state": "unknown"},
                                           "mail": {"state": "failed", "since": "2026-10-19T18:50:00Z",
                                                    "at": "2026-10-19T18:50:00Z", "via": "run", "reason": "no-mta"}})
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), last, "show writes nothing")
        # Records of this version: a confirmation for another address does not count, never the target.
        last["channels"] = {"ntfy": {"ok": {"at": "2026-10-19T19:00:00Z", "via": "test",
                                            "target": "https://ntfy.example.com/cockpit-h"}},
                            "mail": {"ok": {"at": "2026-10-19T19:00:00Z", "via": "run", "target": "old@example.com"}}}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(last, f)
        out = call(hs.do_show, [], self.conf, self.token, self.state, ss)[1]
        self.assertEqual(json.loads(out)["channels"], {"ntfy": {"state": "ok", "at": "2026-10-19T19:00:00Z", "via": "test",
                                                                "recovered": None},
                                                       "mail": {"state": "unknown"}})
        self.assertNotIn("old@example.com", out)

    def test_smtp_listening(self):
        def ss(out, rc=0):
            return lambda argv, limit=60: hsproc.Result(rc, out)
        cases = (("LISTEN 0 100 127.0.0.1:25 0.0.0.0:*\n", True), ("LISTEN 0 100 [::1]:25 [::]:*\n", True),
                 ("LISTEN 0 100 0.0.0.0:25 0.0.0.0:*\n", True), ("LISTEN 0 4096 *:25 *:*\n", True),
                 ("LISTEN 0 100 127.0.0.53%lo:53 0.0.0.0:*\n", False), ("LISTEN 0 100 192.0.2.10:25 0.0.0.0:*\n", False),
                 ("LISTEN 0 100 127.0.0.1:2525 0.0.0.0:*\n", False), ("", False))
        for out, want in cases:
            self.assertEqual(hs.smtp_listening(ss(out)), want, out)
        self.assertIsNone(hs.smtp_listening(ss("", 1)))


KUMA = "http://10.8.0.2:3001/api/push/pushpushpushpush"
KUMA_ID = "pushpushpushpush"
HC = "https://hc.example.net/ping/11111111-2222-4333-8444-555555555555"


class Heartbeat(Base):
    """The watchdog: heartbeat.conf through the fixed verbs, a secret like the token."""

    def set(self, adapter, data, *extra):
        stdin = io.BytesIO(data if isinstance(data, bytes) else data.encode())
        return call(hs.do_heartbeat, ["set", adapter, *extra], self.heartbeat, self.lock, stdin)

    def text(self):
        with open(self.heartbeat, encoding="utf-8") as f:
            return f.read()

    def test_from_standard_input_only(self):
        code, out, err = self.set("uptime-kuma", KUMA + "?status=up&msg=OK&ping=\n")
        self.assertEqual(code, 0, err)
        self.assertNotIn(KUMA_ID, out + err)
        self.assertIn("Watchdog set up: Uptime Kuma.", out)
        self.assertIn("The address is not encrypted (http): allowed because it is in a private network.", out)
        self.assertEqual(hsheartbeat.parse_conf(self.text()), {"adapter": "uptime-kuma", "url": KUMA})
        self.assertEqual(stat.S_IMODE(os.stat(self.heartbeat).st_mode), 0o600)
        code, out, _ = self.set("healthchecks", HC)
        self.assertEqual(code, 0)
        self.assertNotIn("not encrypted", out)
        self.assertEqual(hsheartbeat.parse_conf(self.text())["adapter"], "healthchecks")
        code, out, _ = self.set("uptime-kuma", "http://127.0.0.1:3001/api/push/abc")
        self.assertIn("The watchdog is this server itself", out)

    def test_refusals_change_nothing(self):
        self.set("healthchecks", HC)
        before = self.text()
        cases = [("healthchecks", "http://hc-ping.com/abc", "Plain http:// is allowed only to an IP address of a private network"),
                 ("uptime-kuma", "https://kuma.example.net/dashboard/1", "ends in /api/push/TOKEN"),
                 ("healthchecks", HC + "/fail", "without /fail"),
                 ("healthchecks", HC + "?create=1", "This query"),
                 ("healthchecks", "https://u:p@hc-ping.com/abc", "The address is not valid"),
                 ("healthchecks", "", "missing"), ("healthchecks", HC + "\n" + HC, "missing"),
                 ("healthchecks", b"\xff\xfe", "missing"), ("healthchecks", "x" * 5000, "missing")]
        for adapter, data, why in cases:
            with self.subTest(data[:40]):
                code, out, err = self.set(adapter, data)
                self.assertEqual(code, 64)
                self.assertIn(why, err)
                self.assertEqual(self.text(), before)
        self.assertEqual(self.set("healthchecks", "", HC)[0], 64, "never as an argument")
        self.assertEqual(self.set("pushover", HC)[0], 64)
        for rest in ([], ["set"], ["remove", "x"], ["test", "x"], ["show"]):
            self.assertEqual(call(hs.do_heartbeat, rest, self.heartbeat, self.lock)[0], 64, rest)

    def test_remove_says_what_the_watchdog_will_do(self):
        self.set("healthchecks", HC)
        code, out, _ = call(hs.do_heartbeat, ["remove"], self.heartbeat, self.lock)
        self.assertEqual(code, 0)
        self.assertIn("Pause or delete the check at the watchdog", out)
        self.assertEqual(os.path.getsize(self.heartbeat), 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.heartbeat).st_mode), 0o600)
        self.assertEqual(call(hs.do_heartbeat, ["remove"], self.heartbeat, self.lock)[1], "Nothing changed.\n")

    def test_the_test_signal_runs_in_its_unit(self):
        with open(os.path.join(self.state, "test-heartbeat.json"), "w", encoding="utf-8") as f:
            json.dump({"at": hslib.iso(__import__("time").time()), "channel": "heartbeat", "adapter": "healthchecks",
                       "result": {"ok": True, "code": 200, "reason": "accepted", "attempts": 1}}, f)
        code, out, _ = call(hs.do_heartbeat, ["test"], self.heartbeat, self.lock)
        self.assertEqual(code, 0)
        self.assertEqual(self.sc.calls, [["start", "cockpit-host-settings-test@heartbeat.service"]])
        self.assertEqual(json.loads([x for x in out.splitlines() if x.startswith("RESULT ")][0][7:])["adapter"], "healthchecks")

    def test_show_never_the_address(self):
        self.set("uptime-kuma", KUMA)
        with open(os.path.join(self.state, "heartbeat.json"), "w", encoding="utf-8") as f:
            json.dump({"at": "2026-10-19T19:00:01Z", "adapter": "uptime-kuma", "signal": "success", "why": None,
                       "result": {"ok": True, "code": 200, "reason": "accepted", "attempts": 1}}, f)
        ss = lambda argv, limit=60, max_output=0: hsproc.Result(0, "")  # noqa: E731
        code, out, _ = call(hs.do_show, [], self.conf, self.token, self.state, ss)
        self.assertEqual(code, 0)
        self.assertNotIn(KUMA_ID, out)
        view = json.loads(out)["heartbeat"]
        self.assertEqual({k: view[k] for k in ("configured", "invalid", "adapter", "encrypted", "local", "test")},
                         {"configured": True, "invalid": False, "adapter": "uptime-kuma", "encrypted": False, "local": False,
                          "test": None})
        self.assertEqual(view["last"]["signal"], "success")
        with open(self.heartbeat, "w", encoding="utf-8") as f:
            f.write("adapter = uptime-kuma\nurl = http://kuma.example.net/api/push/" + KUMA_ID + "\n")
        out = call(hs.do_show, [], self.conf, self.token, self.state, ss)[1]
        self.assertNotIn(KUMA_ID, out)
        self.assertEqual({k: json.loads(out)["heartbeat"][k] for k in ("configured", "invalid", "adapter")},
                         {"configured": False, "invalid": True, "adapter": None})

    def test_the_timer_with_a_watchdog_alone(self):
        self.set("healthchecks", HC)
        code, out, _ = call(hs.do_timer, "install", ["timer"], self.conf, self.state)
        self.assertEqual(code, 0)
        self.assertIn("The watchdog gets a signal after every run.", out)
        self.assertEqual(self.sc.calls, [["enable", "--now", hs.TIMER], ["start", hs.SERVICE]])
        code, out, _ = call(hs.do_timer, "remove", ["timer"], self.conf, self.state)
        self.assertIn("raises an alarm once its grace time is over", out)
        call(hs.do_heartbeat, ["remove"], self.heartbeat, self.lock)
        self.assertNotIn("alarm", call(hs.do_timer, "remove", ["timer"], self.conf, self.state)[1])
        self.assertEqual(call(hs.do_timer, "install", ["timer"], self.conf, self.state)[0], 1)

    def test_main_routes(self):
        for argv in (["heartbeat"], ["heartbeat", "set"], ["heartbeat", "set", "sms"], ["heartbeat", "list"]):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(hs.main(argv), 64, argv)


NOW = hslib.parse_time("2026-10-19T19:00:00Z")


class Profile(Base):
    """The target profile: profile.conf through the fixed verbs, and the status field."""

    def setUp(self):
        super().setUp()
        self.profile = os.path.join(self.dir, "etc", "profile.conf")

    def run_profile(self, *rest, reason=None):
        stdin = io.BytesIO(reason if isinstance(reason, bytes) else (reason or "").encode())
        return call(hs.do_profile, list(rest), self.profile, self.lock, stdin, NOW)

    def text(self, path=None):
        with open(path or self.profile, encoding="utf-8") as f:
            return f.read()

    def write(self, text):
        os.makedirs(os.path.dirname(self.profile), exist_ok=True)
        with open(self.profile, "w", encoding="utf-8") as f:
            f.write(text)

    def status(self, lang="en"):
        code, out, _ = call(hs.do_status, ["--lang", lang], os.path.join(self.state, "status.json"), NOW, self.profile)
        self.assertEqual(code, 0)
        return out

    def test_roles_expect_skip_and_the_status_field(self):
        self.assertEqual(self.run_profile("roles", "web")[:2], (0, "Profile saved: roles web.\n"))
        self.assertEqual(stat.S_IMODE(os.stat(self.profile).st_mode), 0o644)
        self.assertEqual(self.run_profile("expect", "add", "security/exposure")[0], 0)
        code, out, _ = self.run_profile("skip", "add", "update-gaps/cve", "2026-12-31", reason="network data off on purpose\n")
        self.assertEqual((code, out), (0, "Left out on purpose until 2026-12-31: update-gaps/cve.\n"))
        self.assertEqual(self.run_profile("skip", "add", "security/fail2ban", reason="CrowdSec  instead\t")[0], 0)
        prof, bad = hslib.parse_profile(self.text())
        self.assertEqual(bad, [])
        self.assertEqual(prof, {"roles": ["web"], "extra": ["security/exposure"], "set_at": "2026-10-19T19:00:00Z",
                                "skip": [{"check": "update-gaps/cve", "until": "2026-12-31", "reason": "network data off on purpose"},
                                         {"check": "security/fail2ban", "until": None, "reason": "CrowdSec instead"}]})
        out = self.status()
        doc = json.loads(out)
        self.assertEqual(doc["features"], ["profile"])
        self.assertEqual(doc["profile"], {
            "roles": ["web"], "expect": ["host-settings", "update-gaps", "security", "cronjobs", "image-updates", "security/exposure"],
            "skip": [{"check": "update-gaps/cve", "reason": "network data off on purpose", "until": "2026-12-31T00:00:00Z"},
                     {"check": "security/fail2ban", "reason": "CrowdSec instead"}],
            "set_at": "2026-10-19T19:00:00Z"})
        # Status Board's rules (hslib.validate_profile, the twin of the board's validateProfile) keep
        # all of it: nothing left out, nothing changed
        good, st = hslib.validate_status(out, "host-settings")
        self.assertTrue(good, st)
        self.assertEqual(st["ext_dropped"], 0)
        self.assertEqual(st["profile"], {"roles": doc["profile"]["roles"], "expect": doc["profile"]["expect"],
                                         "skip": [{"check": "update-gaps/cve", "reason": "network data off on purpose",
                                                   "until": hslib.parse_time("2026-12-31T00:00:00Z")},
                                                  {"check": "security/fail2ban", "reason": "CrowdSec instead", "until": None}],
                                         "set_at": NOW})
        self.assertEqual(json.loads(self.status("de"))["profile"], doc["profile"])

    def test_no_profile_no_field(self):
        for setup in (None, "# only a comment\nexpect = security/exposure\n"):
            if setup is not None:
                self.write(setup)
            doc = json.loads(self.status())
            self.assertNotIn("profile", doc)
            self.assertNotIn("features", doc)
            good, st = hslib.validate_status(json.dumps(doc), "host-settings")
            self.assertTrue(good)
            self.assertEqual((st["profile"], st["ext_dropped"]), (None, 0))
        # a profile with no role is a profile: the add-ons of every host (and the line expect, which
        # counts from now on)
        self.assertEqual(self.run_profile("roles")[0], 0)
        self.assertEqual(json.loads(self.status())["profile"]["expect"], list(hslib.BASE_EXPECT) + ["security/exposure"])
        # a file nobody may read: no field, and the status still answers
        os.chmod(self.profile, 0)
        if os.geteuid() != 0:
            self.assertNotIn("profile", json.loads(self.status()))

    def test_every_argument_is_checked_before_anything_is_written(self):
        refused = [("roles", "mail"), ("roles", "Web"), ("roles", "web;rm"), ("roles", "../x"), ("roles", "web", "x" * 41),
                   ("expect", "add", "Security"), ("expect", "add", "a/b/c"), ("expect", "add", "-x"), ("expect", "add", ""),
                   ("expect", "remove", "security/"), ("expect", "drop", "security"), ("expect", "add"),
                   ("skip", "add", "security/Exposure"), ("skip", "add", "x", "2026-13-01"), ("skip", "add", "x", "31.12.2026"),
                   ("skip", "add", "x", "2026-10-19"), ("skip", "add", "x", "2020-01-01"), ("skip", "add", "x", "2026-12-31", "y"),
                   ("skip", "add"), ("skip", "remove", "x y"), ("skip",), ("remove", "x"), ("show", "x"), (), ("bogus",)]
        for rest in refused:
            with self.subTest(rest):
                code, _, _ = self.run_profile(*rest, reason="r")
                self.assertEqual(code, 64)
                self.assertFalse(os.path.exists(self.profile))
        code, _, err = self.run_profile("roles", "web", "mail")
        self.assertIn("Unknown role: mail. Known roles: web, database, build, bastion.", err)
        # a skip names one check of an add-on, never an add-on alone
        for rest in (("skip", "add", "security"), ("skip", "add", "image-updates", "2026-12-31"), ("skip", "remove", "security")):
            code, _, err = self.run_profile(*rest, reason="r")
            self.assertEqual(code, 64, rest)
            self.assertIn("Only a check of an add-on can be left out, as ADDON/CHECK (update-gaps/cve); an add-on alone "
                          "cannot be left out.", err)
        self.assertEqual(self.run_profile("roles", "web")[0], 0)
        before = self.text()
        for reason in (b"", b"\n", b"two\nlines\n", b"x" * 5000, b"\xff\xfe", chr(0x200B).encode()):
            with self.subTest(reason[:12]):
                self.assertEqual(self.run_profile("skip", "add", "update-gaps/cve", reason=reason)[0], 64)
                self.assertEqual(self.text(), before)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(hs.main(["profile", "bogus"]), 64)

    def test_unknown_roles_are_refused_and_a_profile_comes_first(self):
        self.assertEqual(self.run_profile("expect", "add", "image-updates")[:1], (1,))
        self.assertIn("set the roles first", self.run_profile("skip", "add", "update-gaps/cve", reason="r")[2])
        self.assertEqual(self.run_profile("skip", "remove", "update-gaps/cve")[0], 1)
        self.assertFalse(os.path.exists(self.profile))
        # a role from the file that this version does not know: the line does not count
        self.write("roles = web, mail\n")
        prof, bad = hslib.parse_profile(self.text())
        self.assertEqual((prof["roles"], bad), (None, [1]))
        self.assertNotIn("profile", json.loads(self.status()))

    def test_comments_and_other_lines_stay_line_by_line(self):
        self.write("# my notes\n"
                   "roles = web\n"
                   "\n"
                   "colour = blue\n"
                   "expect = security/exposure\n"
                   "# the CVE feed is off\n"
                   "skip = update-gaps/cve: network data off\n"
                   "roles = nonsense\n"
                   "expect = update-gaps/held\n"
                   "skip = security: an add-on alone does not count\n")
        self.assertEqual(hslib.parse_profile(self.text())[1], [4, 8, 10])
        self.assertEqual(self.run_profile("roles", "web", "database")[0], 0)
        self.assertEqual(self.run_profile("expect", "remove", "update-gaps/held")[0], 0)
        self.assertEqual(self.run_profile("skip", "add", "update-gaps/cve", "2026-12-31", reason="network data off")[0], 0)
        self.assertEqual(self.run_profile("expect", "add", "cronjobs/cron")[0], 0)
        self.assertEqual(self.text(), "# my notes\n"
                                      "roles = web, database\n"
                                      "\n"
                                      "colour = blue\n"
                                      "expect = security/exposure\n"
                                      "# the CVE feed is off\n"
                                      "skip = update-gaps/cve until 2026-12-31: network data off\n"
                                      "roles = nonsense\n"
                                      "skip = security: an add-on alone does not count\n"
                                      "set_at = 2026-10-19T19:00:00Z\n"
                                      "expect = cronjobs/cron\n")
        # the profile removed: its lines go, every other line stays
        self.assertEqual(self.run_profile("remove")[:2], (0, "Profile removed: this host has no target profile any more.\n"))
        self.assertEqual(self.text(), "# my notes\n\ncolour = blue\n# the CVE feed is off\nroles = nonsense\n"
                                      "skip = security: an add-on alone does not count\n")
        self.assertNotIn("profile", json.loads(self.status()))
        self.assertEqual(self.run_profile("remove")[:2], (0, "Nothing changed.\n"))

    def test_a_new_file_and_a_change_that_changes_nothing(self):
        self.assertEqual(self.run_profile("remove")[:2], (0, "Nothing changed.\n"))
        self.assertFalse(os.path.exists(self.profile))
        self.run_profile("roles", "build")
        self.assertEqual(self.text(), "\n".join(hslib.PROFILE_HEADER) + "\nroles = build\nset_at = 2026-10-19T19:00:00Z\n")
        os.remove(self.profile + ".bak") if os.path.exists(self.profile + ".bak") else None
        for rest in (("roles", "build"), ("expect", "add", "security"), ("expect", "remove", "update-gaps/held"),
                     ("skip", "remove", "update-gaps/cve")):
            with self.subTest(rest):
                self.assertEqual(self.run_profile(*rest)[:2], (0, "Nothing changed.\n"))
                self.assertFalse(os.path.exists(self.profile + ".bak"), "nothing written")
        code, _, err = self.run_profile("expect", "remove", "security")
        self.assertEqual(code, 1)
        self.assertIn("is expected by the roles or by every host", err)
        # a second skip of the same check replaces the first
        self.run_profile("skip", "add", "security/fail2ban", reason="one")
        self.run_profile("skip", "add", "security/fail2ban", "2027-01-01", reason="two")
        self.assertEqual(hslib.parse_profile(self.text())[0]["skip"],
                         [{"check": "security/fail2ban", "until": "2027-01-01", "reason": "two"}])

    def test_atomic_write_and_backup(self):
        self.run_profile("roles", "web")
        first = self.text()
        self.run_profile("roles", "database")
        self.assertEqual(self.text(self.profile + ".bak"), first, "the file as it was")
        self.assertEqual(stat.S_IMODE(os.stat(self.profile + ".bak").st_mode), 0o644)
        second = self.text()
        saved = hs.os.replace

        def broken(src, dst):
            if dst == self.profile:
                raise OSError(28, "No space left on device")
            return saved(src, dst)
        hs.os.replace = broken
        try:
            with self.assertRaises(OSError):
                hs.do_profile(["roles", "build"], self.profile, self.lock, None, NOW)
        finally:
            hs.os.replace = saved
        self.assertEqual(self.text(), second, "a write that fails leaves the file as it was")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.profile))), ["profile.conf", "profile.conf.bak"],
                         "no temporary file is left")

    def test_the_lock_and_the_limits(self):
        self.run_profile("roles", "web")
        held = hs.Locked(self.lock).__enter__()
        try:
            self.assertEqual(self.run_profile("roles", "build")[0], 75)
        finally:
            held.__exit__(None, None, None)
        for n in range(hslib.MAX_SKIP):
            self.assertEqual(self.run_profile("skip", "add", f"security/c{n}", reason="r")[0], 0)
        self.assertEqual(self.run_profile("skip", "add", "security/one-more", reason="r")[0], 1)
        for n in range(hslib.MAX_EXPECT - len(hslib.resolve_expect(["web"]))):
            self.assertEqual(self.run_profile("expect", "add", f"extra-{n}")[0], 0)
        self.assertEqual(self.run_profile("expect", "add", "one-more")[0], 1)
        doc = json.loads(self.status())
        self.assertEqual((len(doc["profile"]["expect"]), len(doc["profile"]["skip"])), (hslib.MAX_EXPECT, hslib.MAX_SKIP))
        good, st = hslib.validate_status(json.dumps(doc), "host-settings")
        self.assertEqual(st["ext_dropped"], 0, "at the limits nothing is left out either")
        self.assertLess(len(json.dumps(doc).encode()), hslib.MAX_OUTPUT)

    def test_show_needs_no_root_and_show_names_the_profile(self):
        self.run_profile("roles", "web")
        self.run_profile("skip", "add", "update-gaps/cve", "2026-10-20", reason="for a day")
        hs.need_root = lambda: (_ for _ in ()).throw(hs.Fail(77, "", "root"))
        code, out, _ = self.run_profile("show")
        self.assertEqual(code, 0)
        view = json.loads(out)
        self.assertEqual((view["set"], view["roles"], view["extra"], view["set_at"], view["bad_lines"]),
                         (True, ["web"], [], "2026-10-19T19:00:00Z", []))
        self.assertEqual(view["expect"], hslib.resolve_expect(["web"]))
        self.assertEqual(view["skip"], [{"check": "update-gaps/cve", "until": "2026-10-20", "reason": "for a day", "active": True}])
        self.assertEqual(view["catalog"]["roles"], [["web", ["image-updates"]], ["database", ["image-updates"]], ["build", []],
                                                    ["bastion", []]])
        self.assertEqual(json.loads(call(hs.do_profile, ["show"], self.profile, self.lock, None, NOW + 86400)[1])["skip"][0]["active"],
                         False, "it ends on its day")
        self.assertEqual(self.run_profile("roles", "build")[0], 77, "a change needs root")
        hs.need_root = lambda: None
        ss = lambda argv, limit=60, max_output=0: hsproc.Result(0, "")  # noqa: E731
        doc = json.loads(call(hs.do_show, [], self.conf, self.token, self.state, ss, self.profile)[1])
        self.assertEqual(doc["profile"]["roles"], ["web"])
        self.assertTrue(doc["profile"]["set"])


class Menu(Base):
    """Cockpit's menu: packages and override files in a throw-away tree, found through the same
    variables the bridge reads (HOME, XDG_DATA_DIRS, XDG_CONFIG_DIRS)."""

    SYSTEMD = {"menu": {"index": {"label": "Overview", "order": -2}, "logs": {"label": "Logs", "order": 20},
                        "services": {"label": "Services", "order": 100}},
               "tools": {"terminal": {"label": "Terminal"}}}

    def setUp(self):
        super().setUp()
        self.env = {"HOME": os.path.join(self.dir, "home"),
                    "XDG_DATA_DIRS": os.path.join(self.dir, "usr-local") + ":" + os.path.join(self.dir, "usr"),
                    "XDG_CONFIG_DIRS": os.path.join(self.dir, "etc")}
        self.backup = os.path.join(self.dir, "backup")
        self.package("usr", "systemd", self.SYSTEMD)
        self.package("usr", "users", {"menu": {"index": {"label": "Accounts", "order": 70}}})

    def write(self, path, doc):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(doc if isinstance(doc, str) else json.dumps(doc))

    def package(self, where, folder, manifest, override=None):
        root = os.path.join(self.dir, where, "cockpit", folder) if where != "home" else \
            os.path.join(self.env["HOME"], ".local", "share", "cockpit", folder)
        self.write(os.path.join(root, "manifest.json"), manifest)
        if override is not None:
            self.write(os.path.join(root, "override.json"), override)

    def path(self, scope, folder):
        return hsmenu.override_path(scope, folder, self.env)

    def override(self, scope, folder, doc):
        self.write(self.path(scope, folder), doc)

    def read(self, scope, folder):
        with open(self.path(scope, folder), encoding="utf-8") as f:
            return json.load(f)

    def show(self):
        code, out, err = call(hs.do_menu, ["show"], self.env)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def entry(self, view, section, key, folder="systemd"):
        return next(e for e in view["entries"] if (e["section"], e["key"], e["dir"]) == (section, key, folder))

    def apply(self, scope, wanted, base=None, euid=1000, raw=None, now=NOW):
        if raw is None:
            sha = {f["dir"]: f["sha256"] for f in self.show()["files"] if f["scope"] == scope}
            base = base if base is not None else {folder: sha.get(folder, "") for folder in wanted}
            raw = json.dumps({"base": base, "set": wanted}).encode()
        return call(hs.do_menu, ["apply", scope], self.env, io.BytesIO(raw), self.backup, now, euid)

    def tree(self):
        return sorted(os.path.relpath(os.path.join(d, f), self.dir) for d, _, fs in os.walk(self.dir) for f in fs
                      if not f.startswith("lock"))

    def test_show_reads_like_the_bridge(self):
        # Same name twice: the higher priority wins, unless its conditions do not hold.
        self.package("usr-local", "users-old", {"name": "users", "priority": 0, "menu": {"index": {"label": "Old"}}})
        self.package("home", "mine", {"menu": {"index": {"label": "Mine", "order": 3}},
                                      "conditions": [{"path-exists": self.dir}]})
        self.package("usr-local", "gone", {"conditions": [{"any": [{"path-exists": "/nonexistent-x"},
                                                                   {"path-not-exists": self.dir}]}],
                                           "menu": {"index": {"label": "Gone"}}})
        self.package("usr", "broken", {"conditions": [{"path-exists": 3}], "menu": {"index": {"label": "Broken"}}})
        # The package's own override.json counts as part of it; then system, then user, the later winning.
        self.package("usr", "storaged", {"menu": {"index": {"label": "Storage", "order": 30}}},
                     override={"menu": {"index": {"order": 31}}})
        self.override("system", "systemd", {"menu": {"logs": {"order": 5}, "services": None, "old": {"order": 3}},
                                            "preload": ["index"]})
        self.override("user", "systemd", {"menu": {"logs": {"order": 7, "label": "Journal"}, "older": None}})
        self.override("system", "users", "{ not json")
        self.override("system", "nothing-here", {"menu": {"index": None}})
        view = self.show()
        served = sorted({e["dir"] for e in view["entries"]})
        self.assertEqual(served, ["mine", "storaged", "systemd", "users"])
        self.assertEqual(self.entry(view, "menu", "index", "users")["base"]["label"], "Accounts",
                         "priority 1 beats 0; the broken override is left out as by the bridge")
        logs = self.entry(view, "menu", "logs")
        self.assertEqual((logs["base"], logs["system"], logs["user"], logs["effective"]),
                         ({"label": "Logs", "order": 20}, {"order": 5}, {"order": 7, "label": "Journal"},
                          {"label": "Journal", "order": 7, "hidden": False}))
        services = self.entry(view, "menu", "services")
        self.assertEqual((services["system"], services["user"], services["effective"]["hidden"]), ({"hidden": True}, None, True))
        self.assertEqual(self.entry(view, "tools", "terminal")["effective"], {"label": "Terminal", "order": None, "hidden": False})
        self.assertEqual(self.entry(view, "menu", "index", "storaged")["base"], {"label": "Storage", "order": 31})
        self.assertEqual(sorted((o["scope"], o["key"], o["phantom"]) for o in view["orphans"]),
                         [("system", "old", True), ("user", "older", False)])
        files = {(f["scope"], f["dir"]): f for f in view["files"]}
        self.assertEqual(sorted(files), [("system", "nothing-here"), ("system", "systemd"), ("system", "users"),
                                         ("user", "systemd")])
        self.assertEqual((files[("system", "systemd")]["foreign"], files[("system", "systemd")]["valid"]), (1, True))
        self.assertEqual((files[("system", "users")]["valid"], files[("system", "users")]["error"]), (False, "invalid"))
        self.assertFalse(files[("system", "nothing-here")]["served"])
        self.assertEqual(len(files[("user", "systemd")]["sha256"]), 64)
        self.assertEqual(view["paths"], {"system": os.path.join(self.dir, "etc", "cockpit"),
                                         "user": os.path.join(self.env["HOME"], ".config", "cockpit")})
        hs.need_root = lambda: (_ for _ in ()).throw(hs.Fail(77, "", "root"))
        self.assertEqual(call(hs.do_menu, ["show"], self.env)[0], 0, "show needs no root")

    def test_apply_for_all_users_keeps_everything_else(self):
        before = {"menu": {"logs": {"order": 5, "docs": [{"label": "Journal", "url": "https://example.com/"}]},
                           "index": None},
                  "preload": ["index"], "bridges": [{"spawn": ["true"]}]}
        self.override("system", "systemd", before)
        code, out, err = self.apply("system", {"systemd": {"menu": {"services": {"order": 1}, "logs": {"label": "Journal"}},
                                                           "tools": {"terminal": None}}})
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("system", "systemd"),
                         {"menu": {"logs": {"docs": [{"label": "Journal", "url": "https://example.com/"}], "label": "Journal"},
                                   "services": {"order": 1}},
                          "preload": ["index"], "bridges": [{"spawn": ["true"]}], "tools": {"terminal": None}})
        self.assertEqual(stat.S_IMODE(os.stat(self.path("system", "systemd")).st_mode), 0o644)
        self.assertIn(self.path("system", "systemd") + ": changed.", out)
        kept = [os.path.join(d, f) for d, _, fs in os.walk(self.backup) for f in fs]
        self.assertEqual(len(kept), 1)
        with open(kept[0], encoding="utf-8") as f:
            self.assertEqual(json.load(f), before, "the previous version is kept")
        self.assertEqual(stat.S_IMODE(os.stat(kept[0]).st_mode), 0o600)
        view = self.show()
        self.assertTrue(self.entry(view, "tools", "terminal")["effective"]["hidden"])
        self.assertEqual(self.entry(view, "menu", "index")["effective"]["hidden"], False, "a null not in the plan goes")

    def test_hide_show_again_and_an_empty_file_goes(self):
        self.assertEqual(self.apply("system", {"systemd": {"menu": {"logs": None}}})[0], 0)
        self.assertEqual(self.read("system", "systemd"), {"menu": {"logs": None}})
        self.assertTrue(self.entry(self.show(), "menu", "logs")["effective"]["hidden"])
        code, out, _ = self.apply("system", {}, base={"systemd": self.show()["files"][0]["sha256"]})
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.path("system", "systemd")))
        self.assertIn("removed, nothing left in it", out)
        self.assertFalse(self.entry(self.show(), "menu", "logs")["effective"]["hidden"])
        code, out, _ = self.apply("system", {}, base={"systemd": ""})
        self.assertEqual((code, out.splitlines()[0]), (0, "Nothing changed."))
        self.assertEqual(self.apply("system", {"users": {"menu": {"index": {"order": 2.0}}},
                                               "systemd": {"menu": {"logs": {"order": 20.5}}}})[0], 0)
        self.assertEqual((self.read("system", "users"), self.read("system", "systemd")),
                         ({"menu": {"index": {"order": 2}}}, {"menu": {"logs": {"order": 20.5}}}))

    def test_just_for_me_writes_the_users_own_file_and_never_as_root(self):
        code, _, err = self.apply("user", {"systemd": {"menu": {"logs": {"order": 1}}}}, euid=0)
        self.assertEqual(code, 64)
        self.assertIn("Just for me", err)
        hs.need_root = lambda: (_ for _ in ()).throw(hs.Fail(77, "", "root"))
        self.assertEqual(self.apply("system", {"systemd": {"menu": {"logs": {"order": 1}}}})[0], 77)
        self.assertEqual(self.tree(), ["usr/cockpit/systemd/manifest.json", "usr/cockpit/users/manifest.json"])
        self.assertEqual(self.apply("user", {"systemd": {"menu": {"logs": {"order": 1}}}})[0], 0)
        self.assertEqual(self.read("user", "systemd"), {"menu": {"logs": {"order": 1}}})
        self.assertTrue(self.path("user", "systemd").startswith(os.path.join(self.env["HOME"], ".config", "cockpit")))
        self.assertFalse(os.path.exists(self.backup), "no copies of a user's own files")
        self.assertEqual(self.entry(self.show(), "menu", "logs")["user"], {"order": 1})

    def test_plans_that_are_refused_change_nothing(self):
        self.override("system", "systemd", {"menu": {"logs": {"order": 5}}})
        sha = self.show()["files"][0]["sha256"]
        tree = self.tree()
        bad = [
            {"systemd": {"bridges": [{"spawn": ["sh"]}]}},
            {"systemd": {"content-security-policy": "default-src *"}},
            {"systemd": {"menu": {"logs": {"path": "../../x"}}}},
            {"systemd": {"menu": {"logs": {"order": "1"}}}},
            {"systemd": {"menu": {"logs": {"order": True}}}},
            {"systemd": {"menu": {"logs": {"order": 10001}}}},
            {"systemd": {"menu": {"logs": {"order": 1.0001}}}},
            {"systemd": {"menu": {"logs": {}}}},
            {"systemd": {"menu": {"logs": {"label": ""}}}},
            {"systemd": {"menu": {"logs": {"label": " Logs"}}}},
            {"systemd": {"menu": {"logs": {"label": "a\nb"}}}},
            {"systemd": {"menu": {"logs": {"label": "x" * 61}}}},
            {"systemd": {"menu": {"logs": {"label": "Logs" + chr(0x202E)}}}},
            {"systemd": {"menu": {"../logs": {"order": 1}}}},
            {"systemd": {"menu": ["logs"]}},
            {"systemd": {"menu": {"logs": {"order": 1}}}, "users": {"menu": {"index": None}}},
        ]
        for wanted in bad:
            with self.subTest(wanted):
                self.assertEqual(self.apply("system", wanted, base={"systemd": sha})[0], 64)
        for raw in (b"", b"[]", b"{not json", b"\xff", json.dumps({"base": {"../etc": ""}}).encode(),
                    json.dumps({"base": {"systemd": "abc"}}).encode(),
                    json.dumps({"base": {}, "set": {}, "more": 1}).encode(),
                    json.dumps({"base": {"systemd": sha}, "pad": "x" * hsmenu.MAX_PLAN}).encode()):
            with self.subTest(raw[:40]):
                self.assertEqual(self.apply("system", None, raw=raw)[0], 64)
        self.assertEqual(self.tree(), tree)
        self.assertEqual(self.read("system", "systemd"), {"menu": {"logs": {"order": 5}}})

    def test_a_file_changed_meanwhile_or_broken_is_left_alone(self):
        self.override("system", "systemd", {"menu": {"logs": {"order": 5}}})
        sha = self.show()["files"][0]["sha256"]
        self.override("system", "systemd", {"menu": {"logs": {"order": 6}}})
        code, _, err = self.apply("system", {"systemd": {"menu": {"logs": {"order": 1}}}}, base={"systemd": sha})
        self.assertEqual(code, 75)
        self.assertIn("changed meanwhile", err)
        self.assertEqual(self.apply("system", {"users": {"menu": {"index": None}}}, base={"users": sha})[0], 75,
                         "a file that does not exist is not the one the plan was made from")
        self.assertEqual(self.read("system", "systemd"), {"menu": {"logs": {"order": 6}}})
        self.override("system", "systemd", "{ broken")
        code, _, err = self.apply("system", {"systemd": {"menu": {"logs": {"order": 1}}}})
        self.assertEqual(code, 1)
        self.assertIn("not a valid JSON object", err)
        self.override("system", "systemd", {"menu": {"logs": {"docs": []}, "services": "odd"}})
        for wanted in ({"menu": {"logs": None}}, {"menu": {"services": {"order": 1}}}):
            with self.subTest(wanted):
                code, _, err = self.apply("system", {"systemd": wanted})
                self.assertEqual(code, 1)
                self.assertIn("holds more there", err)
        self.assertEqual(self.read("system", "systemd"), {"menu": {"logs": {"docs": []}, "services": "odd"}})
        self.assertFalse(os.path.exists(self.backup))

    def test_backups_and_the_lock(self):
        for i in range(7):
            self.assertEqual(self.apply("system", {"systemd": {"menu": {"logs": {"order": i + 1}}}}, now=NOW + i)[0], 0)
        self.assertEqual(sorted(os.listdir(self.backup)),
                         ["20261019T190002Z", "20261019T190003Z", "20261019T190004Z", "20261019T190005Z",
                          "20261019T190006Z"], "the first run had nothing to keep; of the six after it the newest five")
        held = os.open(os.path.dirname(self.path("system", "systemd")), os.O_RDONLY)
        try:
            hsmenu.fcntl.flock(held, hsmenu.fcntl.LOCK_EX)
            self.assertEqual(self.apply("system", {"systemd": {"menu": {"logs": None}}})[0], 75)
        finally:
            os.close(held)
        self.assertEqual(self.read("system", "systemd"), {"menu": {"logs": {"order": 7}}})

    def test_main_routes_menu(self):
        for argv in (["menu"], ["menu", "show", "x"], ["menu", "apply"], ["menu", "apply", "everyone"],
                     ["menu", "reset", "system"]):
            with self.subTest(argv):
                self.assertEqual(call(hs.main, argv)[0], 64)


if __name__ == "__main__":
    unittest.main(verbosity=1)
