#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The notifier's rules (lib/hslib.py, lib/hsnotify.py), offline:

- every answer of the status verb in test/fixtures/board (Status Board's own fixtures,
  tools/board-fixtures.sha256) is judged as the board judges it (cockpit-status-board,
  test/status.test.js, EXPECT), and so are its manifests;
- a status command must belong to root; exit codes and broken answers become warnings;
- changes per item, an hour against flapping; what cannot be checked (unreadable, exit 77, state
  none, items left out, an add-on removed, findings that break the rules or are no longer sent,
  a list that counts more than it holds) is no reason for OK;
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
    "real-update-gaps.json": dict(state="warning", reported="warning", items=7, key="held", value="6", link="#/held"),
    "real-update-gaps.de.json": dict(state="warning", reported="warning", items=7, key="held", value="6", link="#/held"),
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

    # the optional parts of version 1: checks, findings, a host's profile (the tests OptionalParts)
    "ext-good-security.json": dict(state="danger", reported="danger", items=3, key="exposure", value="2",
                                   link="#/exposure"),
    # says ok: one acceptance has ended, so that finding counts again
    "ext-good-update-gaps.json": dict(state="warning", reported="ok", items=2, key="restart", value=None,
                                      link="#/restart"),
    "ext-good-host-settings.json": dict(state="ok", reported="ok", items=3, key="setup", value="1"),
    "ext-broken-shapes.json": dict(state="warning", reported="warning", items=3, key="y", value="1"),
    "ext-hostile.json": dict(state="danger", reported="warning", items=2, key="exposure", value="120"),
    "ext-hostile-profile.json": dict(state="ok", reported="ok", items=1, key="run"),
    "ext-incompatible-schema-2.json": dict(pkg="security", reason="schema", detail="2"),
    # when a list of findings is complete, shared with the board (the tests Complete); what the
    # item says counts either way
    "ext-complete-total-1-empty.json": dict(state="danger", reported="danger", items=1, key="exposure", value="1",
                                            link="#/exposure"),
    "ext-complete-total-2-one.json": dict(state="danger", reported="danger", items=1, key="exposure", value="2",
                                          link="#/exposure"),
    "ext-complete-total-0-checked.json": dict(state="ok", reported="ok", items=1, key="exposure", value="0",
                                              link="#/exposure"),
    "ext-complete-no-total.json": dict(state="danger", reported="danger", items=1, key="exposure", value="2",
                                       link="#/exposure"),
    "ext-complete-invalid-entry.json": dict(state="danger", reported="danger", items=1, key="exposure", value="2",
                                            link="#/exposure"),
    "ext-complete-duplicate-fid.json": dict(state="danger", reported="danger", items=1, key="exposure", value="2",
                                            link="#/exposure"),
    "ext-complete-over-100.json": dict(state="danger", reported="danger", items=1, key="exposure", value="101",
                                       link="#/exposure"),
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
        self.assertEqual(hslib.status_entry(m("host-settings")), ("command", "/usr/lib/cockpit-host-settings/host-settings.py"))
        # the add-ons of the board's page tests at the edges (labels, answers of 64 KiB and more)
        for edge in ("aaa", "constructor", "exact", "flood", "over"):
            self.assertEqual(hslib.status_entry(m("edge-" + edge)), ("command", f"/usr/lib/cockpit-edge-{edge}/status"))
        self.assertIsNone(hslib.status_entry(m("system")))
        self.assertEqual(hslib.menu_label(m("cronjobs")), "Cron Jobs")
        self.assertEqual(hslib.menu_label(m("hostile-shapes")), "Shapes")
        self.assertEqual(hslib.status_argv("/usr/lib/cockpit-x/y", "de"), ["/usr/lib/cockpit-x/y", "status", "--lang", "de"])
        self.assertEqual(hslib.status_argv("/usr/lib/cockpit-x/y", "fr")[-1], "en")
        with open(os.path.join(HERE, "..", "plugin", "manifest.json"), encoding="utf-8") as f:
            own = json.load(f)
        self.assertEqual(hslib.status_entry(own), ("command", "/usr/lib/cockpit-host-settings/host-settings.py"))


def ext(name):
    text = board("status/" + name)
    return hslib.validate_status(text, json.loads(text)["addon"])[1]


ts = hslib.parse_time


class OptionalParts(unittest.TestCase):
    """The board's tests "optional parts" (test/status.test.js): checks, findings, a host's profile."""

    def test_an_answer_without_them_has_none(self):
        st = ext("real-security.json")
        self.assertEqual([st["features"], st["checks"], st["profile"], st["ext_dropped"]], [[], None, None, 0])
        self.assertTrue(all(i["findings"] is None for i in st["items"]))
        self.assertEqual([hslib.item_state(i, NOW) for i in st["items"]], [i["state"] for i in st["items"]])

    def test_checks_and_findings_of_the_security_monitor(self):
        st = ext("ext-good-security.json")
        self.assertEqual(st["features"], ["checks", "findings"])
        self.assertEqual([[c["id"], c["state"], c["item"]] for c in st["checks"]],
                         [["exposure", "checked", "exposure"], ["containers", "partial", "containers"],
                          ["fail2ban", "notapplicable", None]])
        self.assertEqual([st["checks"][0]["checked_at"], st["checks"][0]["stale_after"], st["checks"][2]["reason"]],
                         [ts("2026-10-19T18:50:00Z"), 3600, "fail2ban is not installed"])
        exp = st["items"][0]["findings"]
        self.assertEqual([exp["complete"], exp["total"]], [True, 2])
        self.assertEqual([[f["fid"], f["state"], f["exposure"], f["fix"], f["exploited"], f["accepted_until"], f["link"]]
                          for f in exp["list"]],
                         [["port.5432-tcp", "danger", "internet", "available", None, None, "#/exposure/5432-tcp"],
                          ["port.6379-tcp", "danger", "internet", "available", None, None, "#/exposure/6379-tcp"]])
        self.assertEqual(exp["list"][1]["since"], ts("2026-10-17T07:30:00Z"))
        self.assertEqual(st["items"][1]["findings"], {"complete": False, "total": 0, "list": []},
                         "a list is complete only where its check is checked")
        self.assertIsNone(st["items"][2]["findings"])
        self.assertEqual(st["ext_dropped"], 0)

    def test_an_acceptance_that_runs_and_one_that_has_ended(self):
        st = ext("ext-good-update-gaps.json")
        self.assertEqual(st["items"][0]["findings"]["list"][0]["accepted_until"], ts("2026-10-30T00:00:00Z"))
        self.assertEqual([hslib.item_state(i, NOW) for i in st["items"]], ["ok", "warning"])
        self.assertEqual(hslib.item_state(st["items"][0], ts("2026-10-30T00:00:00Z")), "warning",
                         "on the day it ends the finding counts again")
        ev = hslib.evaluate(st, NOW)
        self.assertEqual([[i["id"], i["state"]] for i in ev["items"]], [["held", "ok"], ["restart", "warning"]])
        self.assertFalse(ev["by_age"])
        self.assertEqual([c["state"] for c in st["checks"]], ["checked", "checked", "unconfigured"])

    def test_a_hosts_profile_only_from_host_settings_and_only_when_declared(self):
        st = ext("ext-good-host-settings.json")
        self.assertEqual(st["profile"], {
            "roles": ["web"], "expect": ["host-settings", "update-gaps", "security", "cronjobs", "image-updates"],
            "skip": [{"check": "update-gaps/cve", "reason": "network data off on purpose", "until": None}],
            "set_at": ts("2026-10-19T09:00:00Z")})
        self.assertEqual(st["checks"][0]["item"], "run")
        doc = json.loads(board("status/ext-good-host-settings.json"))
        self.assertIsNone(hslib.validate_status(json.dumps(dict(doc, addon="security")), "security")[1]["profile"],
                          "from another add-on")
        self.assertIsNone(hslib.validate_status(json.dumps(dict(doc, features=["checks"])), "host-settings")[1]["profile"],
                          "without the feature")

    def test_only_what_features_declares_is_read(self):
        doc = json.loads(board("status/ext-good-security.json"))
        without = {k: v for k, v in doc.items() if k != "features"}
        for d in [without] + [dict(doc, features=f) for f in (None, "checks", {"checks": True}, [], ["Checks"])]:
            st = hslib.validate_status(json.dumps(d), "security")[1]
            self.assertEqual([st["features"], st["checks"], st["items"][0]["findings"], st["ext_dropped"]],
                             [[], None, None, 0], d.get("features"))
        checks = hslib.validate_status(json.dumps(dict(doc, features=["checks"])), "security")[1]
        self.assertEqual([len(checks["checks"]), checks["items"][0]["findings"]], [3, None])
        findings = hslib.validate_status(json.dumps(dict(doc, features=["findings"])), "security")[1]
        self.assertEqual([findings["checks"], len(findings["items"][0]["findings"]["list"]),
                          findings["items"][0]["findings"]["complete"]], [None, 2, False],
                         "without checks no list is complete")

    def test_shapes_the_board_cannot_use_are_left_out_and_counted(self):
        st = ext("ext-broken-shapes.json")
        self.assertEqual([st["checks"], st["profile"], st["ext_dropped"]], [None, None, 4])
        self.assertTrue(all(i["findings"] is None for i in st["items"]))

    def test_findings_that_break_the_rules_are_told_from_none_sent(self):
        # Host Settings only (listing): the board shows both as an item without findings, the
        # notifier keeps what it told about the findings of either (KeptFindings)
        self.assertEqual([i["listing"] for i in ext("ext-good-security.json")["items"]], ["listed", "listed", "none"])
        self.assertEqual([i["listing"] for i in ext("ext-broken-shapes.json")["items"]], ["broken"] * 3)
        self.assertEqual({i["listing"] for i in ext("real-security.json")["items"]}, {"none"}, "an add-on without the feature")
        doc = json.loads(board("status/ext-good-security.json"))
        without = {k: v for k, v in doc.items() if k != "features"}
        for d in [without] + [dict(doc, features=f) for f in (None, "checks,findings", ["checks"], {"findings": True})]:
            st = hslib.validate_status(json.dumps(d), "security")[1]
            self.assertEqual([(i["findings"], i["listing"]) for i in st["items"]],
                             [(None, "broken"), (None, "broken"), (None, "none")], d.get("features"))
        for broken in ("all", [], {"complete": "yes", "list": []}, {"complete": True, "list": {"a": 1}}, {"list": []}):
            d = json.loads(json.dumps(doc))
            d["items"][0]["findings"] = broken
            st = hslib.validate_status(json.dumps(d), "security")[1]
            self.assertEqual([st["items"][0]["findings"], st["items"][0]["listing"], st["items"][1]["listing"]],
                             [None, "broken", "listed"], broken)
        d = json.loads(json.dumps(doc))
        d["items"][0]["findings"]["list"] = ["unreadable", 7]
        st = hslib.validate_status(json.dumps(d), "security")[1]
        self.assertEqual([st["items"][0]["findings"], st["items"][0]["listing"]],
                         [{"complete": False, "total": 2, "list": []}, "listed"], "a list whose entries break the rules")

    def test_hostile_checks_and_findings(self):
        st = ext("ext-hostile.json")
        self.assertEqual(st["features"], ["checks", "findings", "profile"])
        self.assertEqual(len(st["checks"]), hslib.MAX_CHECKS)
        self.assertEqual([[c["id"], c["state"], c["item"], c["link"]] for c in st["checks"][:3]],
                         [["constructor", "checked", "constructor", None], ["exposure", "partial", "exposure", None],
                          ["w", "checked", None, None]])
        c, e = [i["findings"] for i in st["items"]]
        self.assertEqual([f["fid"] for f in c["list"]], ["constructor", "accept-far", "odd"])
        self.assertEqual([c["complete"], c["total"]], [False, 3], "a list with entries left out is not complete")
        self.assertIsNone(c["list"][1]["accepted_until"], "an acceptance more than 90 days after the check does not count")
        odd = c["list"][2]
        self.assertEqual([odd["title"], odd["exposure"], odd["fix"], odd["exploited"], odd["since"], odd["link"]],
                         ["Oddddo", None, None, None, None, None])
        self.assertEqual(len(c["list"]) + len(e["list"]), hslib.MAX_FINDINGS)
        self.assertEqual([e["complete"], e["total"]], [False, 120])
        self.assertIsNone(st["profile"], "a profile from another add-on than Host Settings is not read")
        # checks: 5 broken or twice, 3 past 50; findings: 4 broken or twice, 23 past 100
        self.assertEqual(st["ext_dropped"], 35)
        self.assertEqual(hslib.item_state(st["items"][0], NOW), "danger", "the acceptance ten years ahead hides nothing")

    def test_a_hostile_profile(self):
        st = ext("ext-hostile-profile.json")
        self.assertEqual(st["profile"]["roles"], ["web", "db", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8"])
        self.assertEqual(st["profile"]["expect"], ["security", "security/exposure", "constructor"])
        self.assertEqual([[s["check"], s["reason"], s["until"]] for s in st["profile"]["skip"]],
                         [["update-gaps/cve", "off", ts("2026-11-01T00:00:00Z")],
                          ["security/fail2ban", "<b>CrowdSec</b> instead", None]])
        self.assertIsNone(st["profile"]["set_at"])
        self.assertEqual(st["ext_dropped"], 12)

    def test_every_text_is_cleaned_and_cut(self):
        for name in ("ext-good-security.json", "ext-good-update-gaps.json", "ext-good-host-settings.json",
                     "ext-hostile.json", "ext-hostile-profile.json"):
            st = ext(name)
            texts = [x for c in (st["checks"] or []) for x in (c["title"], c["reason"])] + \
                [f["title"] for i in st["items"] if i["findings"] for f in i["findings"]["list"]] + \
                [s["reason"] for s in (st["profile"] or {}).get("skip", [])]
            for x in texts:
                if x is not None:
                    self.assertTrue(isinstance(x, str) and hslib.clean_text(x, 1000) == x and
                                    len(x) <= hslib.LIMITS["detail"], (name, x))


# The board's tests "complete" (test/status.test.js): the shared answers ext-complete-*.json, each
# the Security Monitor's check "exposure", checked and in time at NOW, and its item. A and B are the
# two findings ext-good-security.json lists completely, so each answer can follow that one. Only a
# complete list says that a finding missing from it is gone.
FID_A, FID_B = "port.5432-tcp", "port.6379-tcp"
COMPLETE = {
    # counts one, lists none: A and B stay as they were
    "ext-complete-total-1-empty.json": dict(complete=False, total=1, fids=[], dropped=0),
    # counts two, lists A: B stays as it was
    "ext-complete-total-2-one.json": dict(complete=False, total=2, fids=[FID_A], dropped=0),
    # counts none, lists none, its check checked and in time: A and B are gone
    "ext-complete-total-0-checked.json": dict(complete=True, total=0, fids=[], dropped=0),
    # no total: as before, the length of the list
    "ext-complete-no-total.json": dict(complete=True, total=2, fids=[FID_A, FID_B], dropped=0),
    # B breaks the rules, a duplicate of A, B past the limit of 100: never complete
    "ext-complete-invalid-entry.json": dict(complete=False, total=1, fids=[FID_A], dropped=1),
    "ext-complete-duplicate-fid.json": dict(complete=False, total=1, fids=[FID_A], dropped=1),
    "ext-complete-over-100.json": dict(complete=False, total=100, fids=None, dropped=1),
}


class Complete(unittest.TestCase):
    def raw(self, name):
        return json.loads(board("status/" + name))

    def findings_of(self, doc):
        return hslib.validate_status(json.dumps(doc), "security")[1]["items"][0]["findings"]

    def test_every_shared_fixture_has_its_expectation(self):
        self.assertEqual(sorted(f for f in os.listdir(os.path.join(BOARD, "status")) if f.startswith("ext-complete-")),
                         sorted(COMPLETE))

    def test_each_shared_fixture(self):
        for name, want in sorted(COMPLETE.items()):
            with self.subTest(name):
                st = ext(name)
                f = st["items"][0]["findings"]
                self.assertEqual([f["complete"], f["total"], st["ext_dropped"]], [want["complete"], want["total"], want["dropped"]])
                if want["fids"] is not None:
                    self.assertEqual([x["fid"] for x in f["list"]], want["fids"])
                self.assertEqual([[c["id"], c["state"], c["item"]] for c in st["checks"]], [["exposure", "checked", "exposure"]])
                self.assertEqual(hslib.checks_coverage(st, NOW)["coverage"], "checked", "the check is in time: only the list decides")
                self.assertIs(self.raw(name)["items"][0]["findings"]["complete"], True, "every one of them says complete")

    def test_past_the_limit_of_100_the_rest_is_left_out_b_with_it(self):
        f = ext("ext-complete-over-100.json")["items"][0]["findings"]
        self.assertEqual([len(f["list"]), f["list"][0]["fid"], any(x["fid"] == FID_B for x in f["list"])],
                         [hslib.MAX_FINDINGS, FID_A, False])
        sent = self.raw("ext-complete-over-100.json")["items"][0]["findings"]["list"]
        self.assertEqual([len(sent), sent[-1]["fid"]], [hslib.MAX_FINDINGS + 1, FID_B])

    def test_each_fixture_is_complete_without_the_one_thing_it_tests(self):
        def fix(name, change):
            doc = self.raw(name)
            change(doc["items"][0]["findings"], doc)
            return self.findings_of(doc)
        self.assertEqual(fix("ext-complete-total-1-empty.json", lambda f, d: f.pop("total")),
                         {"complete": True, "total": 0, "list": []})
        self.assertTrue(fix("ext-complete-total-2-one.json", lambda f, d: f.update(total=1))["complete"])
        for name in ("ext-complete-invalid-entry.json", "ext-complete-duplicate-fid.json", "ext-complete-over-100.json"):
            self.assertTrue(fix(name, lambda f, d: f["list"].pop())["complete"], name)
        self.assertTrue(fix("ext-complete-no-total.json", lambda f, d: f.update(total=2))["complete"], "a total that agrees")
        # and the rules that were there before still hold
        self.assertFalse(fix("ext-complete-total-0-checked.json", lambda f, d: d["checks"][0].update(state="partial"))["complete"],
                         "a check done in part")
        self.assertFalse(fix("ext-complete-total-0-checked.json", lambda f, d: d["checks"][0].update(item="elsewhere"))["complete"],
                         "no check that owns the item")
        self.assertFalse(fix("ext-complete-total-0-checked.json", lambda f, d: f.update(complete=False))["complete"],
                         "the add-on does not say so")

    def test_a_whole_number_total_past_the_findings_taken_over_whatever_its_size(self):
        def with_total(total):
            doc = self.raw("ext-complete-no-total.json")
            doc["items"][0]["findings"]["total"] = total
            f = self.findings_of(doc)
            return [f["complete"], f["total"], len(f["list"])]
        self.assertEqual(with_total(3), [False, 3, 2])
        self.assertEqual(with_total(hslib.MAX_TOTAL), [False, hslib.MAX_TOTAL, 2])
        self.assertEqual(with_total(hslib.MAX_TOTAL + 1), [False, 2, 2],
                         "past the limit the total is not kept, and the list still is not complete")
        # a total that is no whole number is left out, as before: the length of the list
        for odd in ("3", -1, 2.5, None, True, [3], {"n": 3}):
            self.assertEqual(with_total(odd), [True, 2, 2], odd)
        self.assertEqual(with_total(1), [True, 2, 2], "fewer than listed: the length of the list")
        self.assertEqual(with_total(0), [True, 2, 2])
        # JSON has no integers of its own: 3.0 is the whole number 3 (JavaScript reads it as 3)
        text = board("status/ext-complete-no-total.json").replace('"complete": true,', '"complete": true, "total": 3.0,')
        self.assertIn('"total": 3.0,', text)
        f = hslib.validate_status(text, "security")[1]["items"][0]["findings"]
        self.assertEqual([f["complete"], f["total"]], [False, 3])

    def test_through_the_notifier(self):
        # A and B open after ext-good-security.json, then each shared answer: only a complete list
        # resolves what it leaves out
        def read(name, now):
            return hsnotify.read_addon("security", None, ("command", SEC), "en", now, Runner({SEC: ok(board("status/" + name))}),
                                       lstat_of({SEC: (0o755, 0)}))
        for name, want in sorted(COMPLETE.items()):
            with self.subTest(name):
                events, sent = hsnotify.changes({}, {"security": read("ext-good-security.json", NOW)}, NOW)
                self.assertEqual([(e["fid"], e["kind"]) for e in events], [(FID_A, "worse"), (FID_B, "worse")])
                events, sent = hsnotify.changes(sent, {"security": read(name, NOW + 60)}, NOW + 60)
                gone = sorted({FID_A, FID_B} - set(want["fids"])) if want["complete"] else []
                self.assertEqual(sorted(e["fid"] for e in events if e["kind"] == "resolved"), gone)
                self.assertEqual([e for e in events if e["kind"] != "resolved"], [])
                for fid in (FID_A, FID_B):
                    self.assertEqual(sent["security#exposure/" + fid]["status"], "resolved" if fid in gone else "open", fid)


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

    def test_left_out_items_are_counted(self):
        items = [{"id": f"i{n}", "state": "ok", "title": f"I{n}"} for n in range(52)]
        a, _ = self.read(ok(status_doc("cronjobs", items)))
        self.assertEqual((len(a["items"]), a["dropped"]), (50, 2))
        a, _ = self.read(ok(status_doc("cronjobs", items[:3])))
        self.assertEqual(a["dropped"], 0)

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

def addon(label, items, kind="ok", reason=None, dropped=0):
    return {"label": label, "kind": kind, "reason": reason, "code": None, "state": "none", "version": None,
            "summary": None, "items": [dict({"value": None, "detail": None, "link": None, "stale_at": None, "title": i["id"].upper()}, **i)
                                       for i in items], "dropped": dropped}


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
        self.assertEqual(sent["img#updates"]["level"], "warning")
        events, sent = hsnotify.changes(sent, {}, NOW + 120)
        self.assertEqual([(e["key"], e["kind"], e["label"], e["open"]) for e in events],
                         [("img#", "unchecked", "Image Update Monitor", 1)])
        self.assertEqual(sent, {}, "its keys are forgotten")
        events, _ = hsnotify.changes(sent, {}, NOW + 180)
        self.assertEqual(events, [], "told once")

    def test_an_item_that_cannot_be_judged_keeps_its_level(self):
        # Security Monitor sets `exposure` to none when it cannot read the packet filter.
        snap = {"sec": addon("Security Monitor", [{"id": "exposure", "state": "danger"}])}
        _, sent = hsnotify.changes({}, snap, NOW)
        snap["sec"]["items"][0]["state"] = "none"
        events, sent = hsnotify.changes(sent, snap, NOW + 300)
        self.assertEqual(events, [], "not judged is no reason for OK")
        self.assertEqual(sent["sec#exposure"]["level"], "danger")
        snap["sec"]["items"][0]["state"] = "danger"
        events, sent = hsnotify.changes(sent, snap, NOW + 600)
        self.assertEqual(events, [], "it never was OK, so nothing is new")
        snap["sec"]["items"][0]["state"] = "none"
        _, sent = hsnotify.changes(sent, snap, NOW + 900)
        snap["sec"]["items"][0]["state"] = "ok"
        events, sent = hsnotify.changes(sent, snap, NOW + 1200)
        self.assertEqual([(e["key"], e["kind"], e["from"]) for e in events], [("sec#exposure", "resolved", "danger")])
        # never told, then not judged: nothing at all
        events, sent = hsnotify.changes({}, {"sec": addon("Security Monitor", [{"id": "logins", "state": "none"}])}, NOW)
        self.assertEqual((events, sent), ([], {}))

    def test_exit_77_does_not_make_an_unreadable_addon_readable(self):
        _, sent = hsnotify.changes({}, {"img": addon("Image Update Monitor", [], kind="failed")}, NOW)
        self.assertEqual(sent["img#"]["level"], "warning")
        events, sent = hsnotify.changes(sent, {"img": addon("Image Update Monitor", [], kind="noaccess")}, NOW + 300)
        self.assertEqual(events, [])
        self.assertEqual(sent["img#"]["level"], "warning")

    def test_items_left_out_of_an_answer_are_not_resolved(self):
        items = [{"id": "held", "state": "warning"}, {"id": "restart", "state": "warning"}]
        _, sent = hsnotify.changes({}, {"gaps": addon("Update Gaps", items)}, NOW)
        events, sent = hsnotify.changes(sent, {"gaps": addon("Update Gaps", items[:1], dropped=1)}, NOW + 300)
        self.assertEqual(events, [], "the answer left items out: the missing one may be among them")
        self.assertEqual(sent["gaps#restart"]["level"], "warning")
        events, _ = hsnotify.changes(sent, {"gaps": addon("Update Gaps", items[:1])}, NOW + 600)
        self.assertEqual([(e["key"], e["kind"], e["why"]) for e in events], [("gaps#restart", "resolved", "gone")])

    def test_an_uninstalled_addon_is_no_longer_checked(self):
        snap = {"sec": addon("Security Monitor", [{"id": "exposure", "state": "danger"}, {"id": "ssh", "state": "warning"},
                                                   {"id": "logins", "state": "ok"}]),
                "img": addon("Image Update Monitor", [], kind="timeout")}
        _, sent = hsnotify.changes({}, snap, NOW)
        events, sent = hsnotify.changes(sent, {}, NOW + 300)
        self.assertEqual([(e["key"], e["kind"], e["from"], e["to"], e["open"]) for e in events],
                         [("img#", "unchecked", "warning", "warning", 0), ("sec#", "unchecked", "danger", "warning", 2)])
        self.assertEqual(sent, {})
        one = hsnotify.message("en", "web1", events=events[1:])
        self.assertEqual(one["title"], "web1: Security Monitor, no longer checked")
        self.assertEqual(one["body"], "No longer checked · Security Monitor · the add-on is no longer installed\n"
                                      "the 2 findings it reported can no longer be checked\n\nHost Settings on web1")
        self.assertEqual((one["priority"], one["tags"]), (3, ["warning"]))
        de = hsnotify.message("de", "web1", events=events)
        self.assertEqual(de["body"].split("\n\n")[:2], [
            "Nicht mehr geprüft · Image Update Monitor · das Add-on ist nicht mehr installiert",
            "Nicht mehr geprüft · Security Monitor · das Add-on ist nicht mehr installiert\n"
            "die 2 gemeldeten Befunde lassen sich nicht mehr prüfen"])
        single = hsnotify._unchecked("x", [("a", {"level": "warning", "label": "X"})])
        self.assertEqual(hsnotify._line("en", single).splitlines()[1], "the finding it reported can no longer be checked")
        # an add-on that told nothing goes without a word, as before
        _, sent = hsnotify.changes({}, {"c": addon("C", [{"id": "x", "state": "ok"}])}, NOW)
        events, _ = hsnotify.changes(sent, {}, NOW + 300)
        self.assertEqual(events, [])

    def test_baseline(self):
        snap = {"a": addon("A", [{"id": "x", "state": "warning"}, {"id": "y", "state": "ok"}]),
                "b": addon("B", [], kind="timeout")}
        base = hsnotify.baseline(snap, NOW)
        self.assertEqual(sorted(base), ["a#x", "b#"])
        events, _ = hsnotify.changes(base, snap, NOW + 300)
        self.assertEqual(events, [])

    def test_events_are_ordered(self):
        snap = {"b": addon("B", [{"id": "w", "state": "warning"}]), "a": addon("A", [{"id": "d", "state": "danger"}])}
        events, _ = hsnotify.changes({"z#gone": {"level": "warning", "at": NOW - 9999, "last": {}, "label": "Z"},
                                      "b#old": {"level": "warning", "at": NOW - 9999, "last": {}, "label": "B"}}, snap, NOW)
        self.assertEqual([(e["key"], e["kind"]) for e in events],
                         [("a#d", "worse"), ("b#w", "worse"), ("z#", "unchecked"), ("b#old", "resolved")])


# ---------------------------------------------------------------------------
# Findings: sequences of answers of a Security Monitor that lists the ports of its item
# `exposure`, read through read_addon() as the notifier reads them

SEC = "/usr/lib/cockpit-security/security-status"
SERVICES = {5432: "postgres", 6379: "redis", 9200: "elasticsearch", 3306: "mysql"}


def port(n, state="danger", lang="en", **kw):
    name = SERVICES.get(n, "service")
    return dict({"fid": f"port.{n}-tcp", "state": state, "exposure": "internet",
                 "title": f"{n}/tcp {name}" if lang == "en" else f"{n}/tcp {name}, offen"}, **kw)


def fkey(n):
    return f"security#exposure/port.{n}-tcp"


def sec_doc(findings, at, lang="en", check="checked", check_at=None, complete=True, state=None, items=(), total=None):
    """An answer with the item `exposure` and its findings, a check of it, and more items."""
    worst = state or hslib.STATES[max([hslib.rank(f["state"]) for f in findings if isinstance(f, dict) and
                                       hslib.is_state(f.get("state"))] + [1])]
    exposure = {"id": "exposure", "state": worst, "value": str(len(findings)), "checked_at": at, "stale_after": 3600,
                "title": "Reachable from the internet" if lang == "en" else "Aus dem Internet erreichbar",
                "findings": {"complete": complete, "total": len(findings) if total is None else total,
                             "list": findings}}
    every = [exposure] + [dict({"checked_at": at, "stale_after": 3600}, **i) for i in items]
    return json.dumps({"schema": 1, "addon": "security", "label": "Security Monitor", "version": "0.4.0",
                       "checked_at": at, "summary": "s", "features": ["checks", "findings"],
                       "state": hslib.STATES[max(hslib.rank(i["state"]) if hslib.is_state(i.get("state")) else 0
                                                 for i in every)],
                       "checks": [{"id": "exposure", "state": check, "title": "Exposure", "item": "exposure",
                                   "checked_at": check_at or at, "stale_after": 3600}],
                       "items": every})


def read_sec(result, now):
    return hsnotify.read_addon("security", "Security Monitor", ("command", SEC), "en", now, Runner({SEC: result}),
                               lstat_of({SEC: (0o755, 0)}))


def sec_broken(how, findings=None, check="partial", state="ok"):
    """An answer of the Security Monitor (a function of its time, for Timeline) whose item
    `exposure` sends findings that break the rules, or none: "list" (a text instead of a list),
    "object" (a list instead of the object), "complete" (no boolean), "undeclared" (features
    without findings), "features" (features no list), "lost" (findings declared, the item sends
    none), "older" (an older version: no features, no checks, no findings)."""
    def answer(at):
        doc = json.loads(sec_doc(findings or [port(5432)], at, check=check, state=state))
        exposure = doc["items"][0]
        if how == "list":
            exposure["findings"]["list"] = "unreadable"
        elif how == "object":
            exposure["findings"] = ["port.5432-tcp"]
        elif how == "complete":
            exposure["findings"]["complete"] = "yes"
        elif how == "undeclared":
            doc["features"] = ["checks"]
        elif how == "features":
            doc["features"] = "checks,findings"
        elif how == "lost":
            del exposure["findings"]
        elif how == "older":
            for k in ("features", "checks"):
                del doc[k]
            del exposure["findings"]
        return ok(json.dumps(doc))
    return answer


class Timeline:
    """changes() run after run, the memory carried from one to the next: each step is an answer
    of the Security Monitor (or another result of its status verb) some seconds later."""

    def __init__(self, sent=None, now=NOW):
        self.sent, self.now = dict(sent or {}), now

    def copy(self):
        return Timeline(self.sent, self.now)

    def snapshot(self, findings=None, result=None, age=60, also=None, **kw):
        if callable(result):
            result = result(hslib.iso(self.now - age))
        result = result or ok(sec_doc(findings, hslib.iso(self.now - age), **kw))
        return dict({"security": read_sec(result, self.now)}, **(also or {}))

    def step(self, findings=None, after=300, **kw):
        self.now += after
        events, self.sent = hsnotify.changes(self.sent, self.snapshot(findings, **kw), self.now)
        return events


def told(events):
    return [(e["key"], e["kind"], e["from"], e["to"]) for e in events]


class Findings(unittest.TestCase):
    def test_a_second_dangerous_port_is_told_while_the_item_is_already_danger(self):
        tl = Timeline()
        self.assertEqual(told(tl.step([port(5432)])), [(fkey(5432), "worse", "ok", "danger")])
        events = tl.step([port(5432), port(6379)])
        self.assertEqual(told(events), [(fkey(6379), "worse", "ok", "danger")])
        msg = hsnotify.message("en", "web1", events=events)
        self.assertEqual(msg["title"], "web1: Security Monitor, 6379/tcp redis")
        self.assertEqual(msg["body"], "Danger · Security Monitor · 6379/tcp redis\nReachable from the internet\n\n"
                                      "Host Settings on web1")
        self.assertEqual((msg["priority"], msg["tags"]), (4, ["rotating_light"]))
        self.assertEqual(hsnotify.message("de", "web1", events=events)["body"].splitlines()[0],
                         "Gefahr · Security Monitor · 6379/tcp redis")
        self.assertEqual((tl.sent[fkey(6379)]["status"], tl.sent[fkey(6379)]["level"]), ("open", "danger"))
        self.assertNotIn("security#exposure", tl.sent, "the item told nothing of its own: its findings explain it")
        # the same answer again, in German, in another order: nothing new
        self.assertEqual(tl.step([port(5432), port(6379)]), [])
        self.assertEqual(tl.step([port(5432, lang="de"), port(6379, lang="de")], lang="de"), [])
        self.assertEqual(tl.step([port(6379), port(5432)]), [])

    def test_a_list_that_cannot_say_a_finding_is_gone_resolves_nothing(self):
        base = Timeline()
        base.step([port(5432), port(6379)])
        cases = {
            "the check ran in part": dict(findings=[port(5432)], check="partial"),
            "the check failed": dict(findings=[port(5432)], check="failed"),
            "the add-on says the list is not complete": dict(findings=[port(5432)], complete=False),
            "a finding was left out (broken)": dict(findings=[port(5432), {"fid": "port.6379-tcp", "state": "danger"}]),
            "the answer left items out": dict(findings=[port(5432)], items=[{"id": "Not An Id", "state": "ok", "title": "X"}]),
            "the item is out of date": dict(findings=[port(5432)], age=7200),
            "the check is out of date": dict(findings=[port(5432)], check_at=hslib.iso(NOW - 7200)),
            "the status is unreadable": dict(result=ok("{")),
            "exit 77": dict(result=hsproc.Result(77)),
            "the item cannot be judged": dict(findings=[], state="none", complete=False),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                tl = base.copy()
                events = tl.step(**kw)
                self.assertEqual([e for e in events if e["kind"] == "resolved"], [])
                self.assertEqual((tl.sent[fkey(6379)]["status"], tl.sent[fkey(6379)]["level"]), ("open", "danger"))
        # exit 77 says nothing at all; an unreadable status only that it is unreadable
        self.assertEqual(base.copy().step(result=hsproc.Result(77)), [])
        self.assertEqual(told(base.copy().step(result=ok("{"))), [("security#", "worse", "ok", "warning")])

    def test_a_complete_list_without_the_finding_resolves_it_and_its_return_is_told_again(self):
        tl = Timeline()
        tl.step([port(5432), port(6379)])
        events = tl.step([port(5432)], after=hsnotify.FLAP)
        self.assertEqual([(e["key"], e["kind"], e["from"], e["to"], e["why"]) for e in events],
                         [(fkey(6379), "resolved", "danger", "ok", "fixed")])
        msg = hsnotify.message("en", "h", events=events)
        self.assertEqual(msg["body"].split("\n\n")[0], "OK again · Security Monitor · 6379/tcp redis\n"
                                                        "Reachable from the internet: no longer found by a complete check")
        self.assertEqual((msg["priority"], msg["tags"]), (2, ["white_check_mark"]))
        self.assertEqual(hsnotify.message("de", "h", events=events)["body"].split("\n\n")[0],
                         "Wieder in Ordnung · Security Monitor · 6379/tcp redis\n"
                         "Reachable from the internet: bei einer vollständigen Prüfung nicht mehr gefunden")
        self.assertEqual(tl.sent[fkey(6379)]["status"], "resolved")
        self.assertEqual(tl.step([port(5432)]), [])
        # it comes back
        events = tl.step([port(5432), port(6379)], after=hsnotify.FLAP)
        self.assertEqual(told(events), [(fkey(6379), "again", "ok", "danger")])
        self.assertEqual(hsnotify.message("en", "h", events=events)["body"].split("\n\n")[0],
                         "Danger, again · Security Monitor · 6379/tcp redis\nReachable from the internet")
        self.assertEqual(hsnotify.message("de", "h", events=events)["body"].splitlines()[0],
                         "Gefahr, wieder aufgetreten · Security Monitor · 6379/tcp redis")
        self.assertEqual(tl.step([port(5432), port(6379)]), [])
        # a resolved finding is remembered for 30 days; after that its return is a new finding
        tl.step([port(5432)], after=hsnotify.FLAP)
        self.assertEqual(tl.step([port(5432)], after=hsnotify.KEEP - 60), [])
        self.assertIn(fkey(6379), tl.sent)
        self.assertEqual(tl.step([port(5432)], after=60), [])
        self.assertNotIn(fkey(6379), tl.sent)
        self.assertEqual(told(tl.step([port(5432), port(6379)])), [(fkey(6379), "worse", "ok", "danger")])

    def test_a_flapping_finding_is_told_once_an_hour(self):
        tl = Timeline()
        tl.step([port(5432)])
        self.assertEqual([e["kind"] for e in tl.step([])], ["resolved"])
        self.assertEqual(tl.step([port(5432)]), [], "the same change within the hour waits")
        self.assertEqual(tl.sent[fkey(5432)]["status"], "resolved")
        self.assertEqual([e["kind"] for e in tl.step([port(5432)], after=hsnotify.FLAP)], ["again"],
                         "told after the hour, as it still holds")

    def test_danger_to_warning_and_back(self):
        tl = Timeline()
        tl.step([port(5432)])
        events = tl.step([port(5432, "warning")])
        self.assertEqual(told(events), [(fkey(5432), "better", "danger", "warning")])
        self.assertEqual(hsnotify.message("en", "h", events=events)["body"].splitlines()[0],
                         "Warning instead of Danger · Security Monitor · 5432/tcp postgres")
        self.assertEqual(told(tl.step([port(5432)])), [(fkey(5432), "worse", "warning", "danger")])
        # a finding rated OK by the add-on (a port marked as expected) is OK again
        self.assertEqual(told(tl.step([port(5432, "ok")])), [(fkey(5432), "resolved", "danger", "ok")])
        # one it cannot judge keeps what was told
        tl.step([port(5432)], after=hsnotify.FLAP)
        self.assertEqual(tl.step([port(5432, "none")]), [])
        self.assertEqual(tl.sent[fkey(5432)]["level"], "danger")

    def test_accepted_findings_are_silent_until_the_acceptance_ends(self):
        tl = Timeline()
        tl.step([port(5432), port(6379)])
        until = hslib.iso(tl.now + 7 * 86400)
        self.assertEqual(tl.step([port(5432), port(6379, accepted_until=until)]), [])
        self.assertEqual(tl.sent[fkey(6379)]["status"], "accepted")
        self.assertEqual(tl.step([port(5432), port(6379, accepted_until=until)], after=86400), [])
        # the acceptance ends while the finding is still there: told once
        events = tl.step([port(5432), port(6379, accepted_until=until)], after=6 * 86400)
        self.assertEqual(told(events), [(fkey(6379), "expired", "danger", "danger")])
        self.assertEqual(hsnotify.message("en", "h", events=events)["body"].split("\n\n")[0],
                         "Danger, acceptance ended · Security Monitor · 6379/tcp redis\nReachable from the internet")
        self.assertEqual(hsnotify.message("de", "h", events=events)["body"].splitlines()[0],
                         "Gefahr, Akzeptanz abgelaufen · Security Monitor · 6379/tcp redis")
        self.assertEqual(tl.step([port(5432), port(6379, accepted_until=until)]), [])
        self.assertEqual(tl.sent[fkey(6379)]["status"], "open")

    def test_an_acceptance_the_item_leaves_out_of_its_state(self):
        # As Update Gaps does: the item says ok while its only finding is accepted.
        tl = Timeline()
        until = hslib.iso(NOW + 86400)
        self.assertEqual(tl.step([port(3306, "warning", accepted_until=until)], state="ok"), [],
                         "seen accepted the first time: nothing")
        self.assertEqual(tl.sent, {fkey(3306): tl.sent[fkey(3306)]})
        self.assertEqual(tl.sent[fkey(3306)]["status"], "accepted")
        # complete without it: gone, without a word (it was accepted)
        self.assertEqual(tl.step([], after=3600), [])
        self.assertEqual(tl.sent[fkey(3306)]["status"], "resolved")
        # ended while still there (the item still says ok): one message, for the finding only
        tl = Timeline()
        tl.step([port(3306, "warning", accepted_until=until)], state="ok")
        events = tl.step([port(3306, "warning", accepted_until=until)], after=86400, state="ok")
        self.assertEqual(told(events), [(fkey(3306), "expired", "warning", "warning")])

    def test_thirty_new_findings_come_as_one_message(self):
        tl = Timeline()
        tl.step([port(5432)])
        events = tl.step([port(5432)] + [port(n) for n in range(10000, 10030)])
        self.assertEqual(len(events), 30)
        self.assertEqual({e["kind"] for e in events}, {"worse"})
        msg = hsnotify.message("en", "h", events=events)
        self.assertEqual(msg["title"], "h: 30 changes")
        self.assertLessEqual(len(msg["body"].encode("utf-8")), hsnotify.BODY_MAX)
        self.assertEqual(msg["body"].count("Danger · Security Monitor · "), hsnotify.MAX_LINES)
        self.assertTrue(msg["body"].endswith("and 10 more\n\nHost Settings on h"), msg["body"][-80:])
        self.assertEqual(tl.step([port(5432)] + [port(n) for n in range(10000, 10030)]), [])

    def test_an_item_without_findings_next_to_one_with_findings(self):
        def ssh(state):
            return [{"id": "ssh", "state": state, "title": "SSH logins"}]
        tl = Timeline()
        self.assertEqual(told(tl.step([port(5432)], items=ssh("ok"))), [(fkey(5432), "worse", "ok", "danger")])
        events = tl.step([port(5432), port(6379)], items=ssh("warning"))
        self.assertEqual(told(events), [(fkey(6379), "worse", "ok", "danger"), ("security#ssh", "worse", "ok", "warning")])
        self.assertEqual(hsnotify.message("en", "h", events=events)["body"].split("\n\n")[:2],
                         ["Danger · Security Monitor · 6379/tcp redis\nReachable from the internet",
                          "Warning · Security Monitor · SSH logins"])
        self.assertEqual(tl.step([port(5432), port(6379)], items=ssh("none")), [], "state none keeps the level")
        self.assertEqual(tl.sent["security#ssh"]["level"], "warning")
        self.assertEqual(told(tl.step([port(5432), port(6379)], items=ssh("ok"))), [("security#ssh", "resolved", "warning", "ok")])

    def test_an_item_level_its_findings_do_not_explain(self):
        # danger with no finding listed (the list says it is not complete): the item itself is told
        tl = Timeline()
        events = tl.step([], state="danger", complete=False)
        self.assertEqual(told(events), [("security#exposure", "worse", "ok", "danger")])
        self.assertEqual(hsnotify._line("en", events[0]), "Danger · Security Monitor · Reachable from the internet (0)")
        self.assertTrue(tl.sent["security#exposure"]["findings"])
        # the list names it later: a new finding, told; the item's level is explained by it now
        self.assertEqual(told(tl.step([port(5432)])), [(fkey(5432), "worse", "ok", "danger")])
        self.assertNotEqual(tl.sent.get("security#exposure", {}).get("level"), "danger")
        # resolved: the finding, not the item
        self.assertEqual(told(tl.step([], after=hsnotify.FLAP)), [(fkey(5432), "resolved", "danger", "ok")])

    def test_old_last_json_keys_keep_working(self):
        # What 0.1.0 wrote: per add-on and item. The Security Monitor lists its ports from now on.
        old = {"security#exposure": {"level": "danger", "at": NOW - 86400, "last": {"ok>danger": NOW - 86400},
                                     "label": "Security Monitor", "title": "Reachable from the internet"},
               "security#ssh": {"level": "warning", "at": NOW - 86400, "last": {}, "label": "Security Monitor",
                                "title": "SSH logins"},
               "cronjobs#failed": {"level": "warning", "at": NOW - 86400, "last": {"ok>warning": NOW - 86400},
                                   "label": "Cron Jobs", "title": "Failed runs"}}
        cron = {"cronjobs": addon("Cron Jobs", [{"id": "failed", "state": "warning", "title": "Failed runs"}])}
        ssh = [{"id": "ssh", "state": "warning", "title": "SSH logins"}]
        tl = Timeline(old)
        self.assertEqual(tl.step([port(5432), port(6379)], items=ssh, also=cron), [],
                         "the ports the item already told as danger are taken as told")
        self.assertEqual({k: (e.get("status"), e["level"]) for k, e in tl.sent.items()},
                         {fkey(5432): ("open", "danger"), fkey(6379): ("open", "danger"),
                          "security#ssh": (None, "warning"), "cronjobs#failed": (None, "warning")})
        # from now on finding by finding, and per item where there are no findings
        self.assertEqual(told(tl.step([port(5432), port(6379), port(9200)], items=ssh, also=cron)),
                         [(fkey(9200), "worse", "ok", "danger")])
        cron["cronjobs"]["items"][0]["state"] = "ok"
        self.assertEqual(told(tl.step([port(5432), port(6379), port(9200)], items=ssh, also=cron)),
                         [("cronjobs#failed", "resolved", "warning", "ok")])
        # an item told as a warning: a finding more severe than that is told
        tl = Timeline({"security#exposure": dict(old["security#exposure"], level="warning")})
        self.assertEqual(told(tl.step([port(3306, "warning"), port(5432)])), [(fkey(5432), "worse", "ok", "danger")])

    def test_an_older_version_without_findings_keeps_the_known_ones(self):
        # Back to a version of the add-on that sends no findings (no features, no checks), or one
        # whose item stops listing them: what it says of the item proves nothing about them.
        until = hslib.iso(NOW + 30 * 86400)
        for how in ("older", "lost"):
            with self.subTest(how):
                tl = Timeline()
                tl.step([port(5432), port(6379), port(3306, "warning", accepted_until=until)])
                self.assertEqual(tl.step(result=sec_broken(how, state="danger")), [], "danger as its findings are")
                self.assertEqual(tl.step(result=sec_broken(how, state="ok"), after=hsnotify.FLAP), [],
                                 "OK from the item alone resolves nothing, and says nothing")
                self.assertEqual({n: tl.sent[fkey(n)]["status"] for n in (5432, 6379, 3306)},
                                 {5432: "open", 6379: "open", 3306: "accepted"})
                self.assertEqual(tl.step(result=sec_broken(how, state="danger"), after=hsnotify.FLAP), [],
                                 "danger again: the open findings explain it, nothing is new")
                self.assertEqual(tl.step(result=sec_broken(how, state="ok"), after=86400), [])
                # a version that lists findings again: what a complete list leaves out is resolved
                # now, and told (an accepted one without a word)
                events = tl.step([port(5432)], after=hsnotify.FLAP)
                self.assertEqual([(e["key"], e["kind"], e["why"]) for e in events], [(fkey(6379), "resolved", "fixed")])
                self.assertEqual({n: tl.sent[fkey(n)]["status"] for n in (5432, 6379, 3306)},
                                 {5432: "open", 6379: "resolved", 3306: "resolved"})
        # what the known findings do not explain is told as the item
        tl = Timeline()
        tl.step([port(3306, "warning")])
        self.assertEqual(told(tl.step(result=sec_broken("older", state="danger"))),
                         [("security#exposure", "worse", "ok", "danger")])
        self.assertEqual(tl.sent[fkey(3306)]["status"], "open")

    def test_the_first_summary_lists_open_findings_instead_of_their_item(self):
        until = hslib.iso(NOW + 86400)
        snap = Timeline().snapshot([port(5432), port(6379, accepted_until=until)])
        msg = hsnotify.message("en", "h", snapshot=snap, summary=True)
        self.assertEqual(msg["body"].split("\n\n"), [
            "Messages about the Azentrius add-ons of this host now come this way.", "Right now:",
            "Danger · Security Monitor · 5432/tcp postgres\nReachable from the internet", "Host Settings on h"])
        self.assertEqual(len(hsnotify.problems_of(snap)), 1)
        base = hsnotify.baseline(snap, NOW)
        self.assertEqual({k: (e.get("status"), e["level"]) for k, e in base.items()},
                         {fkey(5432): ("open", "danger"), fkey(6379): ("accepted", "danger")})
        events, _ = hsnotify.changes(base, snap, NOW + 300)
        self.assertEqual(events, [])

    def test_a_removed_addon_takes_its_findings_along(self):
        tl = Timeline()
        until = hslib.iso(NOW + 86400)
        tl.step([port(5432), port(6379), port(3306, "warning", accepted_until=until)])
        events, sent = hsnotify.changes(tl.sent, {}, tl.now + 300)
        self.assertEqual([(e["key"], e["kind"], e["from"], e["open"]) for e in events], [("security#", "unchecked", "danger", 2)])
        self.assertEqual(sent, {})

    def test_the_boards_fixtures_through_the_notifier(self):
        gaps = hsnotify.read_addon("update-gaps", None, ("command", SEC), "en", NOW,
                                   Runner({SEC: ok(board("status/ext-good-update-gaps.json"))}), lstat_of({SEC: (0o755, 0)}))
        events, sent = hsnotify.changes({}, {"update-gaps": gaps}, NOW)
        self.assertEqual(told(events), [("update-gaps#restart/restart.kernel", "worse", "ok", "warning")],
                         "the held package is accepted, the restart's acceptance has ended")
        self.assertEqual(sent["update-gaps#held/held.postgresql-16"]["status"], "accepted")
        sec = hsnotify.read_addon("security", None, ("command", SEC), "en", NOW,
                                  Runner({SEC: ok(board("status/ext-good-security.json"))}), lstat_of({SEC: (0o755, 0)}))
        self.assertEqual([i["findings"]["complete"] if i["findings"] else None for i in sec["items"]], [True, False, None])
        events, _ = hsnotify.changes({}, {"security": sec}, NOW)
        self.assertEqual([e["key"] for e in events], [fkey(5432), fkey(6379)])

    def test_the_memory_is_bounded(self):
        sent = {f"security#exposure/port.{n}-tcp": {"level": "ok", "status": "resolved", "at": NOW - n, "last": {},
                                                    "label": "Security Monitor", "title": str(n)} for n in range(1, 2101)}
        sent["cronjobs#failed"] = {"level": "warning", "at": NOW - 99999, "last": {}, "label": "Cron Jobs"}
        snap = {"cronjobs": addon("Cron Jobs", [{"id": "failed", "state": "warning"}]),
                "security": addon("Security Monitor", [{"id": "exposure", "state": "ok",
                                                        "findings": {"complete": True, "total": 0, "list": []}}])}
        events, out = hsnotify.changes(sent, snap, NOW)
        self.assertEqual(events, [])
        self.assertEqual(len(out), hsnotify.MAX_KEYS)
        self.assertIn("cronjobs#failed", out, "what was told as a problem stays")
        self.assertIn("security#exposure/port.1-tcp", out, "the newest resolved ones stay")
        self.assertNotIn("security#exposure/port.2100-tcp", out)


def sock(name, state="danger"):
    return {"fid": f"socket.{name}", "state": state, "title": f"{name} has the Docker socket"}


class KeptFindings(unittest.TestCase):
    """Findings that break the rules, or that an answer no longer sends, are no proof that a
    finding is gone: sequences through read_addon() and hslib's rules, the clock set by Timeline."""

    def open_two(self):
        tl = Timeline()
        self.assertEqual(told(tl.step([port(5432), port(6379)])),
                         [(fkey(5432), "worse", "ok", "danger"), (fkey(6379), "worse", "ok", "danger")])
        return tl

    def assertOpen(self, tl, *ports):
        for n in ports:
            self.assertEqual((tl.sent[fkey(n)]["status"], tl.sent[fkey(n)]["level"]), ("open", "danger"), n)

    def test_the_reviews_sequence(self):
        # 1. a valid answer with port 5432 open at danger
        tl = Timeline()
        self.assertEqual(told(tl.step([port(5432)])), [(fkey(5432), "worse", "ok", "danger")])
        # 2. the item ok, its check partial, findings declared, but the list is a text
        snap = tl.snapshot(result=sec_broken("list", check="partial", state="ok"))
        self.assertEqual([(i["id"], i["state"], i["findings"], i["listing"]) for i in snap["security"]["items"]],
                         [("exposure", "ok", None, "broken")])
        self.assertEqual(tl.step(result=sec_broken("list", check="partial", state="ok")), [],
                         "no aggregate resolved, no finding closed")
        self.assertOpen(tl, 5432)
        self.assertNotIn("security#exposure", tl.sent)
        # 3. valid and unchanged again: nothing, not a second new
        self.assertEqual(tl.step([port(5432)]), [])
        self.assertOpen(tl, 5432)
        # 4. valid, complete and gone: only now resolved
        self.assertEqual([(e["key"], e["kind"], e["why"]) for e in tl.step([])], [(fkey(5432), "resolved", "fixed")])

    def test_findings_that_break_the_rules_resolve_nothing_and_are_not_told_twice(self):
        base = self.open_two()
        for how in ("list", "object", "complete", "undeclared", "features"):
            for check, state in (("partial", "ok"), ("checked", "ok"), ("checked", "danger"), ("checked", "warning")):
                with self.subTest(how=how, check=check, state=state):
                    tl = base.copy()
                    self.assertEqual(tl.snapshot(result=sec_broken(how, check=check, state=state))
                                     ["security"]["items"][0]["listing"], "broken")
                    self.assertEqual(tl.step(result=sec_broken(how, check=check, state=state)), [])
                    self.assertOpen(tl, 5432, 6379)
                    self.assertEqual(tl.step(result=sec_broken(how, check=check, state=state), after=hsnotify.FLAP), [])
                    self.assertEqual(tl.step([port(5432), port(6379)]), [], "valid and unchanged: not a second new")
                    self.assertOpen(tl, 5432, 6379)
                    self.assertEqual(told(tl.step([port(5432)])), [(fkey(6379), "resolved", "danger", "ok")])

    def test_only_a_complete_list_in_time_resolves_after_a_gap(self):
        tl = self.open_two()
        gaps = [("the check ran in part", dict(findings=[port(5432)], check="partial")),
                ("the check failed", dict(findings=[port(5432)], check="failed")),
                ("no access", dict(findings=[port(5432)], check="noaccess")),
                ("exit 77", dict(result=hsproc.Result(77))),
                ("the item is out of date", dict(findings=[port(5432)], age=7200)),
                ("the check is out of date", dict(findings=[port(5432)], check_at=lambda: hslib.iso(tl.now + 300 - 7200))),
                ("not complete", dict(findings=[port(5432)], complete=False)),
                ("counts more than it lists", dict(findings=[port(5432)], total=2)),
                ("an empty list that counts one", dict(findings=[], total=1)),
                ("a broken list", dict(result=sec_broken("list", check="checked"))),
                ("no findings sent", dict(result=sec_broken("lost", check="checked"))),
                ("an older version", dict(result=sec_broken("older")))]
        for name, kw in gaps:
            if callable(kw.get("check_at")):
                kw["check_at"] = kw["check_at"]()
            self.assertEqual(tl.step(**kw), [], name)
            self.assertOpen(tl, 6379)
        self.assertEqual(told(tl.step([port(5432)])), [(fkey(6379), "resolved", "danger", "ok")])
        self.assertEqual(tl.step([port(5432)]), [])

    def test_a_broken_item_does_not_hold_back_another(self):
        def answer(exposure, containers, broken=False):
            def at_time(at):
                doc = json.loads(sec_doc(exposure, at, items=[{
                    "id": "containers", "state": "danger" if containers else "ok", "title": "Containers",
                    "findings": {"complete": True, "total": len(containers), "list": containers}}]))
                doc["checks"].append({"id": "containers", "state": "checked", "title": "Containers", "item": "containers",
                                      "checked_at": at, "stale_after": 3600})
                if broken:
                    doc["items"][0]["findings"]["list"] = "unreadable"
                return ok(json.dumps(doc))
            return at_time
        tl = Timeline()
        tl.step(result=answer([port(5432)], [sock("a")]))
        self.assertEqual(sorted(tl.sent), ["security#containers/socket.a", fkey(5432)])
        events = tl.step(result=answer([], [sock("b")], broken=True))
        self.assertEqual(told(events), [("security#containers/socket.b", "worse", "ok", "danger"),
                                        ("security#containers/socket.a", "resolved", "danger", "ok")])
        self.assertOpen(tl, 5432)

    def test_an_acceptance_stays_through_broken_data_and_its_end_is_told_with_valid_data(self):
        tl = Timeline()
        until = hslib.iso(NOW + 2 * 86400)
        self.assertEqual(told(tl.step([port(5432), port(6379, accepted_until=until)])),
                         [(fkey(5432), "worse", "ok", "danger")])
        self.assertEqual(tl.step(result=sec_broken("list", state="ok")), [])
        self.assertEqual(tl.sent[fkey(6379)]["status"], "accepted")
        self.assertEqual(tl.step([port(5432), port(6379, accepted_until=until)]), [], "still accepted")
        self.assertEqual(tl.step(result=sec_broken("complete", state="danger"), after=3 * 86400), [],
                         "ended while nothing can be read: told with the next valid answer")
        self.assertEqual(tl.sent[fkey(6379)]["status"], "accepted")
        self.assertEqual(told(tl.step([port(5432), port(6379, accepted_until=until)])),
                         [(fkey(6379), "expired", "danger", "danger")])
        self.assertEqual(tl.step([port(5432), port(6379, accepted_until=until)]), [])
        self.assertOpen(tl, 5432, 6379)

    def test_a_broken_item_is_told_worse_never_better(self):
        # danger with no finding listed (the list is not complete): the item itself is told
        tl = Timeline()
        self.assertEqual(told(tl.step([], state="danger", complete=False)), [("security#exposure", "worse", "ok", "danger")])
        for state in ("warning", "ok"):
            self.assertEqual(tl.step(result=sec_broken("list", state=state)), [], state)
            self.assertEqual(tl.sent["security#exposure"]["level"], "danger")
        self.assertEqual(told(tl.step([], state="ok")), [("security#exposure", "resolved", "danger", "ok")],
                         "a list that can be read")
        tl = Timeline()
        tl.step([], state="warning", complete=False)
        self.assertEqual(told(tl.step(result=sec_broken("object", state="danger"))),
                         [("security#exposure", "worse", "warning", "danger")])

    def test_an_addon_that_never_listed_findings_is_compared_per_item_as_before(self):
        def answer(state):
            return lambda at: ok(status_doc("security", [{"id": "exposure", "state": state, "title": "Exposure"}], at=at))
        tl = Timeline()
        self.assertEqual(tl.snapshot(result=answer("danger"))["security"]["items"][0]["listing"], "none")
        self.assertEqual(told(tl.step(result=answer("danger"))), [("security#exposure", "worse", "ok", "danger")])
        self.assertEqual(tl.step(result=answer("danger")), [])
        self.assertEqual(told(tl.step(result=answer("ok"))), [("security#exposure", "resolved", "danger", "ok")])
        self.assertEqual(told(tl.step(result=answer("warning"), after=hsnotify.FLAP)),
                         [("security#exposure", "worse", "ok", "warning")])


# ---------------------------------------------------------------------------
# Coverage: the twins of the board's checkCoverage(), checksCoverage(), tileOf() and coverageOf()
# against the board's own cases (cockpit-status-board, test/status.test.js, "coverage" and
# "coverage from checks"), then what a host's profile expects, run after run

def with_checks(checks, state="ok"):
    """An answer of the add-on x with the given checks and an item in state (as the board's
    withChecks())."""
    return hslib.validate_status(json.dumps({"schema": 1, "addon": "x", "state": state, "features": ["checks"],
                                             "checks": checks, "items": [{"id": "a", "state": state, "title": "A"}]}),
                                 "x")[1]


def tile(name_or_status, now=NOW):
    st = ext(name_or_status) if isinstance(name_or_status, str) else name_or_status
    return hslib.tile_of({"kind": "ok", "status": st}, now)


class CoverageTwin(unittest.TestCase):
    def test_checks_coverage_the_worst_check_that_applies_and_every_gap(self):
        sec = ext("ext-good-security.json")
        c = hslib.checks_coverage(sec, NOW)
        self.assertEqual([c["coverage"], c["worst"]["id"], [[g["id"], g["coverage"], g["stale_at"]] for g in c["gaps"]]],
                         ["partial", "containers", [["containers", "partial", None]]], "fail2ban does not apply")
        later = hslib.checks_coverage(sec, NOW + 3600)
        self.assertEqual([later["coverage"], later["worst"]["id"], [[g["id"], g["coverage"], g["stale_at"]] for g in later["gaps"]]],
                         ["stale", "exposure", [["exposure", "stale", ts("2026-10-19T19:50:00Z")],
                                                ["containers", "stale", ts("2026-10-19T19:50:00Z")]]])
        self.assertEqual(hslib.checks_coverage(sec, ts("2026-10-19T19:50:00Z"))["coverage"], "partial",
                         "on the dot it is not out of date yet")
        gaps = hslib.checks_coverage(ext("ext-good-update-gaps.json"), NOW)
        self.assertEqual([gaps["coverage"], gaps["worst"]["title"], len(gaps["gaps"])], ["unconfigured", "Open CVEs (network data)", 1])
        hs = ext("ext-good-host-settings.json")
        self.assertEqual([hslib.checks_coverage(hs, NOW)["coverage"], hslib.checks_coverage(hs, NOW)["worst"]], ["checked", None])
        self.assertEqual(hslib.checks_coverage(hs, ts("2026-10-19T19:11:00Z"))["coverage"], "stale",
                         "Notifications, 15 minutes after 18:55")

    def test_checks_coverage_none_without_checks_or_when_none_applies(self):
        for name in ("real-security.json", "good-cronjobs-ok.json", "ext-broken-shapes.json"):
            self.assertIsNone(hslib.checks_coverage(ext(name), NOW), name)
        self.assertIsNone(hslib.checks_coverage(with_checks([{"id": "d", "state": "notapplicable", "title": "D",
                                                              "reason": "no Docker"}]), NOW))
        self.assertIsNone(hslib.checks_coverage(with_checks([]), NOW))

    def test_checks_coverage_the_order_of_the_gaps(self):
        order = ["partial", "stale", "unsupported", "failed", "unconfigured", "noaccess"]

        def check(state, i):
            if state == "stale":
                return {"id": f"c{i}", "state": "checked", "title": f"C{i}", "checked_at": "2026-10-19T10:00:00Z", "stale_after": 60}
            return {"id": f"c{i}", "state": state, "title": f"C{i}"}
        for n in range(1, len(order) + 1):
            states = order[:n]
            self.assertEqual(hslib.checks_coverage(with_checks([check(s, i) for i, s in enumerate(states)]), NOW)["coverage"],
                             states[-1], states)
            self.assertEqual(hslib.checks_coverage(with_checks([check(s, i) for i, s in enumerate(reversed(states))]), NOW)["coverage"],
                             states[-1], "in any order")
        failed = with_checks([{"id": "f", "state": "failed", "title": "F", "checked_at": "2026-10-19T10:00:00Z", "stale_after": 60}])
        self.assertEqual(hslib.checks_coverage(failed, NOW)["coverage"], "failed", "no better for being out of date as well")

    def test_coverage_of_every_kind_of_tile(self):
        self.assertEqual(hslib.coverage_of(tile("good-cronjobs-ok.json"), NOW), "checked", "an entry in state none is no gap")
        self.assertEqual(hslib.coverage_of(tile("good-image-updates-stale.json"), NOW), "stale")
        self.assertEqual(hslib.coverage_of(tile("good-minimal.json"), NOW), "unchecked")
        self.assertEqual(tile("good-minimal.json")["word"], "unchecked")
        self.assertEqual(hslib.coverage_of(tile("ext-good-security.json"), NOW), "partial")
        self.assertEqual(hslib.coverage_of(tile("ext-good-security.json", NOW + 3600), NOW + 3600), "stale")
        self.assertEqual(hslib.coverage_of(tile("ext-good-update-gaps.json"), NOW), "unconfigured")
        self.assertEqual(hslib.coverage_of(tile("ext-good-host-settings.json"), NOW), "checked")
        self.assertEqual(hslib.coverage_of(hslib.tile_of({"kind": "noaccess"}, NOW), NOW), "noaccess")
        for kind in ("rejected", "invalid", "missing", "failed", "timeout", "unsupported", "manifest"):
            self.assertEqual(hslib.coverage_of(hslib.tile_of({"kind": kind}, NOW), NOW), "unreadable", kind)
        self.assertIsNone(hslib.coverage_of({"kind": "missing"}, NOW), "not installed: no gap of a check (without a profile)")
        self.assertIsNone(hslib.coverage_of({"kind": "nostatus"}, NOW))
        # every check applies or not, whatever the items say
        self.assertEqual(hslib.coverage_of(tile(with_checks([{"id": "c", "state": "failed", "title": "C"}])), NOW), "failed")
        self.assertEqual(hslib.coverage_of(tile(with_checks([{"id": "c", "state": "notapplicable", "title": "C"}])), NOW), "checked")
        self.assertEqual(hslib.coverage_of(tile(with_checks([{"id": "c", "state": "notapplicable", "title": "C"}], "none")), NOW),
                         "unchecked")
        # a check the profile leaves out does not apply (Host Settings only)
        self.assertEqual(hslib.coverage_of(tile("ext-good-security.json"), NOW, {"containers"}), "checked")
        self.assertEqual(hslib.coverage_of(tile("ext-good-update-gaps.json"), NOW, {"cve"}), "checked")

    def test_the_notifier_keeps_what_the_twins_need(self):
        sec = hsnotify.read_addon("security", None, ("command", SEC), "en", NOW,
                                  Runner({SEC: ok(board("status/ext-good-security.json"))}), lstat_of({SEC: (0o755, 0)}))
        self.assertEqual([c["id"] for c in sec["checks"]], ["exposure", "containers", "fail2ban"])
        self.assertEqual((sec["legacy"], hsnotify.addon_coverage(sec, NOW)), ("checked", "partial"))
        self.assertEqual(hsnotify.addon_coverage(sec, NOW, {"containers"}), "checked")
        cron = hsnotify.read_addon("cronjobs", None, ("command", CMD), "en", NOW,
                                   Runner({CMD: ok(board("status/good-image-updates-stale.json").replace('"image-updates"', '"cronjobs"'))}),
                                   lstat_of({CMD: (0o755, 0)}))
        self.assertEqual((cron["checks"], cron["legacy"], hsnotify.addon_coverage(cron, NOW)), (None, "stale", "stale"))
        self.assertEqual(hsnotify.addon_coverage(addon("A", [], kind="noaccess"), NOW), "noaccess")
        self.assertEqual(hsnotify.addon_coverage(addon("A", [], kind="timeout"), NOW), "unreadable")


def gaps_doc(checks, at, items=None):
    """A Security Monitor's answer with checks: [(id, state)], each checked at `at`, stale after an hour."""
    return json.dumps({"schema": 1, "addon": "security", "label": "Security Monitor", "version": "0.4.0", "checked_at": at,
                       "state": "ok", "summary": "s", "features": ["checks"],
                       "checks": [dict({"id": cid, "state": state, "title": cid.title(), "checked_at": at, "stale_after": 3600},
                                       **({"reason": "the Docker API did not answer"} if state == "failed" else {}))
                                  for cid, state in checks],
                       "items": items or [{"id": "logins", "state": "ok", "title": "Logins", "checked_at": at, "stale_after": 3600}]})


class Profiled:
    """A host's add-ons run after run with a profile: the Security Monitor's checks (or another
    result of its status verb), Cron Jobs and Update Gaps answering OK, through changes() with
    the coverage of the profile, the memory carried from run to run."""

    def __init__(self, roles=(), skip=(), extra=(), sent=None):
        self.profile = {"expect": hslib.resolve_expect(list(roles), list(extra)), "skip": list(skip)}
        self.sent, self.now = dict(sent or {}), NOW
        self.have = {"security", "cronjobs", "update-gaps"}

    def snapshot(self, checks=(("exposure", "checked"), ("containers", "checked")), result=None, gone=(), others=None):
        snap = {}
        if "security" not in gone:
            snap["security"] = read_sec(result or ok(gaps_doc(checks, hslib.iso(self.now - 60))), self.now)
        for pkg in ("cronjobs", "update-gaps"):
            if pkg not in gone:
                snap[pkg] = addon(pkg.title(), [{"id": "x", "state": "ok"}])
        snap.update(others or {})
        return snap

    def step(self, after=300, profile=True, **kw):
        self.now += after
        snap = self.snapshot(**kw)
        cov = hsnotify.profile_coverage(self.profile, snap, self.now, self.have - set(kw.get("gone", ()))) if profile else None
        events, self.sent = hsnotify.changes(self.sent, snap, self.now, cov)
        return events


def cov_told(events):
    return [(e["key"], e["kind"], e.get("coverage")) for e in events]


class Coverage(unittest.TestCase):
    def test_what_a_profile_expects_and_how_far_it_can_be_checked(self):
        p = Profiled(roles=["web"], extra=["security/exposure", "cronjobs/cron"])
        snap = p.snapshot(checks=[("exposure", "checked"), ("containers", "failed"), ("fail2ban", "notapplicable")])
        report, keys, hold = hsnotify.profile_coverage(p.profile, snap, NOW, p.have)
        self.assertEqual([(r["id"], r["coverage"], [g["id"] for g in r["gaps"]]) for r in report], [
            ("host-settings", "checked", []), ("update-gaps", "checked", []), ("security", "failed", ["containers"]),
            ("cronjobs", "checked", []), ("image-updates", "missing", []), ("security/exposure", "checked", []),
            ("cronjobs/cron", "notapplicable", [])])
        self.assertEqual(report[4]["label"], "Image Update Monitor", "the product name of one that is not installed")
        # cronjobs/cron: Cron Jobs reports no checks, so the entry is no gap of its own
        self.assertEqual({k: v[0] for k, v in keys.items()}, {
            "update-gaps@": "checked", "security@": "checked", "security@exposure": "checked", "security@containers": "failed",
            "cronjobs@": "checked", "image-updates@": "missing"})
        self.assertEqual(keys["security@containers"][2:], ("Containers", "the Docker API did not answer"))
        self.assertEqual(hold, {"image-updates"})
        # installed in a version without a status entry: not supported, not missing
        report, keys, _ = hsnotify.profile_coverage(p.profile, snap, NOW, p.have | {"image-updates"})
        self.assertEqual(keys["image-updates@"][0], "unsupported")
        # a check expected on its own makes its add-on expected, and so every check of it
        q = Profiled(roles=["build"])
        q.profile["expect"] = ["host-settings", "image-updates/rollout"]
        report, keys, _ = hsnotify.profile_coverage(q.profile, q.snapshot(), NOW, q.have)
        self.assertEqual([(r["id"], r["coverage"]) for r in report], [("host-settings", "checked"), ("image-updates/rollout", "missing")])
        self.assertEqual({k: v[0] for k, v in keys.items()}, {"image-updates@": "missing"})
        q.profile["expect"] = ["security/exposure"]
        _, keys, _ = hsnotify.profile_coverage(q.profile, q.snapshot(checks=[("containers", "failed")]), NOW, q.have)
        self.assertEqual({k: v[0] for k, v in keys.items()}, {"security@": "checked", "security@containers": "failed"},
                         "exposure, which the add-on does not report, is no gap; its other checks are expected")

    def test_an_expected_addon_goes_missing_and_comes_back_two_messages(self):
        p = Profiled()
        self.assertEqual(p.step(), [])
        events = p.step(gone=["security"])
        self.assertEqual(cov_told(events), [("security@", "lost", "missing")])
        msg = hsnotify.message("en", "web1", events=events)
        self.assertEqual(msg["title"], "web1: Security Monitor, check no longer possible")
        self.assertEqual(msg["body"], "Check no longer possible · Security Monitor\nthe add-on is not installed\n\nHost Settings on web1")
        self.assertEqual((msg["priority"], msg["tags"]), (3, ["warning"]))
        de = hsnotify.message("de", "web1", events=events)
        self.assertEqual((de["title"], de["body"].split("\n\n")[0]), (
            "web1: Security Monitor, Prüfung nicht mehr möglich",
            "Prüfung nicht mehr möglich · Security Monitor\ndas Add-on ist nicht installiert"))
        self.assertEqual(p.step(gone=["security"]), [], "told once")
        self.assertEqual(p.sent["security@"]["level"], "lost")
        events = p.step(after=hsnotify.FLAP)
        self.assertEqual(cov_told(events), [("security@", "regained", "checked")])
        back = hsnotify.message("en", "web1", events=events)
        self.assertEqual((back["title"], back["body"].split("\n\n")[0], back["priority"]),
                         ("web1: Security Monitor, check possible again", "Check possible again · Security Monitor", 2))
        self.assertEqual(hsnotify.message("de", "web1", events=events)["body"].split("\n\n")[0],
                         "Prüfung wieder möglich · Security Monitor")
        self.assertEqual(p.step(), [], "two messages in all")

    def test_an_addon_that_is_gone_keeps_its_name(self):
        p = Profiled(extra=["thing"])
        p.have.add("thing")
        self.assertEqual(p.step(others={"thing": addon("The Thing", [{"id": "x", "state": "ok"}])}), [])
        self.assertEqual(p.sent["thing@"]["label"], "The Thing")
        p.have.discard("thing")
        events = p.step()
        self.assertEqual([hsnotify._line("en", e) for e in events], ["Check no longer possible · The Thing\nthe add-on is not installed"])
        self.assertEqual(hsnotify.message("en", "h", events=events)["title"], "h: The Thing, check no longer possible")

    def test_an_optional_addon_that_goes_missing_says_nothing(self):
        p = Profiled(roles=["build"])
        img = {"image-updates": addon("Image Update Monitor", [{"id": "updates", "state": "info"}])}
        self.assertEqual(p.step(others=img), [])
        self.assertEqual(p.step(), [], "not expected: no message when it is gone")
        self.assertFalse([k for k in p.sent if k.startswith("image-updates")])
        # with the role web it is expected
        p = Profiled(roles=["web"])
        self.assertEqual(cov_told(p.step()), [("image-updates@", "lost", "missing")])

    def test_a_skip_that_runs_then_ends(self):
        tomorrow = hslib.iso(NOW + 86400)[:10]
        p = Profiled(skip=[{"check": "security/containers", "until": tomorrow, "reason": "no Docker yet"}])
        self.assertEqual(p.step(checks=[("exposure", "checked"), ("containers", "failed")]), [], "not applicable while it runs")
        self.assertNotIn("security@containers", p.sent)
        report, _, _ = hsnotify.profile_coverage(p.profile, p.snapshot(checks=[("containers", "failed")]), p.now, p.have)
        self.assertEqual(report[2]["coverage"], "checked", "the skipped check is no gap of the add-on")
        events = p.step(after=86400, checks=[("exposure", "checked"), ("containers", "failed")])
        self.assertEqual(cov_told(events), [("security@containers", "lost", "failed")], "it ended on its day: it counts")
        self.assertEqual(hsnotify._line("en", events[0]),
                         "Check no longer possible · Security Monitor · Containers\nthe check failed: the Docker API did not answer")
        self.assertEqual(hsnotify._line("de", events[0]).splitlines()[1],
                         "die Prüfung ist fehlgeschlagen: the Docker API did not answer")
        # a skip without an end, set while it was told: forgotten without a word
        p.profile["skip"] = [{"check": "security/containers", "until": None, "reason": "r"}]
        self.assertEqual(p.step(checks=[("exposure", "checked"), ("containers", "failed")]), [])
        self.assertNotIn("security@containers", p.sent)
        # a skip that names an add-on alone does nothing (as on Status Board; the helper refuses it)
        p.profile["skip"] = [{"check": "security", "until": None, "reason": "r"}]
        self.assertEqual(cov_told(p.step(gone=["security"])), [("security@", "lost", "missing")])

    def test_no_access_and_not_set_up(self):
        p = Profiled()
        p.step()
        events = p.step(result=hsproc.Result(77))
        self.assertEqual(cov_told(events), [("security@", "lost", "noaccess")], "exit 77 of an expected add-on is told")
        self.assertEqual(hsnotify._line("en", events[0]).splitlines()[1], "nothing is readable without root")
        events = p.step(after=hsnotify.FLAP, checks=[("exposure", "checked"), ("containers", "unconfigured")])
        self.assertEqual(cov_told(events), [("security@containers", "lost", "unconfigured"), ("security@", "regained", "checked")],
                         "what is lost first")
        self.assertEqual(hsnotify._line("en", events[0]).splitlines()[1],
                         "the check is not set up (its timer is off, or it never ran)")
        msg = hsnotify.message("en", "h", events=events)
        self.assertEqual((msg["title"], msg["priority"]), ("h: 2 changes", 3))
        # unreadable: the message about the add-on's status says it, the coverage is remembered
        events = p.step(after=hsnotify.FLAP, result=ok("{"))
        self.assertEqual([(e["key"], e["kind"]) for e in events], [("security#", "worse")])
        self.assertEqual((p.sent["security@"]["level"], p.sent["security@"]["coverage"]), ("lost", "unreadable"))
        self.assertEqual(p.sent["security@containers"]["level"], "lost", "a check of an add-on that cannot be read stays")

    def test_the_hour_against_flapping(self):
        p = Profiled()
        p.step()
        self.assertEqual(len(p.step(checks=[("exposure", "failed")])), 1)
        self.assertEqual(cov_told(p.step(checks=[("exposure", "checked")])), [("security@exposure", "regained", "checked")])
        self.assertEqual(p.step(checks=[("exposure", "failed")]), [], "the same change within the hour waits")
        self.assertEqual(p.sent["security@exposure"]["level"], "ok")
        self.assertEqual(cov_told(p.step(after=hsnotify.FLAP, checks=[("exposure", "failed")])),
                         [("security@exposure", "lost", "failed")], "told after the hour, as it still holds")

    def test_a_run_without_a_change_and_gaps_that_are_no_loss(self):
        p = Profiled()
        p.step(checks=[("exposure", "failed")])
        for _ in range(3):
            self.assertEqual(p.step(checks=[("exposure", "failed")]), [])
        # partial, out of date or not supported keep what was told: no message either way
        self.assertEqual(p.step(checks=[("exposure", "partial")]), [])
        self.assertEqual(p.sent["security@exposure"]["level"], "lost")
        old_check = gaps_doc([("exposure", "checked")], hslib.iso(p.now - 7200), [{"id": "logins", "state": "ok", "title": "Logins"}])
        self.assertEqual(p.step(result=ok(old_check)), [], "out of date")
        self.assertEqual(cov_told(p.step(after=hsnotify.FLAP, checks=[("exposure", "checked")])),
                         [("security@exposure", "regained", "checked")])
        q = Profiled()
        q.step()
        self.assertEqual(q.step(checks=[("exposure", "partial")]), [], "partial was OK before: no loss")
        self.assertEqual(q.step(checks=[("exposure", "unsupported")]), [])

    def test_without_a_profile_no_coverage_messages(self):
        p = Profiled()
        p.step(gone=["security"])
        self.assertIn("security@", p.sent)
        self.assertEqual(p.step(profile=False), [], "the profile removed: nothing told")
        self.assertFalse([k for k in p.sent if "@" in k], "and the coverage keys are forgotten")
        self.assertEqual(p.step(profile=False, gone=["security"]), [], "today's behaviour: an add-on that told nothing goes quietly")
        # set up again later: the gaps of now are told once
        self.assertEqual(cov_told(p.step(gone=["security"])), [("security@", "lost", "missing")])
        self.assertEqual(p.step(gone=["security"]), [])

    def test_old_last_json_keys_keep_working_with_a_profile(self):
        old = {"cronjobs#x": {"level": "warning", "at": NOW - 86400, "last": {"ok>warning": NOW - 86400}, "label": "Cron Jobs",
                              "title": "X"},
               "security#logins": {"level": "warning", "at": NOW - 86400, "last": {}, "label": "Security Monitor", "title": "Logins"}}
        p = Profiled(sent=old)
        events = p.step(checks=[("exposure", "checked"), ("containers", "failed")])
        self.assertEqual(sorted((e["key"], e["kind"]) for e in events),
                         [("cronjobs#x", "resolved"), ("security#logins", "resolved"), ("security@containers", "lost")])
        self.assertEqual(p.step(checks=[("exposure", "checked"), ("containers", "failed")]), [])

    def test_a_removed_addon_that_told_problems_is_told_once(self):
        p = Profiled()
        p.step(result=ok(gaps_doc([("exposure", "checked")], hslib.iso(NOW + 240),
                                  [{"id": "logins", "state": "danger", "title": "Logins", "checked_at": hslib.iso(NOW + 240),
                                    "stale_after": 3600}])))
        events = p.step(gone=["security"])
        self.assertEqual([(e["key"], e["kind"]) for e in events], [("security#", "unchecked")],
                         "no longer checked says it; the coverage is remembered")
        self.assertEqual(p.sent["security@"]["level"], "lost")

    def test_the_first_summary_names_the_gaps(self):
        p = Profiled(roles=["web"])
        snap = p.snapshot(checks=[("exposure", "checked"), ("containers", "noaccess")])
        gaps, told = hsnotify.coverage_changes({}, hsnotify.profile_coverage(p.profile, snap, NOW, p.have), NOW)
        msg = hsnotify.message("en", "h", snapshot=snap, summary=True, gaps=gaps)
        self.assertEqual(msg["body"].split("\n\n"), [
            "Messages about the Azentrius add-ons of this host now come this way.", "Right now:",
            "Check no longer possible · Image Update Monitor\nthe add-on is not installed",
            "Check no longer possible · Security Monitor · Containers\nnothing is readable without root", "Host Settings on h"])
        self.assertEqual(msg["priority"], 3)
        base = dict(hsnotify.baseline(snap, NOW), **told)
        self.assertEqual(sorted(k for k in base if base[k].get("level") == "lost"), ["image-updates@", "security@containers"])
        self.assertEqual(base["security@exposure"]["level"], "ok", "every expected key is remembered")
        events, _ = hsnotify.changes(base, snap, NOW + 300, hsnotify.profile_coverage(p.profile, snap, NOW + 300, p.have))
        self.assertEqual(events, [], "nothing told twice")

    def test_the_memory_keeps_lost_coverage_first(self):
        sent = {f"security#exposure/port.{n}-tcp": {"level": "ok", "status": "resolved", "at": NOW - n, "last": {},
                                                    "label": "Security Monitor", "title": str(n)} for n in range(1, 2101)}
        sent["image-updates@"] = {"level": "lost", "coverage": "missing", "at": NOW - 99999, "last": {}, "label": "Image Update Monitor"}
        p = Profiled(roles=["web"], sent=sent)
        self.assertEqual(p.step(), [])
        self.assertIn("image-updates@", p.sent)
        self.assertEqual(len(p.sent), hsnotify.MAX_KEYS)


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
             hsnotify._event("d#z", addon("D", []), None, "warning", "ok", "gone", {"label": "D", "title": "Z"})]
        e[2]["code"] = 2
        msg = hsnotify.message("en", "h", events=e)
        self.assertEqual(msg["title"], "h: 4 changes")
        self.assertEqual(msg["body"].split("\n\n"), [
            "Warning · A · X\nout of date since 19 Oct 18:00 UTC; d",
            "Warning instead of Danger · B · Y (1)",
            "Warning · C · Status not readable\nthe command failed (exit 2)",
            "OK again · D · Z\nno longer reported",
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

    def test_both_languages_have_the_same_texts(self):
        import string
        self.assertEqual(sorted(hsnotify.T["en"]), sorted(hsnotify.T["de"]))
        for key, en in hsnotify.T["en"].items():
            fields = sorted(f for _, f, _, _ in string.Formatter().parse(en) if f)
            self.assertEqual(fields, sorted(f for _, f, _, _ in string.Formatter().parse(hsnotify.T["de"][key]) if f), key)


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
# to its topic, as ntfy 2.11 gave them in the job e2e: 403 for what a login does not allow, for
# reading and sending without a login as for reading with the write-only token.
CLOSED = {("GET", "https://ntfy.example.com/v1/health"): (200, '{"healthy":true}'),
          ("POST", "https://ntfy.example.com/", "auth"): (200, '{"id":"x"}'),
          ("POST", "https://ntfy.example.com/", "anon"): (403, '{"code":40301,"http":403,"error":"forbidden"}'),
          ("GET", "https://ntfy.example.com/cockpit-h/json", "anon"): (403, '{"code":40301}'),
          ("GET", "https://ntfy.example.com/cockpit-h/json", "auth"): (403, '{"code":40301}')}


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
                         [("GET", "https://ntfy.example.com/cockpit-h/json?poll=1&since=1m"),
                          ("POST", "https://ntfy.example.com/")])
        with_token = [(c["method"], c["url"]) for c in http.calls if "Authorization" in c["headers"]]
        self.assertEqual(with_token, [("POST", "https://ntfy.example.com/"),
                                      ("GET", "https://ntfy.example.com/cockpit-h/json?poll=1&since=1m")])
        probe = anon[-1]["body"]
        self.assertEqual(probe["topic"], "cockpit-h")
        self.assertIn("auth-default-access: deny-all", probe["message"])

    def test_an_open_server(self):
        answers = dict(CLOSED)
        answers.update({("POST", "https://ntfy.example.com/", "anon"): (200, "{}"),
                        ("GET", "https://ntfy.example.com/cockpit-h/json", "anon"): (200, ""),
                        ("GET", "https://ntfy.example.com/cockpit-h/json", "auth"): (200, "")})
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

    def test_what_the_token_may_read(self):
        for answer, want in (((403, ""), ("ok", "write-only")), ((401, ""), ("ok", "write-only")),
                             ((200, ""), ("info", "can-read")), ((500, ""), ("info", "unknown")),
                             ("timeout", ("info", "timeout"))):
            answers = dict(CLOSED)
            answers[("GET", "https://ntfy.example.com/cockpit-h/json", "auth")] = answer
            steps = hsnotify.check_ntfy(CFG, TOKEN, "en", "h", Http(answers), ("reach", "token"))
            self.assertEqual(results(steps)["token"], want, answer)


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
