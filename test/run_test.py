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
        e = notify.Env(conf=self.conf, profile=os.path.join(self.dir, "profile.conf"), token_file=self.token,
                       state_dir=self.state, root=self.root, now=self.now,
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
        self.assertEqual({i["id"]: i["state"] for i in status["items"]}["ntfy"], "warning")
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
        gets = lambda: [c for c in host.http.calls if c["url"].endswith("/json?poll=1&since=1m")]  # noqa: E731
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

    def test_the_memory_of_0_1_0_keeps_working_when_an_addon_lists_findings(self):
        host = Host()
        with open(os.path.join(host.state, "last.json"), "w", encoding="utf-8") as f:
            json.dump({"format": 1, "baseline_at": "2026-10-18T09:00:00Z",
                       "sent": {"update-gaps#held": {"level": "warning", "at": NOW - 86400,
                                                     "last": {"ok>warning": NOW - 86400}, "label": "Update-Gaps",
                                                     "title": "Held packages"}}}, f)
        held = [{"fid": "held.postgresql-16", "state": "warning", "title": "postgresql-16 is held"}]

        def gaps(argv):
            doc = json.loads(status_doc("update-gaps", [{"id": "held", "state": "warning", "title": "Held packages",
                                                         "findings": {"complete": True, "list": held}}],
                                        at=hslib.iso(host.now - 60)))
            doc.update(features=["checks", "findings"],
                       checks=[{"id": "held", "state": "checked", "title": "Held packages", "item": "held"}])
            return ok(json.dumps(doc))
        runner = Runner({CRON: host.answer("cronjobs"), GAPS: gaps})
        code, out = host.run(runner=runner)
        self.assertEqual(code, 0, out)
        self.assertEqual(host.posts(), [], "no new summary, and the held package was told already")
        last = host.file("last.json")
        self.assertEqual((last["format"], last["baseline_at"]), (1, "2026-10-18T09:00:00Z"))
        self.assertEqual(last["sent"]["update-gaps#held/held.postgresql-16"]["status"], "open")
        held.append({"fid": "held.mariadb-server", "state": "warning", "title": "mariadb-server is held"})
        host.now += 300
        host.run(runner=runner)
        self.assertEqual([(p["title"], p["message"]) for p in host.posts()],
                         [("h: Update-Gaps, mariadb-server is held",
                           "Warning · Update-Gaps · mariadb-server is held\nHeld packages\n\nHost Settings on h")])

    def test_findings_that_break_the_rules_resolve_nothing_in_whole_runs(self):
        # The sequence of the review, through discover(), the status verb, hslib's rules,
        # changes(), the message, the channel and last.json: a broken list next to a check that
        # ran in part closes nothing; only a complete list does, and a send that fails is
        # repeated.
        host = Host()
        held = [{"fid": "held.postgresql-16", "state": "warning", "title": "postgresql-16 is held"}]
        now = {"list": held, "check": "checked", "state": "warning"}

        def gaps(argv):
            doc = json.loads(status_doc("update-gaps", [{"id": "held", "state": now["state"], "title": "Held packages",
                                                         "findings": {"complete": True, "total": len(now["list"]) if
                                                                      isinstance(now["list"], list) else 1,
                                                                      "list": now["list"]}}],
                                        at=hslib.iso(host.now - 60)))
            doc.update(features=["checks", "findings"],
                       checks=[{"id": "held", "state": now["check"], "title": "Held packages", "item": "held",
                                "checked_at": hslib.iso(host.now - 60), "stale_after": 3600}])
            return ok(json.dumps(doc))
        runner = Runner({CRON: host.answer("cronjobs"), GAPS: gaps})
        key = "update-gaps#held/held.postgresql-16"
        code, out = host.run(runner=runner)
        self.assertEqual(code, 0, out)
        self.assertIn("Warning · Update-Gaps · postgresql-16 is held", host.posts()[0]["message"])
        self.assertEqual(host.file("last.json")["sent"][key]["status"], "open")
        # the item OK, its check partial, the list a text: no message, the finding stays open
        now.update(list="unreadable", check="partial", state="ok")
        host.now += 300
        self.assertEqual(host.run(runner=runner)[0], 0)
        self.assertEqual(len(host.posts()), 1)
        self.assertEqual(host.file("last.json")["sent"][key]["status"], "open")
        # readable and unchanged again: nothing, not a second new
        now.update(list=held, check="checked", state="warning")
        host.now += 300
        host.run(runner=runner)
        self.assertEqual(len(host.posts()), 1)
        # a complete list without it, but nothing goes out: it stays open, the change comes again
        now.update(list=[], state="ok")
        host.http.answers[("POST", "https://ntfy.example.com/", "auth")] = "network"
        host.now += 300
        host.run(runner=runner)
        self.assertEqual(host.file("last.json")["sent"][key]["status"], "open")
        host.http.answers[("POST", "https://ntfy.example.com/", "auth")] = (200, "{}")
        host.now += 300
        host.run(runner=runner)
        self.assertEqual(host.posts()[-1]["message"], "OK again · Update-Gaps · postgresql-16 is held\n"
                                                      "Held packages: no longer found by a complete check\n\nHost Settings on h")
        self.assertEqual(host.file("last.json")["sent"][key]["status"], "resolved")
        self.assertEqual(len(host.posts()), 3, "the summary, the failed try, the message")

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


class WithProfile(unittest.TestCase):
    """Whole runs with a target profile: Host Settings, Update Gaps, Security Monitor and Cron Jobs
    are expected on every host; this host has Cron Jobs and Update Gaps, no Security Monitor."""

    def host(self, profile="roles =\n", **kw):
        host = Host(**kw)
        host.profile = os.path.join(host.dir, "profile.conf")
        if profile is not None:
            with open(host.profile, "w", encoding="utf-8") as f:
                f.write(profile)
        return host

    def run_with(self, host, **kw):
        return host.run(profile=host.profile, **kw)

    def test_the_first_run_names_the_gaps_in_the_summary_once(self):
        host = self.host()
        code, out = self.run_with(host)
        self.assertEqual(code, 0, out)
        posts = host.posts()
        self.assertEqual([p["title"] for p in posts], ["h: messages set up"])
        self.assertIn("Check no longer possible · Security Monitor\nthe add-on is not installed", posts[0]["message"])
        last = host.file("last.json")
        self.assertEqual(last["sent"]["security@"]["level"], "lost")
        self.assertEqual([(r["id"], r["coverage"]) for r in last["coverage"]],
                         [("host-settings", "checked"), ("update-gaps", "checked"), ("security", "missing"), ("cronjobs", "checked")])
        host.now += 300
        self.run_with(host)
        self.assertEqual(len(host.posts()), 1, "nothing repeats")

    def test_the_first_summary_names_an_unreadable_addon_once(self):
        host = self.host()
        e = host.env(profile=host.profile, runner=Runner({CRON: hsproc.Result(1, "", "boom"), GAPS: host.answer("update-gaps")}))
        notify.main(["run"], e)
        body = host.posts()[0]["message"]
        self.assertIn("Warning · Cronjobs · Status not readable", body)
        self.assertNotIn("Check no longer possible · Cronjobs", body)
        self.assertIn("Check no longer possible · Security Monitor", body)
        self.assertEqual(host.file("last.json")["sent"]["cronjobs@"]["level"], "lost", "remembered all the same")

    def test_a_profile_set_up_later_states_the_gaps_once_then_changes(self):
        host = self.host(profile=None)
        self.run_with(host)
        self.assertEqual(len(host.posts()), 1)
        self.assertIsNone(host.file("last.json")["coverage"])
        with open(host.profile, "w", encoding="utf-8") as f:
            f.write("roles = web\n")
        host.now += 300
        self.run_with(host)
        self.assertEqual([p["title"] for p in host.posts()][1:], ["h: 2 changes"])
        self.assertEqual(host.posts()[-1]["message"].split("\n\n")[:2], [
            "Check no longer possible · Image Update Monitor\nthe add-on is not installed",
            "Check no longer possible · Security Monitor\nthe add-on is not installed"])
        host.now += 300
        self.run_with(host)
        self.assertEqual(len(host.posts()), 2, "a run without a change says nothing")
        # Cron Jobs cannot be read any more, then again
        e = host.env(profile=host.profile, runner=Runner({CRON: hsproc.Result(77), GAPS: host.answer("update-gaps")}))
        host.now += 300
        e.now = host.now
        notify.main(["run"], e)
        self.assertEqual((host.posts()[-1]["title"], host.posts()[-1]["message"].split("\n\n")[0]),
                         ("h: Cronjobs, check no longer possible", "Check no longer possible · Cronjobs\nnothing is readable without root"))
        host.now += 3600
        self.run_with(host)
        self.assertEqual(host.posts()[-1]["title"], "h: Cronjobs, check possible again")
        self.assertEqual(len(host.posts()), 4)

    def test_nothing_sent_means_the_gaps_come_again(self):
        host = self.host(profile=None)
        self.run_with(host)
        with open(host.profile, "w", encoding="utf-8") as f:
            f.write("roles = build\n")
        host.http.answers[("POST", "https://ntfy.example.com/", "auth")] = "network"
        host.now += 300
        self.run_with(host)
        self.assertNotIn("security@", host.file("last.json")["sent"])
        host.http.answers[("POST", "https://ntfy.example.com/", "auth")] = (200, "{}")
        host.now += 300
        self.run_with(host)
        self.assertEqual(host.posts()[-1]["title"], "h: Security Monitor, check no longer possible")

    def test_an_old_last_json_and_a_broken_profile(self):
        host = self.host(profile="roles = mail\n")
        with open(os.path.join(host.state, "last.json"), "w", encoding="utf-8") as f:
            json.dump({"format": 1, "baseline_at": "2026-10-18T09:00:00Z",
                       "sent": {"cronjobs#failed": {"level": "warning", "at": NOW - 86400, "last": {}, "label": "Cronjobs",
                                                    "title": "Failed runs"}}}, f)
        host.items["cronjobs"][0]["state"] = "warning"
        self.run_with(host)
        self.assertEqual(host.posts(), [], "a profile that does not count changes nothing: no message, as before")
        last = host.file("last.json")
        self.assertEqual((last["format"], last["coverage"], sorted(last["sent"])), (1, None, ["cronjobs#failed"]))
        with open(host.profile, "w", encoding="utf-8") as f:
            f.write("roles = bastion\n")
        host.now += 300
        self.run_with(host)
        self.assertEqual([p["title"] for p in host.posts()], ["h: Security Monitor, check no longer possible"])
        sent = host.file("last.json")["sent"]
        self.assertEqual({k: e["level"] for k, e in sent.items()},
                         {"cronjobs#failed": "warning", "cronjobs@": "ok", "update-gaps@": "ok", "security@": "lost"})


class OwnStatus(unittest.TestCase):
    OK = {"state": "ok", "at": "2026-10-19T18:00:00Z", "via": "run", "recovered": None}

    def info(self, **kw):
        base = {"channels": ["ntfy"], "channel_health": {"ntfy": self.OK},
                "access": {"state": "ok", "anon_read": "ok", "anon_write": "ok"},
                "run": {"at": "2026-10-19T19:00:00Z", "addons": 4}}
        base.update(kw)
        return base

    def states(self, doc):
        return {i["id"]: i["state"] for i in doc["items"]}

    def test_passes_the_boards_rules(self):
        failed = {"state": "failed", "since": "2026-10-19T17:00:00Z", "at": "2026-10-19T18:00:00Z", "via": "run"}
        for info in (self.info(), self.info(channels=[]), self.info(access={"anon_read": "warning"}),
                     self.info(channels=["ntfy", "mail"], channel_health={"ntfy": dict(failed, reason="rejected"),
                                                                          "mail": dict(failed, reason="http-502")}),
                     self.info(channels=["ntfy", "mail"], channel_health={"ntfy": {"state": "unknown"}}),
                     self.info(channel_health={"ntfy": dict(self.OK, via="test", recovered={"since": "2026-10-19T17:00:00Z",
                                                                                           "reason": "no-mta"})})):
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
        self.assertEqual(self.states(doc), {"setup": "ok", "ntfy": "ok", "access": "ok", "run": "ok"})
        self.assertEqual(doc["summary"], "Messages go to ntfy.")
        self.assertEqual([i["detail"] for i in doc["items"] if i["id"] == "ntfy"],
                         ["Accepted the last message on 19 Oct at 18:00 UTC."])
        doc = hsstatus.document(self.info(channels=["ntfy", "mail"], access={"anon_write": "warning"}), "de", "0.1.0")
        self.assertEqual(self.states(doc)["access"], "warning")
        self.assertEqual(doc["summary"], "Braucht Aufmerksamkeit: Schutz des Themas.")
        self.assertIn("Nachrichten hineinschicken", [i for i in doc["items"] if i["id"] == "access"][0]["detail"])
        # A channel without a record is not confirmed: none, never ok.
        self.assertEqual((self.states(doc)["mail"], [i["detail"] for i in doc["items"] if i["id"] == "mail"]),
                         ("none", ["Nicht bestätigt: Mit den jetzigen Einstellungen hat der Kanal noch keine Nachricht und "
                                   "keinen Test angenommen."]))
        doc = hsstatus.document(self.info(channels=["mail"], channel_health={}, access=None), "en", "0.1.0")
        self.assertNotIn("access", self.states(doc))
        self.assertEqual(self.states(doc)["mail"], "none")
        bad = hsstatus.document(self.info(channel_health={"ntfy": {"state": "failed", "since": "2026-10-19T17:00:00Z",
                                                                   "at": "2026-10-19T18:00:00Z", "via": "run",
                                                                   "reason": "rejected"}}), "en", "0.1.0")
        self.assertEqual(bad["summary"], "Needs attention: ntfy.")
        self.assertEqual([i["detail"] for i in bad["items"] if i["id"] == "ntfy"],
                         ["Failing since 19 Oct at 17:00 UTC: refused (token or rights). Runs with nothing to send do "
                          "not end this; the next message it accepts, or a test in Host Settings, does."])
        again = hsstatus.document(self.info(channel_health={"ntfy": dict(self.OK, via="test", recovered={
            "since": "2026-10-19T17:00:00Z", "reason": "rejected"})}), "de", "0.1.0")
        self.assertEqual([i["detail"] for i in again["items"] if i["id"] == "ntfy"],
                         ["Hat die Testnachricht am 19.10. um 18:00 UTC angenommen, nach Fehlern seit 19.10. um 17:00 UTC."])
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
