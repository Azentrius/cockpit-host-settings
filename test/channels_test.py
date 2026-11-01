#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The health of each channel (lib/hschannels.py and its place in host-settings-notify.py), as
whole runs and tests of the page with stand-ins for the add-ons, ntfy, SMTP, the watchdog and the
clock. The sequences:

  1. two channels, one fails: the failure stays in the status and in the watchdog's signal;
  2. the next runs, unchanged: no second message to the channel that took it, no recovery that
     nothing proved (also once the mail server is back);
  3. a test of that channel, or a later message it accepts, heals that channel only, with exactly
     one recovery;
  4. a result belongs to its target: a test made with settings changed since heals nothing, a new
     target is no repair and starts unknown;
  5. a channel taken out of the settings is no longer counted: a change, not a repair; set up
     again, it is unknown;
  6. restarts, the update from 0.2.0 (its last.json) and going back to 0.2.0 and forth: known
     failures stay, what was not kept is not made up;
  7. both channels fail: the changes come again, nothing is taken as told;
  8. the watchdog not reached: findings and channels as they were, its own trouble shown.

And the lock: a test while a run holds it changes nothing; the test writes last.json atomically,
0600, never with the token."""

import io
import json
import os
import stat
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "lib")
sys.dont_write_bytecode = True
sys.path.insert(0, LIB)
sys.path.insert(0, HERE)
import hschannels  # noqa: E402
import hsheartbeat as hb  # noqa: E402
import hslib  # noqa: E402
import hsstatus  # noqa: E402
from heartbeat_test import HC, Watchdog  # noqa: E402
from notify_test import TOKEN  # noqa: E402
from run_test import NOW, Host, notify  # noqa: E402

BOTH = "ntfy_url = https://ntfy.example.com\nntfy_topic = cockpit-h\nmail_to = ops@example.com\nlanguage = en\n"
NTFY = "https://ntfy.example.com/cockpit-h"
MAIL = "ops@example.com"
POST = ("POST", "https://ntfy.example.com/", "auth")


def at(minutes):
    return hslib.iso(NOW + 60 * minutes)


class Box:
    """A host with ntfy and e-mail, a watchdog with Healthchecks' protocol, and the journal of
    every run and test."""

    def __init__(self, conf=BOTH):
        self.host = Host(conf=conf)
        self.beat = os.path.join(self.host.dir, "heartbeat.conf")
        with open(self.beat, "w", encoding="utf-8") as f:
            f.write(hb.format_conf("healthchecks", HC))
        self.dog = Watchdog(self.host, HC)
        self.journal = []

    def go(self, *argv, minutes=0, **kw):
        """One run (no argv) or test, `minutes` after the last one; (exit code, standard output)."""
        self.host.now += 60 * minutes
        e = self.host.env(heartbeat_file=self.beat, http=self.dog, sleep=lambda s: None, err=io.StringIO(), **kw)
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code = notify.main(list(argv) or ["run"], e)
        finally:
            sys.stderr = saved
        self.journal.append(e.out.getvalue() if not argv else e.err.getvalue())
        return code, e.out.getvalue()

    def conf(self, text):
        with open(self.host.conf, "w", encoding="utf-8") as f:
            f.write(text)

    def last(self):
        return self.host.file("last.json")

    def channels(self):
        return self.last()["channels"]

    def items(self):
        return {i["id"]: i["state"] for i in self.host.file("status.json")["items"]}

    def detail(self, iid, lang="en"):
        status = self.host.file("status.json")
        if lang == "de":
            return status["i18n"]["de"]["items"][iid]["detail"]
        return [i["detail"] for i in status["items"] if i["id"] == iid][0]

    def signal(self):
        """(success or failure, body) of the last signal."""
        c = self.dog.calls[-1]
        return ("failure" if c["url"].endswith("/fail") else "success"), c["body"]

    def recoveries(self):
        """How often the watchdog saw a success after a failure."""
        kinds = ["failure" if c["url"].endswith("/fail") else "success" for c in self.dog.calls]
        return sum(1 for a, b in zip(kinds, kinds[1:]) if a == "failure" and b == "success")

    def said(self, text):
        return "".join(self.journal).count(text)


def mail_failing():
    """The first run: ntfy takes the summary, the mail server is not there."""
    box = Box()
    box.host.smtp.fail = ConnectionRefusedError()
    box.go()
    return box


class OneOfTwo(unittest.TestCase):
    def test_1_and_2_the_failure_stays_through_runs_with_nothing_to_send(self):
        box = mail_failing()
        self.assertEqual(len(box.host.posts()), 1)
        self.assertIn("baseline_at", box.last(), "ntfy took it: told")
        self.assertEqual(box.signal(), ("failure", b"Host Settings: a message could not be sent (mail no-mta)"))
        self.assertEqual(box.channels(), {
            "ntfy": {"ok": {"at": at(0), "via": "run", "target": NTFY}},
            "mail": {"failed": {"since": at(0), "at": at(0), "via": "run", "reason": "no-mta", "target": MAIL}}})
        self.assertEqual(box.items(), {"setup": "ok", "ntfy": "ok", "mail": "warning", "access": "none", "heartbeat": "ok",
                                       "run": "ok"})
        self.assertEqual(box.host.file("status.json")["summary"], "Needs attention: E-mail.")
        self.assertEqual(box.detail("mail"), "Failing since 19 Oct at 19:00 UTC: no mail server on localhost:25. Runs with "
                                             "nothing to send do not end this; the next message it accepts, or a test in "
                                             "Host Settings, does.")
        self.assertEqual(box.detail("heartbeat"), "The watchdog was told on 19 Oct at 19:00 UTC that a run failed.")
        self.assertEqual(box.said("channel mail: failed (run, no-mta)"), 1)
        sent = box.last()["sent"]
        for fixed in (False, True):
            # Nothing changed, also once the mail server is back: nothing goes out, nothing heals.
            box.host.smtp.fail = None if fixed else box.host.smtp.fail
            box.go(minutes=5)
            self.assertEqual(len(box.host.posts()), 1, "no second message to the channel that took it")
            self.assertEqual(box.host.smtp.sent, [])
            self.assertEqual(box.last()["sent"], sent)
            self.assertEqual(box.signal(), ("failure", b"Host Settings: a channel has not worked since it failed (mail no-mta)"))
            self.assertEqual(box.channels()["mail"], {"failed": {"since": at(0), "at": at(0), "via": "run",
                                                                 "reason": "no-mta", "target": MAIL}})
            self.assertEqual(box.items()["mail"], "warning")
            self.assertEqual(box.items()["ntfy"], "ok")
            self.assertEqual(box.detail("heartbeat"), f"The watchdog was told on {hsstatus.when('en', box.host.now)} "
                                                      "that a channel is failing.")
            self.assertIn(", failing: mail no-mta", box.journal[-1])
        self.assertEqual(box.recoveries(), 0)
        self.assertEqual(box.said("accepted again"), 0)
        self.assertEqual(box.said("channel mail: failed"), 1, "a failure begins once")


class Recovery(unittest.TestCase):
    def test_3_a_test_heals_its_channel_only_and_once(self):
        box = mail_failing()
        ntfy = box.channels()["ntfy"]
        box.host.smtp.fail = None
        signals = len(box.dog.calls)
        code, out = box.go("test", "mail", minutes=3)
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual((result["result"]["reason"], result["target"], result["health"], result["recovered"]),
                         ("sent", MAIL, "ok", True))
        self.assertEqual(box.host.file("test-mail.json"), result)
        self.assertEqual(box.channels()["mail"], {"ok": {"at": at(3), "via": "test", "target": MAIL},
                                                  "recovered": {"at": at(3), "via": "test", "since": at(0), "reason": "no-mta"}})
        self.assertEqual(box.channels()["ntfy"], ntfy, "the other channel as it was")
        self.assertEqual(box.items()["mail"], "ok", "the test writes the status")
        self.assertEqual(box.detail("mail"), "Accepted the test message on 19 Oct at 19:03 UTC, after failing since 19 Oct "
                                             "at 19:00 UTC.")
        self.assertEqual(box.detail("mail", "de"), "Hat die Testnachricht am 19.10. um 19:03 UTC angenommen, nach Fehlern "
                                                   "seit 19.10. um 19:00 UTC.")
        self.assertEqual(len(box.dog.calls), signals, "a test sends no signal")
        self.assertEqual(len(box.host.posts()), 1)
        self.assertEqual(box.said("channel mail: accepted again (test, failing since 2026-10-19T19:00:00Z)"), 1)
        # The next runs: the watchdog sees one recovery, then successes; a second test is no
        # second recovery.
        box.go(minutes=2)
        self.assertEqual(box.signal(), ("success", b"Host Settings: run done"))
        box.go(minutes=5)
        self.assertEqual(json.loads(box.go("test", "mail", minutes=1)[1]).get("recovered"), None)
        box.go(minutes=4)
        self.assertEqual(box.recoveries(), 1)
        self.assertEqual(box.said("accepted again"), 1)
        self.assertEqual(box.channels()["mail"]["recovered"]["at"], at(3))

    def test_3_a_later_message_it_accepts_heals_it(self):
        box = mail_failing()
        box.host.smtp.fail = None
        box.go(minutes=5)
        self.assertEqual(box.signal()[0], "failure", "nothing to send: nothing proved")
        box.host.items["cronjobs"][0]["state"] = "warning"
        box.go(minutes=5)
        self.assertEqual((len(box.host.posts()), len(box.host.smtp.sent)), (2, 1))
        self.assertEqual(box.channels()["mail"]["recovered"], {"at": at(10), "via": "run", "since": at(0), "reason": "no-mta"})
        self.assertEqual(box.signal()[0], "success")
        self.assertEqual(box.detail("mail"), "Accepted a message again on 19 Oct at 19:10 UTC, after failing since 19 Oct at "
                                             "19:00 UTC.")
        box.go(minutes=5)
        self.assertEqual(box.detail("mail"), "Accepted a message again on 19 Oct at 19:10 UTC, after failing since 19 Oct at "
                                             "19:00 UTC.", "the status keeps naming the one recovery")
        self.assertEqual((box.recoveries(), box.said("accepted again")), (1, 1))

    def test_3_check_connection_heals_ntfy_and_can_fail_it(self):
        box = Box()
        box.host.http.answers[POST] = "network"
        box.go()
        self.assertEqual(box.channels()["ntfy"]["failed"]["reason"], "network")
        self.assertEqual(box.items()["mail"], "ok")
        mail = box.channels()["mail"]
        box.host.http.answers[POST] = (200, "{}")
        result = json.loads(box.go("test", "ntfy", minutes=1)[1])
        self.assertEqual((result["health"], result["recovered"]), ("ok", True))
        self.assertEqual(box.channels()["ntfy"]["recovered"]["via"], "test")
        self.assertEqual(box.channels()["mail"], mail)
        box.go(minutes=4)
        self.assertEqual(box.signal()[0], "success")
        # Check connection when the server cannot be reached: the channel fails, by a test.
        box.host.http.answers[("GET", "https://ntfy.example.com/v1/health")] = "timeout"
        result = json.loads(box.go("test", "ntfy", minutes=1)[1])
        self.assertEqual(result["health"], "failed")
        self.assertEqual(box.channels()["ntfy"]["failed"], {"since": at(6), "at": at(6), "via": "test", "reason": "timeout",
                                                            "target": NTFY})
        self.assertEqual(box.items()["ntfy"], "warning")
        box.go(minutes=4)
        self.assertEqual(box.signal(), ("failure", b"Host Settings: a channel has not worked since it failed (ntfy timeout)"))


class Targets(unittest.TestCase):
    def test_4_a_result_counts_only_for_its_target(self):
        box = mail_failing()
        box.host.smtp.fail = None
        # A new address is no repair: still failed, nothing sent.
        box.conf(BOTH.replace(MAIL, "other@example.com"))
        box.go(minutes=5)
        self.assertEqual(box.signal()[0], "failure")
        self.assertEqual(box.items()["mail"], "warning")
        # A test made with the old address that ends after it changed again proves nothing.
        box.conf(BOTH)

        class Changing:
            sent = []

            def __call__(inner):
                return inner

            def __enter__(inner):
                return inner

            def __exit__(inner, *exc):
                return False

            def send_message(inner, m):
                inner.sent.append(m)
                box.conf(BOTH.replace(MAIL, "other@example.com"))
        result = json.loads(box.go("test", "mail", minutes=1, smtp=Changing())[1])
        self.assertEqual((result["target"], result["result"]["reason"], result["health"]), (MAIL, "sent", "failed"))
        self.assertNotIn("recovered", result)
        self.assertEqual(box.channels()["mail"]["failed"]["since"], at(0))
        self.assertNotIn("ok", box.channels()["mail"])
        self.assertEqual(box.said("channel mail: the result belongs to settings changed since, not counted"), 1)
        # A test with the address set now heals it.
        result = json.loads(box.go("test", "mail", minutes=1)[1])
        self.assertEqual((result["target"], result["health"], result["recovered"]), ("other@example.com", "ok", True))
        box.go(minutes=3)
        self.assertEqual((box.signal()[0], box.recoveries()), ("success", 1))

    def test_4_a_new_target_starts_unknown(self):
        box = Box()
        box.go()
        self.assertEqual((box.items()["ntfy"], box.items()["mail"]), ("ok", "ok"))
        box.conf(BOTH.replace("cockpit-h", "cockpit-new").replace(MAIL, "other@example.com"))
        box.host.http.answers[("GET", "https://ntfy.example.com/cockpit-new/json", "anon")] = (403, "{}")
        box.go(minutes=5)
        self.assertEqual((box.items()["ntfy"], box.items()["mail"]), ("none", "none"), "never shown as tested")
        self.assertEqual(box.detail("mail"), "Not confirmed: it has not accepted a message or a test with the settings set now.")
        self.assertEqual(box.signal()[0], "success", "unknown is no failure")
        self.assertEqual(box.channels()["mail"]["ok"]["target"], MAIL, "the old address stays the old one")
        view = hschannels.health(hschannels.records_of(box.channels()), hslib.parse_config(BOTH)[0])
        self.assertEqual(view["mail"]["state"], "ok", "with the old settings it was confirmed")


class Removed(unittest.TestCase):
    def test_5_taken_out_is_a_change_not_a_repair(self):
        box = mail_failing()
        box.conf("ntfy_url = https://ntfy.example.com\nntfy_topic = cockpit-h\n")
        box.go(minutes=5)
        self.assertEqual(box.channels()["mail"], {"removed": {"at": at(5), "failed_since": at(0)}})
        self.assertNotIn("mail", box.items())
        self.assertEqual(box.signal()[0], "success", "no longer counted")
        self.assertEqual(box.said("channel mail: removed from the settings while failing since 2026-10-19T19:00:00Z: a "
                                  "change of the settings, not a repair"), 1)
        self.assertEqual(box.said("accepted again"), 0)
        self.assertNotIn("recovered", box.channels()["mail"])
        box.go(minutes=5)
        self.assertEqual(box.said("removed from the settings"), 1, "said once")
        # Set up again: unknown, never ok, and the old delivery is not taken in again.
        box.conf(BOTH)
        box.go(minutes=5)
        self.assertEqual(box.channels()["mail"], {"reset_at": at(15)})
        self.assertEqual(box.items()["mail"], "none")
        self.assertEqual(box.signal()[0], "success")
        self.assertEqual(box.said("channel mail: set up again, not confirmed yet"), 1)


class Upgrade(unittest.TestCase):
    OLD = {"format": 1, "baseline_at": "2026-10-19T09:00:00Z", "sent": {},
           "delivery": {"at": "2026-10-19T18:50:00Z", "summary": False, "changes": 1, "title": "h: Cronjobs, Failed runs",
                        "results": {"ntfy": {"ok": True, "code": 200, "reason": "sent"},
                                    "mail": {"ok": False, "code": None, "reason": "no-mta"}}},
           "run": {"at": "2026-10-19T18:55:00Z", "took_ms": 300, "addons": 2, "bad_lines": []}}

    def test_6_the_last_json_of_0_2_0(self):
        box = Box()
        with open(os.path.join(box.host.state, "last.json"), "w", encoding="utf-8") as f:
            json.dump(self.OLD, f)
        box.go()
        self.assertEqual(box.host.posts(), [], "no new summary, nothing again")
        self.assertEqual(box.last()["format"], 1)
        self.assertEqual(box.channels(), {
            "ntfy": {"ok": {"at": "2026-10-19T18:50:00Z", "via": "run", "target": None}},
            "mail": {"failed": {"since": "2026-10-19T18:50:00Z", "at": "2026-10-19T18:50:00Z", "via": "run",
                                "reason": "no-mta", "target": None}}})
        self.assertEqual((box.items()["mail"], box.items()["ntfy"]), ("warning", "none"),
                         "the failure stays; a success without its target is not made a confirmation")
        self.assertEqual(box.signal(), ("failure", b"Host Settings: a channel has not worked since it failed (mail no-mta)"))
        self.assertEqual(box.said("taken in"), 2)
        # Restarts: every run is a new process; nothing is taken in twice, the failure keeps its
        # beginning.
        box.go(minutes=5)
        box.go(minutes=5)
        self.assertEqual(box.said("taken in"), 2)
        self.assertEqual(box.channels()["mail"]["failed"]["since"], "2026-10-19T18:50:00Z")
        # A test ends it, a check confirms ntfy for its target.
        self.assertTrue(json.loads(box.go("test", "mail", minutes=1)[1])["recovered"])
        self.assertEqual(json.loads(box.go("test", "ntfy", minutes=1)[1])["health"], "ok")
        box.go(minutes=3)
        self.assertEqual((box.items()["mail"], box.items()["ntfy"], box.signal()[0]), ("ok", "ok", "success"))

    def test_6_going_back_to_0_2_0_and_forth(self):
        # 0.2.0 reads last.json on (format 1), keeps "channels" as it is and writes its own
        # delivery without targets. Coming back, a delivery newer than the records is taken in.
        box = mail_failing()
        last = box.last()
        self.assertEqual(last["format"], hsnotify_format())
        self.assertEqual(sorted(last["delivery"]), ["at", "changes", "results", "summary", "title"],
                         "the delivery as 0.2.0 writes and reads it")
        # 0.2.0 sent a message both channels took: no proof for e-mail's target, ntfy confirmed
        # by this version stays so.
        last["delivery"] = dict(last["delivery"], at=at(20), results={"ntfy": {"ok": True, "code": 200, "reason": "sent"},
                                                                     "mail": {"ok": True, "code": None, "reason": "sent"}})
        with open(os.path.join(box.host.state, "last.json"), "w", encoding="utf-8") as f:
            json.dump(last, f)
        box.go(minutes=25)
        self.assertEqual(box.channels()["mail"]["failed"]["since"], at(0))
        self.assertEqual(box.channels()["ntfy"]["ok"], {"at": at(0), "via": "run", "target": NTFY})
        self.assertEqual((box.items()["mail"], box.items()["ntfy"]), ("warning", "ok"))
        # 0.2.0 sent one that ntfy did not take: a failure, without its target.
        last = box.last()
        last["delivery"] = dict(last["delivery"], at=at(30), results={"ntfy": {"ok": False, "code": 403, "reason": "rejected"},
                                                                     "mail": {"ok": False, "code": None, "reason": "no-mta"}})
        with open(os.path.join(box.host.state, "last.json"), "w", encoding="utf-8") as f:
            json.dump(last, f)
        box.go(minutes=10)
        self.assertEqual(box.channels()["ntfy"]["failed"], {"since": at(30), "at": at(30), "via": "run", "reason": "rejected",
                                                            "target": None})
        self.assertEqual(box.channels()["mail"]["failed"]["since"], at(0), "a failure that goes on keeps its beginning")
        self.assertEqual(box.signal(), ("failure", b"Host Settings: a channel has not worked since it failed "
                                                   b"(mail no-mta, ntfy rejected)"))

    def test_6_another_format_starts_anew(self):
        box = Box()
        with open(os.path.join(box.host.state, "last.json"), "w", encoding="utf-8") as f:
            json.dump(dict(self.OLD, format=2), f)
        box.go()
        self.assertEqual(box.host.posts()[0]["title"], "h: messages set up")
        self.assertEqual(sorted(box.channels()), ["mail", "ntfy"])
        self.assertEqual(box.signal()[0], "success")


def hsnotify_format():
    import hsnotify
    return hsnotify.FORMAT


class BothFail(unittest.TestCase):
    def test_7_the_summary_comes_again_and_nothing_is_taken_as_told(self):
        box = Box()
        box.host.http.answers[POST] = "network"
        box.host.smtp.fail = ConnectionRefusedError()
        box.go()
        self.assertNotIn("baseline_at", box.last())
        self.assertEqual(box.last()["sent"], {})
        self.assertEqual(box.signal(), ("failure", b"Host Settings: a message could not be sent (mail no-mta, ntfy network)"))
        box.go(minutes=5)
        self.assertEqual(len(box.host.posts()), 2, "sent again")
        box.host.http.answers[POST] = (200, "{}")
        box.host.smtp.fail = None
        box.go(minutes=5)
        self.assertEqual(box.host.posts()[-1]["title"], "h: messages set up")
        self.assertEqual(box.host.smtp.sent[-1]["Subject"], "h: messages set up")
        self.assertIn("baseline_at", box.last())
        self.assertEqual({c: sorted(r) for c, r in box.channels().items()}, {"ntfy": ["ok", "recovered"], "mail": ["ok", "recovered"]})
        self.assertEqual((box.signal()[0], box.recoveries()), ("success", 1))

    def test_7_a_change_both_missed_is_told_later(self):
        box = Box()
        box.go()
        box.host.http.answers[POST] = "timeout"
        box.host.smtp.fail = ConnectionRefusedError()
        box.host.items["cronjobs"][0]["state"] = "danger"
        box.go(minutes=5)
        self.assertNotIn("cronjobs#failed", box.last()["sent"], "not taken as told")
        self.assertEqual((box.items()["ntfy"], box.items()["mail"]), ("warning", "warning"))
        box.host.http.answers[POST] = (200, "{}")
        box.go(minutes=5)
        self.assertEqual(box.host.posts()[-1]["title"], "h: Cronjobs, Failed runs")
        self.assertEqual(box.last()["sent"]["cronjobs#failed"]["level"], "danger")
        self.assertEqual((box.items()["ntfy"], box.items()["mail"]), ("ok", "warning"),
                         "ntfy took it; e-mail missed it and stays failed")
        posts = len(box.host.posts())
        box.go(minutes=5)
        self.assertEqual(len(box.host.posts()), posts, "told: not again")


class WatchdogAlone(unittest.TestCase):
    def test_8_the_watchdog_not_reached_changes_nothing_else(self):
        for failing in (True, False):
            with self.subTest(mail_failing=failing):
                box = mail_failing() if failing else Box()
                if not failing:
                    box.go()
                before = box.last()
                box.dog.answers = ["timeout"]
                box.go(minutes=5)
                self.assertEqual(len(box.dog.calls), 1 + hb.ATTEMPTS)
                after = box.last()
                self.assertEqual((after["sent"], after["channels"]), (before["sent"], before["channels"]))
                self.assertEqual(box.host.file("heartbeat.json")["result"]["reason"], "timeout")
                self.assertEqual(box.items()["heartbeat"], "warning")
                self.assertEqual(box.items()["mail"], "warning" if failing else "ok")
                box.dog.answers = []
                box.go(minutes=5)
                self.assertEqual(box.items()["heartbeat"], "ok")
                self.assertEqual(box.last()["channels"], before["channels"])
                self.assertEqual(len(box.host.posts()), 1)


class Lock(unittest.TestCase):
    def test_a_test_while_a_run_holds_the_lock_changes_nothing(self):
        box = mail_failing()
        before = box.last()
        box.host.smtp.fail = None
        held = notify.lock(box.host.env())
        try:
            code, out = box.go("test", "mail", minutes=1)
        finally:
            os.close(held)
        self.assertEqual((code, out), (75, ""))
        self.assertEqual(box.last(), before)
        self.assertEqual(box.host.smtp.sent, [])

    def test_the_test_writes_last_json_whole_with_its_mode_and_no_token(self):
        box = Box()
        box.go("test", "ntfy")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(box.host.state, "last.json")).st_mode), 0o600)
        self.assertEqual(sorted(box.last()), ["access", "channels", "format"], "before the first run as well")
        self.assertFalse(os.path.exists(os.path.join(box.host.state, "status.json")), "no status before the first run")
        box.go("test", "mail")
        box.go()
        self.assertEqual([n for n in os.listdir(box.host.state) if n.endswith(".tmp")], [])
        for name in os.listdir(box.host.state):
            with open(os.path.join(box.host.state, name), encoding="utf-8") as f:
                self.assertNotIn(TOKEN, f.read(), name)
        self.assertEqual(box.channels()["ntfy"]["ok"]["target"], NTFY, "the server and topic, never the token")


class Records(unittest.TestCase):
    """hschannels on its own."""

    CFG = hslib.parse_config(BOTH)[0]

    def test_targets_never_the_token(self):
        self.assertEqual(hschannels.targets(self.CFG), {"ntfy": NTFY, "mail": MAIL})
        self.assertEqual(hschannels.targets(hslib.parse_config("language = de\n")[0]), {})

    def test_what_does_not_hold_is_left_out(self):
        doc = {"ntfy": {"failed": {"since": "x", "at": at(0)}, "ok": {"at": at(0), "via": "boom", "target": 7},
                        "recovered": {"at": at(0)}, "removed": "yes", "reset_at": 5},
               "mail": [], "sms": {"ok": {"at": at(0)}}}
        self.assertEqual(hschannels.records_of(doc), {"ntfy": {"ok": {"at": at(0), "via": "run", "target": None}}})
        self.assertEqual(hschannels.records_of(None), {})
        f = {"failed": {"since": at(0), "at": at(1), "via": "test", "reason": "<b>", "target": "x" * 400}}
        self.assertEqual(hschannels.records_of({"mail": f})["mail"]["failed"],
                         {"since": at(0), "at": at(1), "via": "test", "reason": "network", "target": None})

    def test_states(self):
        self.assertEqual(hschannels.state_of(None, MAIL), "unknown")
        self.assertEqual(hschannels.state_of({"ok": {"at": at(0), "via": "run", "target": None}}, MAIL), "unknown")
        self.assertEqual(hschannels.state_of({"ok": {"at": at(0), "via": "run", "target": MAIL}}, MAIL), "ok")
        self.assertEqual(hschannels.state_of({"ok": {"at": at(0), "via": "run", "target": MAIL}}, "b@example.com"), "unknown")
        self.assertEqual(hschannels.state_of({"failed": {"since": at(0)}, "ok": {"target": MAIL}}, MAIL), "failed")

    def test_record(self):
        recs = {}
        self.assertEqual(hschannels.record(recs, "mail", False, "no-mta", MAIL, "run", at(0), MAIL), "failed")
        self.assertIsNone(hschannels.record(recs, "mail", False, "refused", MAIL, "test", at(1), MAIL))
        self.assertEqual(recs["mail"]["failed"], {"since": at(0), "at": at(1), "via": "test", "reason": "refused", "target": MAIL})
        self.assertEqual(hschannels.record(recs, "mail", True, "sent", "old@example.com", "test", at(2), MAIL), "stale")
        self.assertEqual(hschannels.record(recs, "mail", True, "sent", MAIL, "test", at(3), None), "stale")
        self.assertEqual(hschannels.record(recs, "mail", True, "sent", MAIL, "test", at(4), MAIL), "recovered")
        self.assertIsNone(hschannels.record(recs, "mail", True, "sent", MAIL, "run", at(5), MAIL))
        self.assertEqual(recs["mail"], {"ok": {"at": at(5), "via": "run", "target": MAIL},
                                        "recovered": {"at": at(4), "via": "test", "since": at(0), "reason": "refused"}})
        self.assertEqual(hschannels.health(recs, self.CFG)["mail"],
                         {"state": "ok", "at": at(5), "via": "run", "recovered": None})

    def test_reconcile(self):
        recs = {"mail": {"failed": {"since": at(0), "at": at(0), "via": "run", "reason": "no-mta", "target": MAIL}},
                "ntfy": {"ok": {"at": at(0), "via": "run", "target": NTFY}}}
        only_ntfy = hslib.parse_config("ntfy_url = https://ntfy.example.com\nntfy_topic = cockpit-h\n")[0]
        out, notes = hschannels.reconcile(recs, only_ntfy, None, NOW + 60)
        self.assertEqual(out["mail"], {"removed": {"at": at(1), "failed_since": at(0)}})
        self.assertEqual(notes, [("mail", "removed", at(0))])
        self.assertIn("failed", recs["mail"], "the records handed in stay as they were")
        out, notes = hschannels.reconcile(out, self.CFG, {"at": at(0), "results": {"mail": {"ok": False, "reason": "no-mta"}}},
                                          NOW + 120)
        self.assertEqual((out["mail"], notes), ({"reset_at": at(2)}, [("mail", "reset", None)]))
        # A delivery newer than the records is taken in; one they know is not.
        newer = {"at": at(3), "results": {"ntfy": {"ok": False, "reason": "rejected"}, "mail": {"ok": True, "reason": "sent"}}}
        out, notes = hschannels.reconcile(out, self.CFG, newer, NOW + 240)
        self.assertEqual(out["ntfy"]["failed"]["target"], None)
        self.assertEqual(out["mail"]["ok"], {"at": at(3), "via": "run", "target": None})
        self.assertEqual(sorted(notes), [("mail", "taken", "sent"), ("ntfy", "taken", "rejected")])
        self.assertEqual(hschannels.reconcile(out, self.CFG, newer, NOW + 300)[1], [])
        self.assertEqual(hschannels.failing(out, self.CFG), [("ntfy", "rejected")])

    def test_check_outcome(self):
        self.assertEqual(hschannels.check_outcome([{"id": "reach", "result": "ok"}, {"id": "publish", "result": "ok"}]),
                         (True, "sent"))
        self.assertEqual(hschannels.check_outcome([{"id": "reach", "result": "failed", "reason": "tls"},
                                                   {"id": "publish", "result": "skipped", "reason": "unreachable"}]),
                         (False, "tls"))
        self.assertEqual(hschannels.check_outcome([{"id": "publish", "result": "failed", "reason": "no-token"}]),
                         (False, "no-token"))
        self.assertEqual(hschannels.check_outcome(None), (False, "network"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
