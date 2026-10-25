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
        self.saved = {k: getattr(hs, k, None) for k in ("need_root", "systemctl", "unit_exists", "journal_tail", "STATE_DIR",
                                                         "PROFILE")}
        hs.PROFILE = os.path.join(self.dir, "etc", "profile.conf")
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
