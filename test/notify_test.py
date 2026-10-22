#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The notifier's rules (lib/hslib.py, lib/hsnotify.py), offline:

- every answer of the status verb in test/fixtures/board (Status Board's own fixtures,
  tools/board-fixtures.sha256) is judged as the board judges it (cockpit-status-board,
  test/status.test.js, EXPECT), and so are its manifests;
- a status command must belong to root; exit codes and broken answers become warnings;
- changes per item, an hour against flapping, unreadable is no reason for OK;
- the texts of the messages, in English and German, cut to what ntfy takes;
- ntfy as JSON to the server's root URL, the token only as a header; SMTP to localhost:25;
- "Check connection": every step against recorded answers of an ntfy server.
No request leaves the test: the network, SMTP, the commands and the clock are stand-ins."""

import json
import os
import stat
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import hslib  # noqa: E402
import hsnotify  # noqa: E402
import hsproc  # noqa: E402

BOARD = os.path.join(HERE, "fixtures", "board")
NOW = hslib.parse_time("2026-10-19T19:00:00Z")     # the time the board's fixtures are written for

# As the board's test/status.test.js: pkg is the package the answer is read for (default: the
# addon it names).
EXPECT = {
    "good-image-updates.json": dict(state="info", reported="info", items=4, key="updates", value="3", link="#/updates"),
    "good-image-updates.de.json": dict(state="info", reported="info", items=4, key="updates", value="3"),
    "good-image-updates-stale.json": dict(state="warning", reported="ok", items=1, key="updates", stale=True, by_age=True),
    "good-image-updates-stale.de.json": dict(state="warning", reported="ok", items=1, key="updates", stale=True,
                                             by_age=True),
    "good-cronjobs-danger.json": dict(state="danger", reported="danger", items=2, key="failed-runs", value="2"),
    "good-cronjobs-danger.de.json": dict(state="danger", reported="danger", items=2, key="failed-runs", value="2"),
    "good-cronjobs-ok.json": dict(state="ok", reported="ok", items=2, key="failed-runs", value="0"),
    "good-cronjobs-ok.de.json": dict(state="ok", reported="ok", items=2, key="failed-runs", value="0"),
    "good-update-gaps.json": dict(state="warning", reported="warning", items=4, key="held", value="2", link="#/held"),
    "good-update-gaps.de.json": dict(state="warning", reported="warning", items=4, key="held", value="2"),
    "real-cronjobs.json": dict(state="danger", reported="danger", items=3, key="failed", value="1",
                               link="#/overview/results"),
    "real-cronjobs.de.json": dict(state="danger", reported="danger", items=3, key="failed", value="1",
                                  link="#/overview/results"),
    "real-image-updates.json": dict(state="info", reported="info", items=5, key="updates", value="2",
                                    link="#/updates/update"),
    "real-image-updates.de.json": dict(state="info", reported="info", items=5, key="updates", value="2",
                                       link="#/updates/update"),
    "real-update-gaps.json": dict(state="warning", reported="warning", items=6, key="held", value="6", link="#/held"),
    "real-update-gaps.de.json": dict(state="warning", reported="warning", items=6, key="held", value="6", link="#/held"),
    "real-security.json": dict(state="danger", reported="danger", items=5, key="containers", value="1",
                               link="#/containers"),
    "real-security.de.json": dict(state="danger", reported="danger", items=5, key="containers", value="1",
                                  link="#/containers"),
    "real-security-edge.json": dict(state="danger", reported="danger", items=5, key="containers", value="1",
                                    link="#/containers"),
    "real-security-edge.de.json": dict(state="danger", reported="danger", items=5, key="containers", value="1",
                                       link="#/containers"),
    "real-security-ci.json": dict(state="danger", reported="danger", items=5, key="containers", value="5",
                                  link="#/containers"),
    "real-security-ci.de.json": dict(state="danger", reported="danger", items=5, key="containers", value="5",
                                     link="#/containers"),
    "real-security-ipv6.json": dict(state="danger", reported="danger", items=5, key="containers", value="1",
                                    link="#/containers"),
    "real-security-ipv6.de.json": dict(state="danger", reported="danger", items=5, key="containers", value="1",
                                       link="#/containers"),
    "good-minimal.json": dict(state="none", reported="none", items=0, key=None),
    "good-understated.json": dict(state="danger", reported="ok", items=1, key="cves", dropped=2),

    "broken-not-json.txt": dict(pkg="update-gaps", reason="not-json"),
    "broken-empty.txt": dict(pkg="cronjobs", reason="not-json"),
    "broken-truncated.json": dict(pkg="cronjobs", reason="not-json"),
    "broken-schema-2.json": dict(pkg="image-updates", reason="schema", detail="2"),
    "broken-no-schema.json": dict(pkg="image-updates", reason="schema", detail=None),
    "broken-wrong-addon.json": dict(pkg="update-gaps", reason="addon-mismatch", detail="cronjobs"),
    "broken-state.json": dict(pkg="cronjobs", reason="state"),
    "broken-items-object.json": dict(pkg="cronjobs", reason="items"),
    "broken-array.json": dict(pkg="cronjobs", reason="not-object"),

    "hostile-markup.json": dict(state="warning", reported="warning", items=4, key="xss", value="<i>7</i>", link=None),
    "hostile-bidi.json": dict(state="ok", reported="ok", items=1, dropped=1, key="y", value="5"),
    "hostile-proto.json": dict(state="ok", reported="ok", items=1, key="p"),
    "hostile-addon-path.json": dict(pkg="cronjobs", reason="addon"),
    "hostile-schema-string.json": dict(pkg="cronjobs", reason="schema", detail="1"),
    "hostile-types.json": dict(state="info", reported="info", items=4, dropped=6, key="u"),
    "hostile-long.json": dict(state="info", reported="info", items=hslib.MAX_ITEMS, dropped=31, key="item-0"),
}
INVISIBLE = [chr(c) for c in list(range(0, 0x20)) + [0x7F] + list(range(0x200B, 0x2010)) + list(range(0x202A, 0x202F)) +
             list(range(0x2066, 0x206A)) + [0xFEFF]]


def board(rel):
    with open(os.path.join(BOARD, rel), encoding="utf-8") as f:
        return f.read()


class BoardFixtures(unittest.TestCase):
    def test_every_fixture_has_an_expectation(self):
        self.assertEqual(sorted(os.listdir(os.path.join(BOARD, "status"))), sorted(EXPECT))

    def test_judged_as_the_board_judges(self):
        for name, want in sorted(EXPECT.items()):
            with self.subTest(name):
                text = board("status/" + name)
                pkg = want.get("pkg")
                if pkg is None:
                    try:
                        pkg = json.loads(text).get("addon")
                    except ValueError:
                        pkg = None
                ok, st = hslib.validate_status(text, pkg)
                if "reason" in want:
                    self.assertFalse(ok)
                    self.assertEqual(st[0], want["reason"])
                    if "detail" in want:
                        self.assertEqual(st[1], want["detail"])
                    continue
                self.assertTrue(ok, st)
                self.assertEqual(st["addon"], pkg)
                self.assertEqual(st["state"], want["reported"])
                self.assertEqual(len(st["items"]), want["items"])
                self.assertEqual(st["dropped"], want.get("dropped", 0))
                ev = hslib.evaluate(st, NOW)
                self.assertEqual(ev["state"], want["state"])
                self.assertEqual(ev["key"]["id"] if ev["key"] else None, want["key"])
                if "value" in want:
                    self.assertEqual(ev["key"]["value"], want["value"])
                if "link" in want:
                    self.assertEqual(ev["key"]["link"], want["link"])
                self.assertEqual(ev["stale_since"] is not None, want.get("stale", False))
                self.assertEqual(ev["by_age"], want.get("by_age", False))
                texts = [st["label"], st["version"], st["summary"]] + \
                    [x for i in st["items"] for x in (i["title"], i["value"], i["detail"])]
                for x in texts:
                    if x is not None:
                        self.assertIsInstance(x, str)
                        self.assertFalse(any(c in x for c in INVISIBLE), repr(x))

    def test_markup_stays_text_and_texts_are_cut(self):
        ok, st = hslib.validate_status(board("status/hostile-markup.json"), "cronjobs")
        self.assertEqual(st["label"], "<img src=x onerror=\"window.__pwned='label'\">")
        self.assertEqual(st["version"], "<script>window.__pwned='version'</script>"[:39] + "…")
        ok, st = hslib.validate_status(board("status/hostile-bidi.json"), "update-gaps")
        self.assertEqual(st["label"], "Updatespag Gaps")
        self.assertEqual(st["summary"], "line one line two tabbedbellzerowidthisolate end")
        ok, st = hslib.validate_status(board("status/hostile-long.json"), "update-gaps")
        self.assertEqual(len(st["label"]), hslib.LIMITS["label"])
        self.assertTrue(st["label"].endswith("…"))

    def test_the_addon_must_be_the_package(self):
        text = board("status/good-cronjobs-ok.json")
        self.assertTrue(hslib.validate_status(text, "cronjobs")[0])
        self.assertEqual(hslib.validate_status(text, "image-updates")[1][0], "addon-mismatch")

    def test_too_large_and_odd_json(self):
        self.assertEqual(hslib.validate_status("x" * (hslib.MAX_OUTPUT + 1))[1][0], "too-large")
        self.assertEqual(hslib.validate_status('{"schema": NaN}')[1][0], "not-json")
        self.assertEqual(hslib.validate_status('{"schema": true, "addon": "a", "state": "ok", "items": []}')[1],
                         ("schema", None))
        self.assertTrue(hslib.validate_status('{"schema": 1.0, "addon": "a", "state": "ok", "items": []}')[0])
        self.assertEqual(hslib.validate_status('{"schema": 1, "addon": "a\\n", "state": "ok", "items": []}')[1][0],
                         "addon")

    def test_manifests(self):
        def m(name):
            return json.loads(board("manifests/" + name + ".json"))
        self.assertEqual(hslib.status_entry(m("cronjobs")), ("command", "/usr/lib/cockpit-cronjobs/cronjobs-status"))
        for bad in ("hostile-sh", "hostile-dotdot", "hostile-shapes"):
            self.assertEqual(hslib.status_entry(m(bad)), ("rejected", None), bad)
        self.assertEqual(hslib.status_entry(m("hostile-args")), ("command", "/usr/lib/cockpit-extra/status-helper"))
        self.assertIsNone(hslib.status_entry(m("system")))
        self.assertEqual(hslib.menu_label(m("cronjobs")), "Cron Jobs")
        self.assertEqual(hslib.menu_label(m("hostile-shapes")), "Shapes")
        self.assertEqual(hslib.status_argv("/usr/lib/cockpit-x/y", "de"), ["/usr/lib/cockpit-x/y", "status", "--lang", "de"])
        self.assertEqual(hslib.status_argv("/usr/lib/cockpit-x/y", "fr")[-1], "en")
        with open(os.path.join(HERE, "..", "plugin", "manifest.json"), encoding="utf-8") as f:
            own = json.load(f)
        self.assertEqual(hslib.status_entry(own), ("command", "/usr/lib/cockpit-host-settings/host-settings.py"))


# ---------------------------------------------------------------------------
# Reading the add-ons

class St:
    def __init__(self, mode, uid=0):
        self.st_mode, self.st_uid = mode, uid


def lstat_of(table):
    """An os.lstat stand-in: paths in `table` are files (mode, uid), their folders root's 0755."""
    def lstat(path):
        if path in table:
            return St(stat.S_IFREG | table[path][0], table[path][1])
        if any(p.startswith(path.rstrip("/") + "/") for p in table):
            return St(stat.S_IFDIR | 0o755, 0)
        raise FileNotFoundError(path)
    return lstat


class Runner:
    """hsproc.run with recorded answers: {command: Result or callable(argv)}."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, argv, limit=60, max_output=hsproc.MAX_OUTPUT):
        self.calls.append(list(argv))
        a = self.answers[argv[0]]
        return a(argv) if callable(a) else a


def ok(text):
    return hsproc.Result(0, text)


def status_doc(addon, items, state=None, label=None, at="2026-10-19T18:55:00Z"):
    items = [dict({"checked_at": at, "stale_after": 3600}, **i) for i in items]
    worst = max((hslib.rank(i["state"]) for i in items), default=0)
    return json.dumps({"schema": 1, "addon": addon, "label": label or addon.title(), "version": "1.0.0",
                       "checked_at": at, "state": state or hslib.STATES[worst], "summary": "s", "items": items})


CMD = "/usr/lib/cockpit-cronjobs/cronjobs-status"


class ReadAddon(unittest.TestCase):
    def read(self, result, mode=0o755, uid=0, entry=("command", CMD)):
        run = Runner({CMD: result})
        return hsnotify.read_addon("cronjobs", "Cron Jobs", entry, "en", NOW, run, lstat_of({CMD: (mode, uid)})), run

    def test_a_good_answer(self):
        a, run = self.read(ok(board("status/good-cronjobs-danger.json")))
        self.assertEqual((a["kind"], a["state"], a["label"]), ("ok", "danger", "Cron Jobs"))
        self.assertEqual(run.calls, [[CMD, "status", "--lang", "en"]])
        self.assertEqual([i["id"] for i in a["items"]], ["failed-runs", "unrecorded"])

    def test_untrusted_commands_are_not_run(self):
        for mode, uid in ((0o757, 0), (0o775, 0), (0o755, 1000)):
            a, run = self.read(ok("{}"), mode, uid)
            self.assertEqual((a["kind"], a["state"]), ("untrusted", "warning"))
            self.assertEqual(run.calls, [])
        a, run = hsnotify.read_addon("x", None, ("command", CMD), "en", NOW, Runner({}), lstat_of({})), None
        self.assertEqual(a["kind"], "missing")
        a = hsnotify.read_addon("x", None, ("rejected", None), "en", NOW, Runner({}), lstat_of({}))
        self.assertEqual((a["kind"], a["state"], a["label"]), ("rejected", "warning", "x"))

    def test_exit_codes_and_broken_answers(self):
        cases = [(hsproc.Result(77), "noaccess", "none"), (hsproc.Result(64), "unsupported", "warning"),
                 (hsproc.Result(3, "", "boom"), "failed", "warning"), (hsproc.Result(None, timeout=True), "timeout", "warning"),
                 (hsproc.Result(None, missing=True), "missing", "warning"),
                 (hsproc.Result(0, "{", cut=False), "invalid", "warning"),
                 (hsproc.Result(0, "x", cut=True), "invalid", "warning"),
                 (ok(board("status/good-update-gaps.json")), "invalid", "warning")]
        for res, kind, state in cases:
            with self.subTest(kind):
                a, _ = self.read(res)
                self.assertEqual((a["kind"], a["state"]), (kind, state))
        a, _ = self.read(hsproc.Result(3))
        self.assertEqual(a["code"], 3)
        a, _ = self.read(ok(board("status/good-update-gaps.json")))
        self.assertEqual(a["reason"], "addon-mismatch")

    def test_out_of_date_items_count_as_warnings(self):
        a, _ = self.read(ok(status_doc("cronjobs", [{"id": "a", "state": "ok", "title": "A"}], at="2026-10-19T17:00:00Z")))
        self.assertEqual(a["items"][0]["state"], "warning")
        self.assertEqual(a["items"][0]["stale_at"], hslib.parse_time("2026-10-19T18:00:00Z"))

    def test_discover(self):
        import tempfile
        root = tempfile.mkdtemp()
        for pkg, doc in (("cronjobs", board("manifests/cronjobs.json")), ("system", board("manifests/system.json")),
                         ("evil", board("manifests/hostile-sh.json")), ("host-settings", '{"azentrius": {"status": ["/usr/lib/cockpit-host-settings/x"]}}'),
                         ("Bad_Name", board("manifests/cronjobs.json")), ("broken", "{")):
            os.makedirs(os.path.join(root, pkg))
            with open(os.path.join(root, pkg, "manifest.json"), "w", encoding="utf-8") as f:
                f.write(doc)
        found = hsnotify.discover(root)
        self.assertEqual(found, [("broken", None, ("manifest", None)),
                                 ("cronjobs", "Cron Jobs", ("command", "/usr/lib/cockpit-cronjobs/cronjobs-status")),
                                 ("evil", "Image Update Monitor", ("rejected", None))])
        self.assertEqual(hsnotify.discover(os.path.join(root, "nothing")), [])


# ---------------------------------------------------------------------------
# What changed

def addon(label, items, kind="ok", reason=None):
    return {"label": label, "kind": kind, "reason": reason, "code": None, "state": "none", "version": None,
            "summary": None, "items": [dict({"value": None, "detail": None, "link": None, "stale_at": None, "title": i["id"].upper()}, **i)
                                       for i in items]}


class Changes(unittest.TestCase):
    def test_new_problems_and_their_end(self):
        snap = {"cronjobs": addon("Cron Jobs", [{"id": "failed", "state": "danger", "value": "2"}, {"id": "cron", "state": "ok"}])}
        events, sent = hsnotify.changes({}, snap, NOW)
        self.assertEqual([(e["key"], e["kind"], e["from"], e["to"]) for e in events],
                         [("cronjobs#failed", "worse", "ok", "danger")])
        self.assertEqual(sent["cronjobs#failed"]["level"], "danger")
        self.assertNotIn("cronjobs#cron", sent)
        # nothing new, nothing told
        again, sent2 = hsnotify.changes(sent, snap, NOW + 300)
        self.assertEqual(again, [])
        self.assertEqual(sent2["cronjobs#failed"]["level"], "danger")
        # better, then OK again
        snap["cronjobs"]["items"][0]["state"] = "warning"
        events, sent3 = hsnotify.changes(sent2, snap, NOW + 600)
        self.assertEqual([(e["kind"], e["from"], e["to"]) for e in events], [("better", "danger", "warning")])
        snap["cronjobs"]["items"][0]["state"] = "info"
        events, sent4 = hsnotify.changes(sent3, snap, NOW + 900)
        self.assertEqual([(e["kind"], e["to"]) for e in events], [("resolved", "ok")])
        self.assertEqual(sent4["cronjobs#failed"]["level"], "ok")
        # an OK entry is forgotten once its last change is an hour old
        _, sent5 = hsnotify.changes(sent4, snap, NOW + 900 + hsnotify.FLAP)
        self.assertNotIn("cronjobs#failed", sent5)

    def test_a_second_finding_in_an_addon_that_is_already_warning(self):
        snap = {"gaps": addon("Update Gaps", [{"id": "held", "state": "warning"}])}
        _, sent = hsnotify.changes({}, snap, NOW)
        snap["gaps"]["items"].append({"id": "restart", "state": "warning", "title": "Restart", "value": None, "detail": None,
                                      "link": None, "stale_at": None})
        events, _ = hsnotify.changes(sent, snap, NOW + 300)
        self.assertEqual([e["key"] for e in events], ["gaps#restart"])

    def test_flapping_is_told_once_an_hour(self):
        snap = {"a": addon("A", [{"id": "x", "state": "warning"}])}
        events, sent = hsnotify.changes({}, snap, NOW)
        self.assertEqual(len(events), 1)
        snap["a"]["items"][0]["state"] = "ok"
        events, sent = hsnotify.changes(sent, snap, NOW + 300)
        self.assertEqual([e["kind"] for e in events], ["resolved"])
        snap["a"]["items"][0]["state"] = "warning"
        events, sent = hsnotify.changes(sent, snap, NOW + 600)
        self.assertEqual(events, [], "the same change within the hour waits")
        self.assertEqual(sent["a#x"]["level"], "ok")
        events, sent = hsnotify.changes(sent, snap, NOW + hsnotify.FLAP + 1)
        self.assertEqual([e["kind"] for e in events], ["worse"], "told after the hour, as it still holds")

    def test_unreadable_is_no_reason_for_ok(self):
        snap = {"sec": addon("Security Monitor", [{"id": "containers", "state": "danger"}])}
        _, sent = hsnotify.changes({}, snap, NOW)
        snap = {"sec": addon("Security Monitor", [], kind="invalid", reason="not-json")}
        events, sent = hsnotify.changes(sent, snap, NOW + 300)
        self.assertEqual([(e["key"], e["kind"], e["addon_kind"], e["reason"]) for e in events],
                         [("sec#", "worse", "invalid", "not-json")])
        self.assertEqual(sent["sec#containers"]["level"], "danger", "the item keeps its level")
        snap = {"sec": addon("Security Monitor", [{"id": "containers", "state": "danger"}])}
        events, sent = hsnotify.changes(sent, snap, NOW + 600)
        self.assertEqual([(e["key"], e["kind"]) for e in events], [("sec#", "resolved")])

    def test_noaccess_and_removed_addons(self):
        snap = {"img": addon("Image Update Monitor", [{"id": "updates", "state": "warning"}])}
        _, sent = hsnotify.changes({}, snap, NOW)
        events, sent = hsnotify.changes(sent, {"img": addon("Image Update Monitor", [], kind="noaccess")}, NOW + 60)
        self.assertEqual(events, [], "exit 77 says nothing either way")
        events, sent = hsnotify.changes(sent, {}, NOW + 120)
        self.assertEqual([(e["key"], e["kind"], e["why"], e["label"]) for e in events],
                         [("img#updates", "resolved", "removed", "Image Update Monitor")])

    def test_baseline(self):
        snap = {"a": addon("A", [{"id": "x", "state": "warning"}, {"id": "y", "state": "ok"}]),
                "b": addon("B", [], kind="timeout")}
        base = hsnotify.baseline(snap, NOW)
        self.assertEqual(sorted(base), ["a#x", "b#"])
        events, _ = hsnotify.changes(base, snap, NOW + 300)
        self.assertEqual(events, [])

    def test_events_are_ordered(self):
        snap = {"b": addon("B", [{"id": "w", "state": "warning"}]), "a": addon("A", [{"id": "d", "state": "danger"}])}
        events, _ = hsnotify.changes({"z#gone": {"level": "warning", "at": NOW - 9999, "last": {}, "label": "Z"}}, snap, NOW)
        self.assertEqual([e["key"] for e in events], ["a#d", "b#w", "z#gone"])


# ---------------------------------------------------------------------------
# Messages

class Messages(unittest.TestCase):
    def test_one_change(self):
        snap = {"cronjobs": addon("Cron Jobs", [{"id": "failed", "state": "danger", "value": "2", "title": "Failed runs",
                                                 "detail": "backup, mail"}])}
        events, _ = hsnotify.changes({}, snap, NOW)
        msg = hsnotify.message("en", "web1", events=events)
        self.assertEqual(msg["title"], "web1: Cron Jobs, Failed runs")
        self.assertEqual(msg["body"], "Danger · Cron Jobs · Failed runs (2)\nbackup, mail\n\nHost Settings on web1")
        self.assertEqual((msg["priority"], msg["tags"]), (4, ["rotating_light"]))
        de = hsnotify.message("de", "web1", events=events)
        self.assertEqual(de["body"].splitlines()[0], "Gefahr · Cron Jobs · Failed runs (2)")
        self.assertTrue(de["body"].endswith("Host Settings auf web1"))

    def test_several_changes_better_resolved_stale_unreadable(self):
        e = [hsnotify._event("a#x", addon("A", []), {"title": "X", "state": "warning", "value": None, "detail": "d",
                                                     "stale_at": hslib.parse_time("2026-10-19T18:00:00Z")}, "ok", "warning"),
             hsnotify._event("b#y", addon("B", []), {"title": "Y", "state": "warning", "value": "1", "detail": None,
                                                     "stale_at": None}, "danger", "warning"),
             hsnotify._event("c#", addon("C", [], kind="failed"), None, "ok", "warning"),
             hsnotify._event("d#z", {}, None, "warning", "ok", "removed", {"label": "D", "title": "Z"})]
        e[2]["code"] = 2
        msg = hsnotify.message("en", "h", events=e)
        self.assertEqual(msg["title"], "h: 4 changes")
        self.assertEqual(msg["body"].split("\n\n"), [
            "Warning · A · X\nout of date since 19 Oct 18:00 UTC; d",
            "Warning instead of Danger · B · Y (1)",
            "Warning · C · Status not readable\nthe command failed (exit 2)",
            "OK again · D · Z\nthe add-on is no longer installed",
            "Host Settings on h"])
        self.assertEqual(msg["priority"], 3)
        de = hsnotify.message("de", "h", events=e)
        self.assertIn("Achtung statt Gefahr · B · Y (1)", de["body"])
        self.assertIn("veraltet seit 19.10. 18:00 UTC; d", de["body"])
        self.assertIn("der Befehl schlug fehl (Exit 2)", de["body"])

    def test_resolved_addon_reads_as_readable_again(self):
        e = hsnotify._event("c#", addon("C", []), None, "warning", "ok")
        msg = hsnotify.message("en", "h", events=[e])
        self.assertEqual(msg["title"], "h: C, status readable")
        self.assertEqual(msg["body"].split("\n\n")[0], "OK again · C · status readable")
        self.assertEqual((msg["priority"], msg["tags"]), (2, ["white_check_mark"]))

    def test_summary(self):
        snap = {"a": addon("A", [{"id": "x", "state": "warning"}]), "b": addon("B", [{"id": "y", "state": "danger"}]),
                "c": addon("C", [{"id": "z", "state": "ok"}])}
        msg = hsnotify.message("en", "h", snapshot=snap, summary=True)
        self.assertEqual(msg["title"], "h: messages set up")
        self.assertEqual(msg["body"].split("\n\n")[:4], [
            "Messages about the Azentrius add-ons of this host now come this way.", "Right now:",
            "Danger · B · Y", "Warning · A · X"])
        self.assertEqual(msg["priority"], 4)
        clean = hsnotify.message("de", "h", snapshot={"c": snap["c"]}, summary=True)
        self.assertIn("Im Moment keine Warnungen; 1 Add-ons gelesen.", clean["body"])
        self.assertEqual((clean["priority"], clean["tags"]), (3, ["white_check_mark"]))
        empty = hsnotify.message("en", "h", snapshot={}, summary=True)
        self.assertIn("No add-on with a status was found on this host.", empty["body"])

    def test_long_messages_fit_into_ntfy(self):
        items = [{"id": f"i{n}", "state": "warning", "detail": "x" * 190} for n in range(60)]
        snap = {"a": addon("A", items)}
        events, _ = hsnotify.changes({}, snap, NOW)
        msg = hsnotify.message("en", "h", events=events)
        self.assertLessEqual(len(msg["body"].encode("utf-8")), hsnotify.BODY_MAX)
        self.assertRegex(msg["body"], r"and \d+ more\n\nHost Settings on h$")


# ---------------------------------------------------------------------------
# The channels

class Http:
    """hsnotify.http with recorded answers: {(method, url-without-query): (status, body)} or a
    NetError reason; every request is kept."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, method, url, body=None, headers=None, timeout=20, limit=65536):
        self.calls.append({"method": method, "url": url, "body": json.loads(body) if body else None,
                           "headers": dict(headers or {})})
        key = (method, url.split("?")[0], "auth" if (headers or {}).get("Authorization") else "anon")
        a = self.answers.get(key, self.answers.get((method, url.split("?")[0])))
        if a is None:
            raise AssertionError(f"unexpected request {method} {url}")
        if isinstance(a, str):
            raise hsnotify.NetError(a)
        return a[0], a[1].encode("utf-8") if isinstance(a[1], str) else a[1]


CFG = {"ntfy_url": "https://ntfy.example.com", "ntfy_topic": "cockpit-h", "mail_to": "ops@example.com", "language": "en"}
TOKEN = "tk_testtokentesttokentesttokent"


class Ntfy(unittest.TestCase):
    def test_publish_as_json_with_the_token_as_a_header(self):
        http = Http({("POST", "https://ntfy.example.com/"): (200, "{}")})
        r = hsnotify.send_ntfy(CFG, {"title": "T ä", "body": "B", "priority": 4, "tags": ["warning"]}, TOKEN, http)
        self.assertEqual(r, {"ok": True, "code": 200, "reason": "sent"})
        call = http.calls[0]
        self.assertEqual(call["body"], {"topic": "cockpit-h", "title": "T ä", "message": "B", "priority": 4,
                                        "tags": ["warning"]})
        self.assertEqual(call["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertNotIn(TOKEN, json.dumps(call["body"]))
        http = Http({("POST", "https://ntfy.example.com/"): (200, "{}")})
        hsnotify.send_ntfy(CFG, {"title": "T", "body": "B", "priority": 3, "tags": []}, None, http)
        self.assertNotIn("Authorization", http.calls[0]["headers"])

    def test_refusals_and_network_errors(self):
        for answer, reason in (((401, ""), "rejected"), ((403, ""), "rejected"), ((429, ""), "rate-limited"),
                               ((413, ""), "too-large"), ((500, ""), "http-500"), ((301, ""), "http-301"),
                               ("tls", "tls"), ("timeout", "timeout"), ("network", "network")):
            http = Http({("POST", "https://ntfy.example.com/"): answer})
            r = hsnotify.send_ntfy(CFG, hsnotify.test_message("en", "h"), TOKEN, http)
            self.assertEqual((r["ok"], r["reason"]), (False, reason))

    def test_redirects_are_not_followed(self):
        handler = hsnotify._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "http://elsewhere/"))


class Smtp:
    def __init__(self, fail=None):
        self.fail, self.sent = fail, []

    def __call__(self):
        return self

    def __enter__(self):
        if isinstance(self.fail, BaseException) and isinstance(self.fail, ConnectionRefusedError):
            raise self.fail
        return self

    def __exit__(self, *exc):
        return False

    def send_message(self, m):
        if self.fail is not None:
            raise self.fail
        self.sent.append(m)


class Mail(unittest.TestCase):
    def test_a_message_to_the_local_mta(self):
        smtp = Smtp()
        r = hsnotify.send_mail(CFG, {"title": "h: Cron Jobs, Failed", "body": "Gefahr · ä", "priority": 4, "tags": []},
                               "h.example.com", smtp)
        self.assertEqual(r, {"ok": True, "code": None, "reason": "sent"})
        m = smtp.sent[0]
        self.assertEqual(m["To"], "ops@example.com")
        self.assertEqual(m["From"], "Host Settings <cockpit-host-settings@h.example.com>")
        self.assertEqual(m["Auto-Submitted"], "auto-generated")
        self.assertEqual(m["Subject"], "h: Cron Jobs, Failed")
        self.assertIn("Gefahr · ä", m.get_content())

    def test_failures(self):
        import smtplib
        cases = ((ConnectionRefusedError(), "no-mta"), (smtplib.SMTPRecipientsRefused({}), "refused"),
                 (smtplib.SMTPDataError(554, b"no"), "refused"), (smtplib.SMTPServerDisconnected("x"), "smtp"),
                 (OSError("x"), "network"))
        for exc, reason in cases:
            with self.subTest(reason):
                r = hsnotify.send_mail(CFG, hsnotify.test_message("en", "h"), "h", Smtp(exc))
                self.assertEqual((r["ok"], r["reason"]), (False, reason))


# Answers of an ntfy server with auth-default-access: deny-all and a user with write-only access
# to its topic (docs.ntfy.sh, config: access control; publish as JSON; subscribe with poll=1).
CLOSED = {("GET", "https://ntfy.example.com/v1/health"): (200, '{"healthy":true}'),
          ("POST", "https://ntfy.example.com/", "auth"): (200, '{"id":"x"}'),
          ("POST", "https://ntfy.example.com/", "anon"): (403, '{"code":40301,"http":403,"error":"forbidden"}'),
          ("GET", "https://ntfy.example.com/cockpit-h/json", "anon"): (403, '{"code":40301}'),
          ("GET", "https://ntfy.example.com/v1/account", "auth"): (
              200, '{"username":"cockpit-h","role":"user","grants":[{"topic":"cockpit-h","permission":"write-only"}]}')}


def results(steps):
    return {s["id"]: (s["result"], s["reason"]) for s in steps}


class Check(unittest.TestCase):
    def test_a_closed_server(self):
        http = Http(CLOSED)
        steps = hsnotify.check_ntfy(CFG, TOKEN, "en", "h", http)
        self.assertEqual([s["id"] for s in steps], list(hsnotify.STEPS))
        self.assertEqual(results(steps), {"reach": ("ok", "answered"), "tls": ("ok", "https"), "publish": ("ok", "sent"),
                                          "anon-read": ("ok", "protected"), "anon-write": ("ok", "protected"),
                                          "token": ("ok", "write-only")})
        self.assertEqual(hsnotify.access_of(steps), {"state": "ok", "anon_read": "ok", "anon_write": "ok"})
        anon = [c for c in http.calls if "Authorization" not in c["headers"]]
        self.assertEqual([(c["method"], c["url"]) for c in anon if c["url"] != "https://ntfy.example.com/v1/health"],
                         [("GET", "https://ntfy.example.com/cockpit-h/json?poll=1&since=latest"),
                          ("POST", "https://ntfy.example.com/")])
        probe = anon[-1]["body"]
        self.assertEqual(probe["topic"], "cockpit-h")
        self.assertIn("auth-default-access: deny-all", probe["message"])

    def test_an_open_server(self):
        answers = dict(CLOSED)
        answers.update({("POST", "https://ntfy.example.com/", "anon"): (200, "{}"),
                        ("GET", "https://ntfy.example.com/cockpit-h/json", "anon"): (200, ""),
                        ("GET", "https://ntfy.example.com/v1/account", "auth"): (
                            200, '{"role":"user","grants":[{"topic":"cockpit-*","permission":"read-write"}]}')})
        steps = hsnotify.check_ntfy(CFG, TOKEN, "en", "h", Http(answers))
        self.assertEqual(results(steps)["anon-read"], ("warning", "readable"))
        self.assertEqual(results(steps)["anon-write"], ("warning", "writable"))
        self.assertEqual(results(steps)["token"], ("info", "can-read"))
        self.assertEqual(hsnotify.access_of(steps)["state"], "warning")

    def test_plain_http_no_token_and_an_unreachable_server(self):
        cfg = dict(CFG, ntfy_url="http://ntfy.example.com")
        http = Http({("GET", "http://ntfy.example.com/v1/health"): (200, "{}"),
                     ("POST", "http://ntfy.example.com/"): (403, ""),
                     ("GET", "http://ntfy.example.com/cockpit-h/json"): (403, "")})
        r = results(hsnotify.check_ntfy(cfg, None, "en", "h", http))
        self.assertEqual(r["tls"], ("warning", "unencrypted"))
        self.assertEqual(r["publish"], ("failed", "no-token"))
        self.assertEqual(r["token"], ("skipped", "no-token"))
        down = Http({("GET", "https://ntfy.example.com/v1/health"): "network"})
        r = results(hsnotify.check_ntfy(CFG, TOKEN, "en", "h", down))
        self.assertEqual(r["reach"], ("failed", "network"))
        self.assertEqual({k: v for k, v in r.items() if k not in ("reach", "tls")},
                         {k: ("skipped", "unreachable") for k in ("publish", "anon-read", "anon-write", "token")})
        self.assertEqual(len(down.calls), 1)

    def test_the_daily_check_reads_only(self):
        http = Http(CLOSED)
        steps = hsnotify.check_ntfy(CFG, TOKEN, "en", "h", http, ("reach", "tls", "anon-read"))
        self.assertEqual([s["id"] for s in steps], ["reach", "tls", "anon-read"])
        self.assertFalse([c for c in http.calls if c["method"] == "POST"], "nothing is published")
        self.assertEqual(hsnotify.access_of(steps)["state"], "none", "the write probe was never made")
        self.assertEqual(hsnotify.access_of(steps, {"anon_write": "ok"})["state"], "ok")
        self.assertEqual(hsnotify.access_of(steps, {"anon_write": "warning"})["state"], "warning")
        self.assertEqual(hsnotify.access_of([{"id": "anon-read", "result": "info"}], {"anon_read": "ok", "anon_write": "ok"}),
                         {"state": "ok", "anon_read": "ok", "anon_write": "ok"})

    def test_token_rights(self):
        f = hsnotify._token_rights
        self.assertEqual(f(200, b'{"role":"admin"}', "t"), ("info", "admin", 200))
        self.assertEqual(f(200, b'{"grants":[{"topic":"other","permission":"write-only"}]}', "t"), ("info", "unknown", 200))
        self.assertEqual(f(200, b"not json", "t"), ("info", "unknown", 200))
        self.assertEqual(f(401, b"", "t"), ("info", "unknown", 401))


class Settings(unittest.TestCase):
    def test_parse_and_format(self):
        text = ("# c\nntfy_url = https://ntfy.example.com/\nntfy_topic = cockpit-h\nmail_to = ops@example.com\n"
                "language = de\nbogus = 1\nntfy_url = https://user:pw@evil/\nnonsense\n")
        cfg, bad = hslib.parse_config(text)
        self.assertEqual(cfg, {"ntfy_url": "https://ntfy.example.com", "ntfy_topic": "cockpit-h", "mail_to": "ops@example.com",
                               "language": "de"})
        self.assertEqual(bad, [6, 7, 8])
        self.assertEqual(hslib.parse_config(hslib.format_config(cfg))[0], cfg)
        self.assertEqual(hslib.channels(cfg), ["ntfy", "mail"])
        self.assertEqual(hslib.channels(dict(cfg, ntfy_topic=None)), ["mail"])

    def test_values(self):
        good = ["https://ntfy.example.com", "http://10.0.0.5:8080", "https://h/ntfy/", "https://[2001:db8::1]:443/x"]
        bad = ["ftp://h", "https://", "https://h?x=1", "https://h#f", "https://u@h", "https://h:0", "https://h:99999",
               "https://h/a b", "https://-h", "https://h\n", "https://" + "a" * 200]
        for u in good:
            self.assertIsNotNone(hslib.valid_url(u), u)
        for u in bad:
            self.assertIsNone(hslib.valid_url(u), u)
        self.assertEqual(hslib.valid_url("https://h/ntfy/"), "https://h/ntfy")
        self.assertTrue(hslib.valid_topic("cockpit-web1_1"))
        for tp in ("", "a" * 65, "a/b", "a b", "ä", "x\n"):
            self.assertFalse(hslib.valid_topic(tp), tp)
        self.assertTrue(hslib.valid_mail("ops+cockpit@example.com"))
        for m in ("ops", "ops@", "@x", "a@b@c", "a@-b", "a b@c", "a@b\n"):
            self.assertFalse(hslib.valid_mail(m), m)
        self.assertTrue(hslib.valid_token(TOKEN))
        for tk in ("", "a b", "a\nb", "x" * 257):
            self.assertFalse(hslib.valid_token(tk), tk)

    def test_clean_text_and_times(self):
        self.assertEqual(hslib.clean_text("  a\t b\n" + chr(0x202E) + "c" + chr(0x200B) + "  ", 80), "a b c")
        self.assertEqual(hslib.clean_text("abcdef", 4), "abc…")
        self.assertIsNone(hslib.clean_text(chr(0x200B), 10))
        self.assertIsNone(hslib.clean_text(5, 10))
        self.assertEqual(hslib.parse_time("2026-10-19T19:00:00Z"), hslib.parse_time("2026-10-19T21:00:00+02:00"))
        self.assertEqual(hslib.parse_time("2026-10-19T19:00Z"), NOW)
        self.assertAlmostEqual(hslib.parse_time("2026-10-19T19:00:00.123456Z") - NOW, 0.123, places=5)
        for bad in ("2026-10-19T19:00:00", "2026-13-01T00:00:00Z", "2026-10-19 19:00:00Z", "x", None):
            self.assertIsNone(hslib.parse_time(bad), bad)
        self.assertEqual(hslib.iso(NOW), "2026-10-19T19:00:00Z")


if __name__ == "__main__":
    unittest.main(verbosity=1)
