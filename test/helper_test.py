#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""host-settings.py, the fixed verbs of the page and of the Status Board, with stand-ins for
systemctl and ss: every argument is checked before anything is written; the settings file keeps
its format; the token comes from standard input only, lands with mode 0600 and is never printed;
the status verb answers without root, also without a status.json; switching the timer off says
so in the status; "install timer" needs a channel."""

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
import hslib  # noqa: E402
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
        self.saved = {k: getattr(hs, k) for k in ("need_root", "systemctl", "unit_exists", "journal_tail", "STATE_DIR")}
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
                         {"setup": "ok", "delivery": "none", "access": "none", "run": "ok"})
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
        self.assertIn("Set up ntfy or e-mail first.", err)
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
        self.assertEqual(call(hs.do_show, ["x"], self.conf)[0], 64)

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


if __name__ == "__main__":
    unittest.main(verbosity=1)
