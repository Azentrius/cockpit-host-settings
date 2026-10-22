#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""host-settings-notify.py, whole runs with stand-ins for the add-ons, the network, SMTP and the
clock: the first run sends the summary, later runs only changes; nothing sent means the same
changes come again; the topic's protection is read once a day without publishing; "test ntfy"
and "test mail" write what the page shows; the files get their modes; the token never lands in a
file. And hsstatus: the status the notifier writes passes Status Board's own rules."""

import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "lib")
sys.dont_write_bytecode = True
sys.path.insert(0, LIB)
sys.path.insert(0, HERE)
import hslib  # noqa: E402
import hsproc  # noqa: E402
import hsstatus  # noqa: E402
from notify_test import CLOSED, TOKEN, Http, Runner, Smtp, lstat_of, ok, status_doc  # noqa: E402

spec = importlib.util.spec_from_file_location("host_settings_notify", os.path.join(LIB, "host-settings-notify.py"))
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)

NOW = hslib.parse_time("2026-10-19T19:00:00Z")
CRON = "/usr/lib/cockpit-cronjobs/cronjobs-status"
GAPS = "/usr/lib/cockpit-update-gaps/gaps-status.py"


class Host:
    """A host with two add-ons, a settings file, a token and a state folder in a temp dir."""

    def __init__(self, conf="ntfy_url = https://ntfy.example.com\nntfy_topic = cockpit-h\nlanguage = en\n", token=TOKEN):
        self.dir = tempfile.mkdtemp()
        self.root = os.path.join(self.dir, "cockpit")
        for pkg, cmd in (("cronjobs", CRON), ("update-gaps", GAPS)):
            os.makedirs(os.path.join(self.root, pkg))
            with open(os.path.join(self.root, pkg, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"menu": {"index": {"label": pkg.title()}}, "azentrius": {"status": [cmd, "status"]}}, f)
        self.state = os.path.join(self.dir, "state")
        os.makedirs(self.state)
        self.conf = os.path.join(self.dir, "notify.conf")
        with open(self.conf, "w", encoding="utf-8") as f:
            f.write(conf)
        self.token = os.path.join(self.dir, "ntfy.token")
        with open(self.token, "w", encoding="utf-8") as f:
            f.write((token or "") + "\n")
        self.items = {"cronjobs": [{"id": "failed", "state": "ok", "title": "Failed runs"}],
                      "update-gaps": [{"id": "held", "state": "ok", "title": "Held packages"}]}
        self.http = Http(dict(CLOSED))
        self.smtp = Smtp()
        self.now = NOW

    def answer(self, pkg):
        return lambda argv: ok(status_doc(pkg, self.items[pkg], at=hslib.iso(self.now - 60)))

    def env(self, **kw):
        out = io.StringIO()
        e = notify.Env(conf=self.conf, token_file=self.token, state_dir=self.state, root=self.root, now=self.now,
                       http=self.http, smtp=self.smtp, host="h", fqdn="h.example.com",
                       runner=Runner({CRON: self.answer("cronjobs"), GAPS: self.answer("update-gaps")}),
                       lstat=lstat_of({CRON: (0o755, 0), GAPS: (0o755, 0)}), out=out)
        e.__dict__.update(kw)
        return e

    def run(self, *argv, **kw):
        e = self.env(**kw)
        code = notify.main(list(argv) or ["run"], e)
        return code, e.out.getvalue()

    def file(self, name):
        with open(os.path.join(self.state, name), encoding="utf-8") as f:
            return json.load(f)

    def posts(self, auth=True):
        return [c["body"] for c in self.http.calls if c["method"] == "POST" and
                ("Authorization" in c["headers"]) == auth]


class Runs(unittest.TestCase):
    def test_first_run_summary_then_changes_only(self):
        host = Host()
        host.items["update-gaps"][0]["state"] = "warning"
        code, out = host.run()
        self.assertEqual(code, 0, out)
        posts = host.posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["title"], "h: messages set up")
        self.assertIn("Warning · Update-Gaps · Held packages", posts[0]["message"])
        last = host.file("last.json")
        self.assertEqual(last["baseline_at"], "2026-10-19T19:00:00Z")
        self.assertEqual(last["delivery"]["results"], {"ntfy": {"ok": True, "code": 200, "reason": "sent"}})
        self.assertEqual(sorted(last["seen"]), ["cronjobs", "update-gaps"])
        # The daily check of the topic: read without a login, nothing published without one.
        self.assertEqual(last["access"]["anon_read"], "ok")
        self.assertEqual(host.posts(auth=False), [])

        host.now += 300
        host.run()
        self.assertEqual(len(host.posts()), 1, "nothing changed, nothing sent")
        host.now += 300
        host.items["cronjobs"][0]["state"] = "danger"
        host.run()
        self.assertEqual(len(host.posts()), 2)
        self.assertEqual(host.posts()[-1]["title"], "h: Cronjobs, Failed runs")
        self.assertEqual(host.posts()[-1]["priority"], 4)

    def test_nothing_sent_means_the_change_comes_again(self):
        host = Host()
        host.run()
        host.http.answers[("POST", "https://ntfy.example.com/", "auth")] = "network"
        host.items["cronjobs"][0]["state"] = "warning"
        host.now += 300
        host.run()
        last = host.file("last.json")
        self.assertEqual(last["delivery"]["results"]["ntfy"]["reason"], "network")
        self.assertNotIn("cronjobs#failed", last["sent"])
        status = host.file("status.json")
        self.assertEqual({i["id"]: i["state"] for i in status["items"]}["delivery"], "warning")
        host.http.answers[("POST", "https://ntfy.example.com/", "auth")] = (200, "{}")
        host.now += 300
        host.run()
        self.assertEqual(host.posts()[-1]["title"], "h: Cronjobs, Failed runs")
        self.assertEqual(host.file("last.json")["sent"]["cronjobs#failed"]["level"], "warning")

    def test_without_a_channel_nothing_is_sent_and_the_summary_waits(self):
        host = Host(conf="language = de\n")
        host.run()
        self.assertEqual(host.http.calls, [])
        self.assertNotIn("baseline_at", host.file("last.json"))
        status = host.file("status.json")
        self.assertEqual(status["state"], "none")
        self.assertEqual(status["i18n"]["de"]["summary"], "Noch nicht eingerichtet.")

    def test_both_channels_and_german(self):
        host = Host(conf="ntfy_url = https://ntfy.example.com\nntfy_topic = cockpit-h\nmail_to = ops@example.com\n"
                         "language = de\n")
        host.run()
        self.assertEqual(host.posts()[0]["title"], "h: Nachrichten eingerichtet")
        self.assertEqual(host.smtp.sent[0]["Subject"], "h: Nachrichten eingerichtet")
        self.assertEqual(host.file("last.json")["delivery"]["results"]["mail"]["reason"], "sent")

    def test_the_verbs_are_asked_in_the_language_of_the_messages(self):
        host = Host(conf="language = de\n")
        e = host.env()
        notify.main(["run"], e)
        self.assertEqual(sorted(c[-1] for c in e.runner.calls), ["de", "de"])
        self.assertEqual(sorted(c[0] for c in e.runner.calls), [CRON, GAPS])

    def test_files_modes_and_no_token(self):
        host = Host()
        host.run()
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(host.state, "status.json")).st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(host.state, "last.json")).st_mode), 0o600)
        for name in os.listdir(host.state):
            with open(os.path.join(host.state, name), encoding="utf-8") as f:
                self.assertNotIn(TOKEN, f.read(), name)

    def test_the_daily_check_and_a_new_target(self):
        host = Host()
        host.run()
        gets = lambda: [c for c in host.http.calls if c["url"].endswith("/json?poll=1&since=latest")]  # noqa: E731
        self.assertEqual(len(gets()), 1)
        host.now += 3600
        host.run()
        self.assertEqual(len(gets()), 1, "once a day")
        host.now += 86400
        host.run()
        self.assertEqual(len(gets()), 2)
        with open(host.conf, "w", encoding="utf-8") as f:
            f.write("ntfy_url = https://ntfy.example.com\nntfy_topic = other\n")
        host.http.answers[("GET", "https://ntfy.example.com/other/json", "anon")] = (200, "")
        host.http.answers[("POST", "https://ntfy.example.com/", "auth")] = (200, "{}")
        host.now += 300
        host.run()
        access = host.file("last.json")["access"]
        self.assertEqual((access["target"], access["anon_read"], access["state"]),
                         ("https://ntfy.example.com/other", "warning", "warning"))

    def test_test_ntfy_and_mail(self):
        host = Host(conf="ntfy_url = https://ntfy.example.com\nntfy_topic = cockpit-h\nmail_to = ops@example.com\n")
        host.run()
        code, out = host.run("test", "ntfy")
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["target"], "https://ntfy.example.com/cockpit-h")
        self.assertEqual(result["access"], {"state": "ok", "anon_read": "ok", "anon_write": "ok"})
        self.assertEqual(host.file("test-ntfy.json"), result)
        self.assertTrue(host.file("last.json")["access"]["full"])
        self.assertEqual({i["id"]: i["state"] for i in host.file("status.json")["items"]}["access"], "ok")
        self.assertEqual(len(host.posts(auth=False)), 1, "the write probe, without a login")
        code, out = host.run("test", "mail")
        self.assertEqual(json.loads(out)["result"]["reason"], "sent")
        self.assertEqual(host.smtp.sent[-1]["Subject"], "h: test message")
        code, out = Host(conf="language = en\n").run("test", "mail")
        self.assertEqual(json.loads(out)["error"], "not-set-up")

    def test_wrong_calls_and_the_lock(self):
        host = Host()
        for argv in (["test"], ["test", "sms"], ["run", "x"], ["status"]):
            self.assertEqual(notify.main(argv, host.env()), 64, argv)
        held = notify.lock(host.env())
        try:
            self.assertEqual(notify.main(["run"], host.env()), 75)
        finally:
            os.close(held)

    def test_an_addon_that_fails_is_a_warning_of_its_own(self):
        host = Host()
        host.run()
        e = host.env(runner=Runner({CRON: hsproc.Result(1, "", "boom"), GAPS: host.answer("update-gaps")}))
        host.now += 300
        e.now = host.now
        notify.main(["run"], e)
        self.assertEqual(host.posts()[-1]["title"], "h: Cronjobs, Status not readable")
        self.assertIn("the command failed (exit 1)", host.posts()[-1]["message"])
        self.assertIn("not readable: cronjobs", e.out.getvalue())


class OwnStatus(unittest.TestCase):
    def info(self, **kw):
        base = {"channels": ["ntfy"], "delivery": {"at": "2026-10-19T18:00:00Z", "results": {"ntfy": {"ok": True}}},
                "access": {"state": "ok", "anon_read": "ok", "anon_write": "ok"},
                "run": {"at": "2026-10-19T19:00:00Z", "addons": 4}}
        base.update(kw)
        return base

    def states(self, doc):
        return {i["id"]: i["state"] for i in doc["items"]}

    def test_passes_the_boards_rules(self):
        for info in (self.info(), self.info(channels=[]), self.info(access={"anon_read": "warning"}),
                     self.info(delivery={"at": "2026-10-19T18:00:00Z", "results": {"ntfy": {"ok": False, "reason": "rejected"},
                                                                                   "mail": {"ok": False, "reason": "http-502"}}})):
            for lang in ("en", "de"):
                doc = hsstatus.localize(hsstatus.status_file(info, "0.1.0"), lang, NOW)
                good, st = hslib.validate_status(json.dumps(doc), "host-settings")
                self.assertTrue(good, st)
                self.assertEqual(hsstatus.problems(doc), [])
                self.assertEqual(len(st["items"]), len(doc["items"]))
        for why in ("missing", "off", "unreadable"):
            doc = hsstatus.none_document("de", "0.1.0", why)
            self.assertTrue(hslib.validate_status(json.dumps(doc), "host-settings")[0])

    def test_items(self):
        doc = hsstatus.document(self.info(), "en", "0.1.0")
        self.assertEqual(self.states(doc), {"setup": "ok", "delivery": "ok", "access": "ok", "run": "ok"})
        self.assertEqual(doc["summary"], "Messages go to ntfy.")
        doc = hsstatus.document(self.info(channels=["ntfy", "mail"], access={"anon_write": "warning"}), "de", "0.1.0")
        self.assertEqual(self.states(doc)["access"], "warning")
        self.assertEqual(doc["summary"], "Braucht Aufmerksamkeit: Schutz des Themas.")
        self.assertIn("Nachrichten hineinschicken", [i for i in doc["items"] if i["id"] == "access"][0]["detail"])
        doc = hsstatus.document(self.info(channels=["mail"], delivery=None, access=None), "en", "0.1.0")
        self.assertNotIn("access", self.states(doc))
        self.assertEqual(self.states(doc)["delivery"], "none")
        bad = hsstatus.document(self.info(delivery={"at": "x", "results": {"ntfy": {"ok": False, "reason": "rejected"}}}),
                                "en", "0.1.0")
        self.assertEqual([i["detail"] for i in bad["items"] if i["id"] == "delivery"],
                         ["Could not be sent: ntfy (refused (token or rights))."])
        self.assertEqual(hsstatus.document(self.info(channels=[]), "en", "0.1.0")["summary"], "Not set up yet.")

    def test_an_old_run_is_a_warning(self):
        status = hsstatus.status_file(self.info(), "0.1.0")
        fresh = hsstatus.localize(status, "en", NOW + 60)
        self.assertEqual(fresh["state"], "ok")
        old = hsstatus.localize(status, "de", NOW + hsstatus.STALE_AFTER + 1)
        self.assertEqual(self.states(old)["run"], "warning")
        self.assertEqual(old["state"], "warning")
        self.assertEqual([i["detail"] for i in old["items"] if i["id"] == "run"], ["Der letzte Lauf ist älter als 15 Minuten."])
        self.assertNotIn("i18n", old)

    def test_switched_off(self):
        doc = hsstatus.off_file("0.1.0", "2026-10-19T19:00:00Z")
        de = hsstatus.localize(doc, "de", NOW + 10 ** 7)
        self.assertEqual((de["state"], de["summary"]), ("none", "Die regelmäßige Prüfung ist ausgeschaltet."))
        self.assertEqual(de["items"][0]["title"], "Ausgeschaltet")

    def test_version_from_the_page(self):
        with open(os.path.join(HERE, "..", "plugin", "settings.js"), encoding="utf-8") as f:
            self.assertIn(f'const VERSION = "{hsstatus.version()}";', f.read())


if __name__ == "__main__":
    unittest.main(verbosity=1)
