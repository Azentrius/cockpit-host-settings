# SPDX-License-Identifier: Apache-2.0
"""Host Settings, the Cockpit add-on: what the notifier does.

Lives in /usr/lib/cockpit-host-settings (root-owned). Standard library only,
no side effects on import. host-settings-notify.py runs it as the service's
own user, never as root; the commands, the network and the clock come in as
arguments, so the tests can stand in for all of them.

  discover()    the add-ons with a status entry in /usr/share/cockpit/*/manifest.json
  collect()     their status verbs (`COMMAND status --lang xx`), judged as Status Board does
  changes()     what changed since the last message: per item (add-on and id), and per finding
                (add-on, item and fid) for an item that lists its findings, with an hour against
                flapping; with a target profile also whether each expected add-on and check can
                still be checked (profile_coverage())
  message()     the text of a message: only what the add-ons said, their labels and this
                host's name
  send_ntfy(), send_mail()   the two channels
  check_ntfy()  the page's "Check connection": reachable, the token may publish, the topic is
                closed to anyone without a login

Formats: docs/INTERNALS.md.
"""

import json
import os
import smtplib
import socket
import ssl
import stat
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import hslib
import hsproc

ADDON = "host-settings"            # this add-on's own folder: never read by itself
FORMAT = 1                         # of last.json; another one starts the memory anew (and a summary)
FLAP = 3600                        # the same change of the same key again only after an hour
KEEP = 30 * 86400                  # a resolved finding is remembered this long, to tell "again"
MAX_KEYS = 2000                    # keys in the memory (sent) at most
CHECK_EVERY = 86400                # the topic's protection without a login: read once a day
VERB_LIMIT = 10                    # seconds per status verb (the contract asks for 2 at most)
HTTP_TIMEOUT = 20
BODY_MAX = 3800                    # bytes; ntfy turns longer messages into attachments (4096)
MAX_LINES = 20
PROBLEM = ("warning", "danger")
UNKNOWN = "unknown"                # a key that cannot be judged now keeps what was told before
ERROR_KINDS = ("rejected", "untrusted", "manifest", "invalid", "missing", "failed", "timeout", "unsupported")
# In a key of the memory: what a host's profile expects ("PKG@" an add-on, "PKG@CHECK" a check).
# Package names and check ids have no "@", so these keys never meet "PKG#", "PKG#ID", "PKG#ID/FID".
COVERAGE = "@"
# The coverage of an expected add-on or check that makes it "no longer possible"; back to
# "checked" it is "possible again". Every other gap (partial, stale, unchecked, unsupported)
# keeps what was told.
LOST = ("failed", "noaccess", "unconfigured", "unreadable", "missing")
PRIORITY = {"danger": 4, "warning": 3, "ok": 2}
TAGS = {"danger": "rotating_light", "warning": "warning", "ok": "white_check_mark"}

T = {
    "en": {
        "state.none": "Unknown", "state.ok": "OK", "state.info": "Note", "state.warning": "Warning",
        "state.danger": "Danger",
        "title.one": "{host}: {label}, {title}",
        "title.many": "{host}: {n} changes",
        "title.summary": "{host}: messages set up",
        "title.test": "{host}: test message",
        "title.probe": "{host}: this topic is open",
        "head.worse": "{state}", "head.better": "{state} instead of {before}", "head.resolved": "OK again",
        "head.again": "{state}, again", "head.expired": "{state}, acceptance ended",
        "head.unchecked": "No longer checked",
        "why.gone": "no longer reported", "why.fixed": "no longer found by a complete check",
        "title.unchecked": "{host}: {label}, no longer checked",
        "unchecked.why": "the add-on is no longer installed",
        "unchecked.one": "the finding it reported can no longer be checked",
        "unchecked.other": "the {n} findings it reported can no longer be checked",
        "head.lost": "Check no longer possible", "head.regained": "Check possible again",
        "title.lost": "{host}: {label}, check no longer possible",
        "title.regained": "{host}: {label}, check possible again",
        "cov.failed": "the check failed", "cov.noaccess": "nothing is readable without root",
        "cov.unconfigured": "the check is not set up (its timer is off, or it never ran)",
        "cov.unreadable": "the status cannot be read", "cov.missing": "the add-on is not installed",
        "stale": "out of date since {when}",
        "unreadable": "Status not readable", "readable": "status readable",
        "reason.not-json": "the answer is not JSON", "reason.not-object": "the answer is not an object",
        "reason.not-text": "the answer is not text", "reason.too-large": "the answer is too large",
        "reason.schema": "another version of the status contract", "reason.addon": "no valid add-on name",
        "reason.addon-mismatch": "the answer names another add-on", "reason.state": "no valid state",
        "reason.items": "no list of items", "reason.rejected": "the manifest names a command that is not allowed",
        "reason.untrusted": "the command does not belong to root or others can change it",
        "reason.manifest": "the manifest could not be read", "reason.missing": "the command is missing",
        "reason.failed": "the command failed (exit {code})", "reason.timeout": "the command took too long",
        "reason.unsupported": "the command does not know the status verb",
        "more": "and {n} more",
        "summary.intro": "Messages about the Azentrius add-ons of this host now come this way.",
        "summary.problems": "Right now:",
        "summary.clean": "No warnings right now; {n} add-ons read.",
        "summary.none": "No add-on with a status was found on this host.",
        "test.body": "A test message from Host Settings on {host}. Messages about the Azentrius add-ons of this "
                     "host come this way.",
        "probe.body": "Host Settings on {host}: this topic accepts messages without a login, so anyone who knows "
                      "its name can send messages here. Set auth-default-access: deny-all on the ntfy server and "
                      "give the host's user write access to the topic only.",
        "footer": "Host Settings on {host}",
    },
    "de": {
        "state.none": "Unbekannt", "state.ok": "In Ordnung", "state.info": "Hinweis", "state.warning": "Achtung",
        "state.danger": "Gefahr",
        "title.one": "{host}: {label}, {title}",
        "title.many": "{host}: {n} Änderungen",
        "title.summary": "{host}: Nachrichten eingerichtet",
        "title.test": "{host}: Testnachricht",
        "title.probe": "{host}: dieses Thema ist offen",
        "head.worse": "{state}", "head.better": "{state} statt {before}", "head.resolved": "Wieder in Ordnung",
        "head.again": "{state}, wieder aufgetreten", "head.expired": "{state}, Akzeptanz abgelaufen",
        "head.unchecked": "Nicht mehr geprüft",
        "why.gone": "wird nicht mehr gemeldet", "why.fixed": "bei einer vollständigen Prüfung nicht mehr gefunden",
        "title.unchecked": "{host}: {label}, nicht mehr geprüft",
        "unchecked.why": "das Add-on ist nicht mehr installiert",
        "unchecked.one": "der gemeldete Befund lässt sich nicht mehr prüfen",
        "unchecked.other": "die {n} gemeldeten Befunde lassen sich nicht mehr prüfen",
        "head.lost": "Prüfung nicht mehr möglich", "head.regained": "Prüfung wieder möglich",
        "title.lost": "{host}: {label}, Prüfung nicht mehr möglich",
        "title.regained": "{host}: {label}, Prüfung wieder möglich",
        "cov.failed": "die Prüfung ist fehlgeschlagen", "cov.noaccess": "ohne root ist nichts lesbar",
        "cov.unconfigured": "die Prüfung ist nicht eingerichtet (ihr Timer ist aus, oder sie lief nie)",
        "cov.unreadable": "der Status lässt sich nicht lesen", "cov.missing": "das Add-on ist nicht installiert",
        "stale": "veraltet seit {when}",
        "unreadable": "Status nicht lesbar", "readable": "Status lesbar",
        "reason.not-json": "die Antwort ist kein JSON", "reason.not-object": "die Antwort ist kein Objekt",
        "reason.not-text": "die Antwort ist kein Text", "reason.too-large": "die Antwort ist zu groß",
        "reason.schema": "eine andere Version des Status-Vertrags", "reason.addon": "kein gültiger Add-on-Name",
        "reason.addon-mismatch": "die Antwort nennt ein anderes Add-on", "reason.state": "kein gültiger Zustand",
        "reason.items": "keine Liste von Einträgen",
        "reason.rejected": "das Manifest nennt einen Befehl, der nicht erlaubt ist",
        "reason.untrusted": "der Befehl gehört nicht root, oder andere können ihn ändern",
        "reason.manifest": "das Manifest ließ sich nicht lesen", "reason.missing": "der Befehl fehlt",
        "reason.failed": "der Befehl schlug fehl (Exit {code})", "reason.timeout": "der Befehl brauchte zu lange",
        "reason.unsupported": "der Befehl kennt das Status-Verb nicht",
        "more": "und {n} weitere",
        "summary.intro": "Nachrichten über die Azentrius-Add-ons dieses Hosts kommen jetzt auf diesem Weg.",
        "summary.problems": "Im Moment:",
        "summary.clean": "Im Moment keine Warnungen; {n} Add-ons gelesen.",
        "summary.none": "Auf diesem Host wurde kein Add-on mit Status gefunden.",
        "test.body": "Eine Testnachricht von Host Settings auf {host}. Nachrichten über die Azentrius-Add-ons "
                     "dieses Hosts kommen auf diesem Weg.",
        "probe.body": "Host Settings auf {host}: Dieses Thema nimmt Nachrichten ohne Anmeldung an, also kann jeder, "
                      "der seinen Namen kennt, hier Nachrichten einschleusen. Setzen Sie auf dem ntfy-Server "
                      "auth-default-access: deny-all, und geben Sie dem Benutzer des Hosts nur Schreibrecht auf "
                      "das Thema.",
        "footer": "Host Settings auf {host}",
    },
}
MONTHS = {"en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")}


def t(lang, key, **kw):
    return T["de" if lang == "de" else "en"][key].format(**kw)


def when(lang, ts):
    d = datetime.fromtimestamp(ts, timezone.utc)
    if lang == "de":
        return f"{d.day}.{d.month}. {d:%H:%M} UTC"
    return f"{d.day} {MONTHS['en'][d.month - 1]} {d:%H:%M} UTC"


def level(state):
    """Warning and danger are what messages are about; everything else counts as OK."""
    return state if state in PROBLEM else "ok"


# ---------------------------------------------------------------------------
# The add-ons on this host

def _read_manifest(path, limit=1 << 20):
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError("too large")
    return json.loads(data.decode("utf-8"))


def discover(root="/usr/share/cockpit"):
    """[(pkg, label, entry)], sorted by package: entry is ("command", path), ("rejected", None)
    or ("manifest", None) for a manifest that could not be read. Packages without a status
    entry, this add-on and folders that are no package names are left out."""
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for pkg in names:
        if pkg == ADDON or not hslib.PACKAGE_RE.fullmatch(pkg):
            continue
        path = os.path.join(root, pkg, "manifest.json")
        if not os.path.isfile(path):
            continue
        try:
            manifest = _read_manifest(path)
        except (OSError, ValueError, RecursionError):
            out.append((pkg, None, ("manifest", None)))
            continue
        entry = hslib.status_entry(manifest)
        if entry:
            out.append((pkg, hslib.menu_label(manifest), entry))
        if len(out) >= hslib.MAX_PACKAGES:
            break
    return out


def trusted(path, lstat=os.lstat):
    """Whether a status command and every folder above it belong to root and nobody else can
    change them. The manifest's own check (COMMAND_RE) keeps it below /usr/lib/cockpit-NAME/."""
    try:
        st = lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
        return False
    d = os.path.dirname(path)
    while d not in ("", "/"):
        try:
            st = lstat(d)
        except OSError:
            return False
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            return False
        d = os.path.dirname(d)
    return True


def read_addon(pkg, label, entry, lang, now, run=hsproc.run, lstat=os.lstat):
    """What one add-on says, as the notifier keeps it: {label, kind, reason, code, state,
    version, summary, items, dropped, checks, legacy}. kind is "ok" for a valid answer,
    "noaccess" for exit 77 (nothing can be judged, so it keeps what was told; Status Board shows
    it as a warning), else one of ERROR_KINDS (a warning). dropped counts
    the items a valid answer had beyond the 50 that count, or that broke the rules.

    An item's state is the one the board shows at the time now (never better than its open
    findings, at least a warning when out of date); its findings are None, or {complete, total,
    list} when the add-on declares the feature and the item lists them, each finding {fid,
    state, title, accepted, link} with accepted judged at the time now. checks are the answer's
    checks (hslib.validate_check) or None; legacy is what its items say about its coverage, as
    the board judges an answer without checks (checked, unchecked, stale)."""
    out = {"label": label or pkg, "kind": "ok", "reason": None, "code": None, "state": "none",
           "version": None, "summary": None, "items": [], "dropped": 0, "checks": None, "legacy": None}

    def fail(kind, reason=None, code=None):
        out.update(kind=kind, reason=reason, code=code, state="none" if kind == "noaccess" else "warning")
        return out

    what, cmd = entry
    if what != "command":
        return fail(what)
    ok = trusted(cmd, lstat)
    if ok is None:
        return fail("missing")
    if not ok:
        return fail("untrusted")
    res = run(hslib.status_argv(cmd, lang), VERB_LIMIT, hslib.MAX_OUTPUT)
    if res.missing:
        return fail("missing")
    if res.timeout:
        return fail("timeout")
    if res.rc == 77:
        return fail("noaccess")
    if res.rc == 64:
        return fail("unsupported")
    if res.rc != 0:
        return fail("failed", code=res.rc)
    if res.cut:
        return fail("invalid", "too-large")
    good, status = hslib.validate_status(res.out, pkg)
    if not good:
        return fail("invalid", status[0])
    ev = hslib.evaluate(status, now)
    # The check that shows each item's result (the board's rule for complete lists), and whether
    # it is in time: a check out of date says nothing about a finding missing from the list.
    owner = {c["item"]: c for c in (status["checks"] or []) if c["item"] is not None}
    out.update(label=status["label"] or out["label"], state=ev["state"], version=status["version"],
               summary=status["summary"], dropped=status["dropped"],
               items=[{"id": i["id"], "title": i["title"], "state": i["state"], "value": i["value"],
                       "detail": i["detail"], "link": i["link"], "stale_at": i["stale_at"],
                       "findings": _findings_of(i["findings"], owner.get(i["id"]), now)} for i in ev["items"]],
               checks=status["checks"],
               legacy=hslib.coverage_of(hslib.tile_of({"kind": "ok", "status": dict(status, checks=None)}, now), now))
    return out


def _findings_of(found, check, now):
    """An item's findings as the notifier keeps them: complete as the board judges it, fresh
    while the check that shows them is in time, each finding accepted or not at the time now."""
    if found is None:
        return None
    stale = check is not None and check["checked_at"] is not None and check["stale_after"] is not None and \
        now > check["checked_at"] + check["stale_after"]
    return {"complete": found["complete"], "fresh": not stale, "total": found["total"],
            "list": [{"fid": f["fid"], "state": f["state"], "title": f["title"], "accepted": hslib.accepted(f, now),
                      "link": f["link"]} for f in found["list"]]}


def collect(lang, now, run=hsproc.run, root="/usr/share/cockpit", lstat=os.lstat):
    """{pkg: read_addon(...)} for every add-on with a status entry."""
    return {pkg: read_addon(pkg, label, entry, lang, now, run, lstat) for pkg, label, entry in discover(root)}


def installed(root="/usr/share/cockpit"):
    """The package folders with a manifest: an expected add-on that is not among the add-ons with a
    status entry is installed in a version without one (unsupported) or not at all (missing)."""
    try:
        names = os.listdir(root)
    except OSError:
        return set()
    return {p for p in names if hslib.PACKAGE_RE.fullmatch(p) and os.path.isfile(os.path.join(root, p, "manifest.json"))}


# ---------------------------------------------------------------------------
# What a host's profile expects, and how far it can be checked (coverage)

def addon_coverage(a, now, skipped=()):
    """The coverage of an add-on of a snapshot, as the board judges it (hslib.coverage_of()): from
    the checks that apply, without those the profile leaves out (skipped: their ids), else from
    the items."""
    if a["kind"] == "noaccess":
        return "noaccess"
    if a["kind"] != "ok":
        return "unreadable"
    from_checks = hslib.checks_coverage({"checks": a.get("checks")}, now, skipped)
    return from_checks["coverage"] if from_checks else a.get("legacy") or "checked"


def profile_coverage(profile, snapshot, now, have=()):
    """What a host's profile expects and how far it can be checked at the time now, as
    (report, keys, hold); profile is {expect, skip} (expect resolved, skip [{check, until,
    reason}]), have the package folders with a manifest (installed()).

      report  [{id, label, coverage, gaps}] in the order of expect, for the page: an add-on's
              coverage as the board judges it, with the checks the profile leaves out taken as
              not applicable, and its checks with a gap [{id, title, coverage}]; for a check,
              its own coverage when the add-on reports it, "notapplicable" when it does not or a
              running skip leaves it out, the add-on's when the add-on cannot be judged
      keys    {key: (coverage, label, title, note)}, what the messages compare: "PKG@" for each
              expected add-on (missing, unsupported, noaccess, unreadable; for a valid answer
              "checked" while its checks speak for themselves, else what its items say) and
              "PKG@CHECK" for each check of it that applies; title is the check's title, note its
              reason (text of the add-on)
      hold    the add-ons whose checks cannot be judged now (not installed, no access,
              unreadable): what was told about their checks stays as it was

    As Status Board reads a profile: an entry "PKG/CHECK" makes the add-on PKG expected, and so
    every check of it, and is no gap of its own when PKG does not report that check; a skip names
    one check ("PKG/CHECK"; one naming an add-on alone does nothing). This add-on itself counts
    as checked: it is the one running."""
    skips = {s["check"] for s in profile["skip"] if "/" in s["check"] and hslib.skip_active(s, now)}
    keys, hold, judged = {}, set(), {}
    for pkg in dict.fromkeys(e.partition("/")[0] for e in profile["expect"]):
        a = snapshot.get(pkg)
        # the product name; None for an add-on that is gone and not one the roles name, which
        # coverage_changes() then takes from the memory
        label = (a or {}).get("label") or hslib.ADDON_LABELS.get(pkg)
        if pkg == ADDON:
            judged[pkg] = (label, "checked", None)
            continue
        if a is None or a["kind"] != "ok":
            cov = addon_coverage(a, now) if a else "unsupported" if pkg in have else "missing"
            judged[pkg] = (label, cov, None)
            keys[pkg + COVERAGE] = (cov, label, None, None)
            hold.add(pkg)
            continue
        skipped = {s.partition("/")[2] for s in skips if s.startswith(pkg + "/")}
        from_checks = hslib.checks_coverage({"checks": a.get("checks")}, now, skipped)
        cov = from_checks["coverage"] if from_checks else a.get("legacy") or "checked"
        judged[pkg] = (label, cov, from_checks)
        keys[pkg + COVERAGE] = ("checked" if from_checks else cov, label, None, None)
        for c in a.get("checks") or []:
            if c["state"] != "notapplicable" and c["id"] not in skipped:
                keys[pkg + COVERAGE + c["id"]] = (hslib.check_coverage(c, now)["coverage"], label, c["title"], c["reason"])
    report = []
    for item in profile["expect"]:
        pkg, _, check = item.partition("/")
        label, cov, from_checks = judged[pkg]
        row = {"id": item, "label": label or pkg, "coverage": cov, "gaps": []}
        report.append(row)
        if not check:
            row["gaps"] = [{"id": g["id"], "title": g["title"], "coverage": g["coverage"]}
                           for g in (from_checks or {}).get("gaps", [])]
        elif pkg != ADDON and pkg not in hold:
            found = next((c for c in snapshot[pkg].get("checks") or [] if c["id"] == check), None)
            row["coverage"] = "notapplicable" if found is None or found["state"] == "notapplicable" or item in skips \
                else hslib.check_coverage(found, now)["coverage"]
    return report, keys, hold


def coverage_changes(told, coverage, now):
    """(events, entries): what changed in the coverage of what a host's profile expects, and the
    new entries of the memory for its keys ("PKG@", "PKG@CHECK"). told: the memory's entries of
    those keys; coverage: profile_coverage(), or None without a profile (then nothing is told and
    the entries are forgotten).

    A key whose coverage turns to one of LOST is told once, "no longer possible"; back to
    "checked", once more, "possible again"; any other state keeps what was told. An entry is
    {level: lost or ok, coverage, at, last, label, title}, kept for every key the profile
    expects (so an add-on that is gone is still named by its product name); a key that is not
    there was OK. The same change of the same key waits when it was told less than FLAP seconds
    ago, as for items.
    A key the profile no longer expects, or that does not apply now (a skip that runs, a check
    the add-on reports as not applicable), is forgotten without a message, except the checks of
    an add-on that cannot be judged now (hold), which stay as they were."""
    if coverage is None:
        return [], {}
    _, keys, hold = coverage
    events, out = [], {}
    for key in sorted(set(told) | set(keys)):
        entry = told.get(key) if isinstance(told.get(key), dict) else {}
        old = "lost" if entry.get("level") == "lost" else "ok"
        if key not in keys:
            pkg, _, check = key.partition(COVERAGE)
            if check and pkg in hold and entry:
                out[key] = entry
            continue
        cov, label, title, note = keys[key]
        label = label or entry.get("label") or key.partition(COVERAGE)[0]
        new = "lost" if cov in LOST else "ok" if cov == "checked" else old
        last = entry.get("last") if isinstance(entry.get("last"), dict) else {}
        change = old + ">" + new
        if new == old or (isinstance(last.get(change), (int, float)) and now - last[change] < FLAP):
            # unchanged, or a change that waits for the hour against flapping: as told, with what
            # is known now
            out[key] = dict({"at": now, "last": {}}, **entry)
            out[key].update(level=old, coverage=cov, label=label, **({"title": title} if title else {}))
            continue
        out[key] = {"level": new, "coverage": cov, "at": now, "last": dict(last, **{change: now}), "label": label}
        if title or entry.get("title"):
            out[key]["title"] = title or entry["title"]
        events.append(_cevent(key, out[key], note))
    return events, out


def _cevent(key, entry, note):
    """A change of the coverage of an expected add-on or check: "lost" (no longer possible, a
    warning) or "regained" (possible again)."""
    pkg, _, check = key.partition(COVERAGE)
    lost = entry["level"] == "lost"
    return {
        "key": key, "pkg": pkg, "id": "", "check": check, "label": entry["label"], "title": entry.get("title") or check or None,
        "kind": "lost" if lost else "regained", "from": "ok" if lost else "warning", "to": "warning" if lost else "ok",
        "state": "warning" if lost else "ok", "coverage": entry["coverage"], "note": note if lost else None,
        "value": None, "detail": None, "stale_at": None, "why": None, "addon_kind": None, "reason": None, "code": None,
    }


# ---------------------------------------------------------------------------
# What changed
#
# The memory of what was told (`sent` in last.json) is {key: entry}; a key that is not there
# was OK:
#   "PKG#"         the add-on itself (a warning while its status cannot be read)
#   "PKG#ID"       an item; for an item that lists findings only what they do not explain
#   "PKG#ID/FID"   a finding of an item that lists them
#   "PKG@"         an add-on the host's profile expects (coverage_changes())
#   "PKG@CHECK"    a check of it, or a check the profile expects on its own
# An entry is {level, at, last: {"FROM>TO": time}, label, title}. A finding's entry also has
# status (open; accepted: told nothing while accepted; resolved: kept KEEP seconds to tell
# "again") and item (its item's title); the entry of an item that lists findings has
# findings: true. The entry of a coverage key has level "lost" or "ok" and its coverage. The
# keys of 0.1.0 are the first two kinds and are read as before.

STATUSES = ("open", "accepted", "resolved")


def _parts(key):
    """(pkg, item id, fid) of a key; item id and fid are "" where the key has none. Package
    names have no "#", item ids and fids no "/"."""
    pkg, _, rest = key.partition("#")
    iid, _, fid = rest.partition("/")
    return pkg, iid, fid


def _told(entry):
    """The level a key was told at: warning or danger, else ok. An accepted or a resolved
    finding is not told as a problem."""
    if not isinstance(entry, dict) or entry.get("level") not in PROBLEM:
        return "ok"
    return entry["level"] if entry.get("status", "open") == "open" else "ok"


def _known(entry):
    """The level of a finding that is open or accepted: what explains its item's level."""
    if not isinstance(entry, dict) or entry.get("level") not in PROBLEM:
        return "ok"
    return entry["level"] if entry.get("status", "open") in ("open", "accepted") else "ok"


def _worst(levels):
    return max(levels, key=hslib.rank, default="ok")


def _level_of(thing):
    """The level of an item or a finding; UNKNOWN while the add-on cannot judge it (none)."""
    return UNKNOWN if thing["state"] == "none" else level(thing["state"])


def _addon_level(a):
    return "warning" if a["kind"] in ERROR_KINDS else UNKNOWN if a["kind"] == "noaccess" else "ok"


def keys_of(snapshot):
    """{key: (level, item, finding)} of a snapshot, as a first message tells it. An add-on is
    "PKG#" (a warning while its status cannot be read, UNKNOWN while nothing is readable without
    root), each of its items "PKG#ID" (UNKNOWN while the add-on cannot judge it: state none),
    each finding of an item that lists them "PKG#ID/FID" (UNKNOWN for state none). Such an item
    is only what its findings do not explain: OK while an open or accepted finding is at least
    as severe."""
    cur = {}
    for pkg, a in snapshot.items():
        cur[pkg + "#"] = (_addon_level(a), None, None)
        if a["kind"] != "ok":
            continue
        for it in a["items"]:
            key = pkg + "#" + it["id"]
            new = _level_of(it)
            if it.get("findings") is not None:
                for f in it["findings"]["list"]:
                    cur[key + "/" + f["fid"]] = (_level_of(f), it, f)
                cover = _worst(x for x in (_level_of(f) for f in it["findings"]["list"]) if x in PROBLEM)
                if new != UNKNOWN and hslib.rank(new) <= hslib.rank(cover):
                    new = "ok"
            cur[key] = (new, it, None)
    return cur


def changes(sent, snapshot, now, coverage=None):
    """(events, sent): what to tell, and the new memory of what was told (above). A change that
    was told for the same key less than FLAP seconds ago waits (its key keeps the old level),
    so a flapping item or finding gives one message an hour at most. coverage: what the host's
    profile expects and how far it can be checked (profile_coverage()), or None without a
    profile: then the coverage keys are forgotten and nothing about them is told
    (coverage_changes()). A coverage change of an add-on itself ("PKG@") is not told when the
    same run tells about the add-on already (its status is not readable, or readable again; it
    is no longer installed), though it is remembered.

    What cannot be checked is no reason for OK, so these keys keep what was told before: the
    items and findings of an add-on whose status cannot be read or that answers exit 77, an
    item or a finding the add-on cannot judge (state none), an item missing from an answer
    that left items out (more than 50, or broken ones), and a finding missing from a list that
    cannot say it is gone (below). An add-on that is no longer installed while it had told
    problems gives one event "unchecked", and its keys are forgotten.

    An item that lists findings is compared finding by finding. A new finding at warning or
    danger, or a higher state, is told as worse, danger to warning as better. An accepted
    finding is told nothing and remembered as accepted; when its acceptance ends while it is
    still there, that is told once ("expired"). A finding missing from the list is resolved
    only when the list is complete (as the board judges it: the add-on says so and a check of
    state "checked" names the item), that check and the item are in time and the answer left
    no items out; otherwise it keeps what was told. A resolved finding that comes back within
    KEEP seconds is told as "again". When an item that was compared per item (0.1.0 or an
    add-on without findings) lists findings for the first time, those no more severe than what
    was told for the item are taken as told, without a message. An item that lists no findings
    (any more) is compared per item, from the most severe of the findings it told; once it is
    OK, they are resolved with it."""
    sent = sent if isinstance(sent, dict) else {}
    told_coverage = {k: v for k, v in sent.items() if COVERAGE in k}
    sent = {k: v for k, v in sent.items() if COVERAGE not in k}
    events, out, uninstalled = [], {}, {}
    told_findings = {}                 # (pkg, item id) -> {fid: entry}
    for key, entry in sent.items():
        pkg, iid, fid = _parts(key)
        if fid and isinstance(entry, dict):
            told_findings.setdefault((pkg, iid), {})[fid] = entry
    present = {(pkg, it["id"]): it for pkg, a in snapshot.items() if a["kind"] == "ok" for it in a["items"]}
    cover, fold = {}, {}

    # 1. The findings of the items that list them.
    for (pkg, iid), it in sorted(present.items()):
        if it.get("findings") is None:
            continue
        told = told_findings.pop((pkg, iid), {})
        item_key = pkg + "#" + iid
        item_entry = sent.get(item_key) if isinstance(sent.get(item_key), dict) else {}
        adopt = "ok" if told or item_entry.get("findings") else _told(item_entry)
        found = it["findings"]
        complete = found["complete"] and found.get("fresh", True) and it.get("stale_at") is None and \
            not snapshot[pkg].get("dropped")
        listed = {f["fid"]: f for f in found["list"]}
        fids = sorted(set(listed) | set(told))
        for fid in fids:
            entry, event = _finding(item_key + "/" + fid, told.get(fid), listed.get(fid), complete, adopt,
                                    snapshot[pkg], it, now)
            if entry:
                out[item_key + "/" + fid] = entry
            if event:
                events.append(event)
        # What explains the item's level: the findings it lists (also one whose change waits for
        # the hour against flapping) and those still remembered as open or accepted.
        cover[(pkg, iid)] = _worst([_known(out.get(item_key + "/" + fid)) for fid in fids] +
                                   [x for x in (_level_of(f) for f in found["list"]) if x in PROBLEM])

    # 2. Findings told for an item that lists none now: kept as they are; an add-on that is gone
    #    takes them along. Such an item, compared per item again, starts from the most severe.
    for (pkg, iid), told in sorted(told_findings.items()):
        if pkg not in snapshot:
            for fid, entry in sorted(told.items()):
                if _told(entry) in PROBLEM:
                    uninstalled.setdefault(pkg, []).append((iid + "/" + fid, entry))
            continue
        for fid, entry in told.items():
            out[pkg + "#" + iid + "/" + fid] = entry
        if (pkg, iid) in present:
            fold[(pkg, iid)] = _worst(_known(e) for e in told.values())

    # 3. The add-ons and their items.
    cur = {}
    for pkg, a in snapshot.items():
        cur[pkg + "#"] = (_addon_level(a), None)
        if a["kind"] == "ok":
            for it in a["items"]:
                cur[pkg + "#" + it["id"]] = (_level_of(it), it)
    for key in sorted(set(cur) | {k for k in sent if not _parts(k)[2]}):
        pkg, iid, _ = _parts(key)
        old_entry = sent.get(key) if isinstance(sent.get(key), dict) else {}
        old = _told(old_entry)
        if hslib.rank(fold.get((pkg, iid), "ok")) > hslib.rank(old):
            old = fold[(pkg, iid)]
            old_entry = dict({"at": now, "last": {}, "label": snapshot[pkg]["label"],
                              "title": present[(pkg, iid)]["title"]}, **old_entry)
            old_entry["level"] = old
        why = None
        if key in cur:
            new, item = cur[key]
            if new == UNKNOWN:
                if old_entry:
                    out[key] = old_entry
                continue
        elif pkg in snapshot and (snapshot[pkg]["kind"] != "ok" or snapshot[pkg].get("dropped")):
            if old_entry:
                out[key] = old_entry
            continue
        elif pkg in snapshot:
            new, item, why = "ok", None, "gone"
        else:
            if old in PROBLEM:
                uninstalled.setdefault(pkg, []).append((iid, old_entry))
            continue
        # What is kept: an item that lists findings keeps only what they do not explain.
        store = new
        if (pkg, iid) in cover and hslib.rank(new) <= hslib.rank(cover[(pkg, iid)]):
            store = "ok"
        if store == old or hslib.rank(store) < hslib.rank(old) <= hslib.rank(new):
            # unchanged, or what was told for the item is explained by its findings now
            if store in PROBLEM or _recent(old_entry, now):
                out[key] = dict(old_entry, level=store)
            continue
        change = old + ">" + new
        last = old_entry.get("last") if isinstance(old_entry.get("last"), dict) else {}
        if isinstance(last.get(change), (int, float)) and now - last[change] < FLAP:
            if old_entry:
                out[key] = old_entry
            continue
        out[key] = {"level": store, "at": now, "last": dict(last, **{change: now})}
        events.append(_event(key, snapshot.get(pkg) or {}, item, old, new, why, old_entry))
        out[key]["label"] = events[-1]["label"]
        if events[-1]["title"]:
            out[key]["title"] = events[-1]["title"]

    # An item's entry says whether it was compared finding by finding.
    for (pkg, iid) in present:
        key = pkg + "#" + iid
        if key in out and bool(out[key].get("findings")) != ((pkg, iid) in cover):
            out[key] = dict(out[key], findings=True) if (pkg, iid) in cover else \
                {k: v for k, v in out[key].items() if k != "findings"}

    # An item compared per item that is OK now resolves the findings it told, without a message.
    for (pkg, iid) in fold:
        if _level_of(present[(pkg, iid)]) == "ok" and _told(out.get(pkg + "#" + iid)) == "ok":
            prefix = pkg + "#" + iid + "/"
            for key in [k for k in out if k.startswith(prefix) and out[k].get("status") in ("open", "accepted")]:
                out[key] = dict(out[key], level="ok", status="resolved", at=now)

    events.extend(_unchecked(pkg, entries) for pkg, entries in sorted(uninstalled.items()))
    told_addons = {e["pkg"] for e in events if not e["id"]}
    cov_events, cov_out = coverage_changes(told_coverage, coverage, now)
    out.update(cov_out)
    events.extend(e for e in cov_events if e["check"] or e["pkg"] not in told_addons)
    order = {"worse": 0, "again": 0, "expired": 0, "unchecked": 1, "lost": 1, "better": 2, "resolved": 3, "regained": 3}
    events.sort(key=lambda e: (order[e["kind"]], -hslib.rank(e["to"]), e["label"].lower(), e["key"]))
    return events, _prune(out, now)


def _finding(key, told, f, complete, adopt, a, item, now):
    """One finding of an item that lists findings: (its entry in the memory or None, its event or
    None). told: its entry so far (None when never told); f: the finding in the answer (None
    when it is missing from the list); complete: whether a missing finding is gone; adopt: what
    was told for the item while it was compared per item (findings no more severe are taken as
    told), else ok; a: the add-on; item: the item."""
    told = told if isinstance(told, dict) and told.get("status") in STATUSES else {}
    status = told.get("status")
    old = told["level"] if status in ("open", "accepted") and told.get("level") in PROBLEM else "ok"
    why = None
    if f is not None:
        lvl = _level_of(f)
        if lvl == UNKNOWN:
            return told or None, None
        if lvl in PROBLEM and f["accepted"]:
            if status == "accepted" and old == lvl:
                return told, None
            return _quiet(key, told, "accepted", lvl, now, a, item, f), None
        if lvl in PROBLEM:
            if status == "open" and old == lvl:
                return told, None
            if status == "open":
                kind = "worse" if hslib.rank(lvl) > hslib.rank(old) else "better"
            elif status == "accepted":
                kind = "expired"
            elif status == "resolved":
                kind = "again"
            elif hslib.rank(lvl) <= hslib.rank(adopt):
                return _quiet(key, told, "open", lvl, now, a, item, f), None
            else:
                kind = "worse"
            new_status, new = "open", lvl
        elif status == "open":
            # the add-on rates it OK (or a note) now
            kind, new_status, new = "resolved", "resolved", "ok"
        elif status == "accepted":
            return _quiet(key, told, "resolved", "ok", now, a, item, f), None
        else:
            return told or None, None
    elif complete and status == "open":
        kind, new_status, new, why = "resolved", "resolved", "ok", "fixed"
    elif complete and status == "accepted":
        return _quiet(key, told, "resolved", "ok", now, a, item, None), None
    else:
        # resolved already, or missing from a list that cannot say it is gone: as it was
        return told or None, None
    change = ("accepted" if status == "accepted" else old) + ">" + new
    last = told.get("last") if isinstance(told.get("last"), dict) else {}
    if isinstance(last.get(change), (int, float)) and now - last[change] < FLAP:
        return told or None, None
    event = _fevent(key, a, item, f, told, kind, old, new, why)
    entry = _quiet(key, told, new_status, new, now, a, item, f)
    entry["last"] = dict(last, **{change: now})
    return entry, event


def _names(key, a, item, f, told):
    """(label, title, item title) of a finding: from the answer, else from what was told."""
    return (a.get("label") or told.get("label") or _parts(key)[0],
            (f or {}).get("title") or told.get("title") or _parts(key)[2],
            (item or {}).get("title") or told.get("item"))


def _quiet(key, told, status, lvl, now, a, item, f):
    """A finding's new entry in the memory (a change without a message keeps its `last`)."""
    label, title, item_title = _names(key, a, item, f, told)
    entry = {"level": lvl, "status": status, "at": now,
             "last": told.get("last") if isinstance(told.get("last"), dict) else {}, "label": label, "title": title}
    if item_title:
        entry["item"] = item_title
    return entry


def _fevent(key, a, item, f, told, kind, old, new, why=None):
    """One change of a finding (the item it belongs to, the finding or None when it is missing)."""
    pkg, iid, fid = _parts(key)
    label, title, item_title = _names(key, a, item, f, told or {})
    return {
        "key": key, "pkg": pkg, "id": iid, "fid": fid, "label": label, "title": title, "item": item_title,
        "kind": kind, "from": old, "to": new, "state": f["state"] if f else new,
        "value": None, "detail": None, "stale_at": (item or {}).get("stale_at"), "why": why,
        "addon_kind": None, "reason": None, "code": None,
    }


def _prune(out, now):
    """Resolved findings are forgotten after KEEP seconds, and at most MAX_KEYS keys stay: what
    was told as a problem first, then accepted findings, then the rest (OK keys kept against
    flapping, resolved findings), each the newest first."""
    def at(e):
        return e["at"] if isinstance(e.get("at"), (int, float)) else 0

    kept = {k: e for k, e in out.items() if not (e.get("status") == "resolved" and now - at(e) >= KEEP)}
    if len(kept) > MAX_KEYS:
        def weight(kv):
            k, e = kv
            told = _told(e) in PROBLEM or e.get("level") == "lost"
            return (0 if told else 1 if e.get("status") == "accepted" else 2, -at(e), k)
        kept = dict(sorted(kept.items(), key=weight)[:MAX_KEYS])
    return kept


def _unchecked(pkg, told):
    """An add-on that is no longer installed, from what was told about it ([(item id or "",
    entry in `sent`)], each with a problem level): one event for the add-on, a warning, since
    nothing checks those findings any more. `open` counts the findings among them."""
    label = next((e["label"] for _, e in told if isinstance(e.get("label"), str) and e["label"]), pkg)
    return {
        "key": pkg + "#", "pkg": pkg, "id": "", "label": label, "title": None, "kind": "unchecked",
        "from": max((e["level"] for _, e in told), key=hslib.rank), "to": "warning", "state": "warning",
        "value": None, "detail": None, "stale_at": None, "why": None,
        "addon_kind": None, "reason": None, "code": None,
        "open": sum(1 for iid, _ in told if iid),
    }


def _event(key, a, item, old, new, why=None, told=None):
    """One change: the key's add-on (its snapshot entry, or {} when it is gone), the item (None
    for the add-on itself or an item that is gone), the levels before and now."""
    pkg, _, iid = key.partition("#")
    told = told or {}
    item = item or {}
    return {
        "key": key, "pkg": pkg, "id": iid, "label": a.get("label") or told.get("label") or pkg,
        "title": item.get("title") or told.get("title"),
        "kind": "worse" if hslib.rank(new) > hslib.rank(old) else ("resolved" if new == "ok" else "better"),
        "from": old, "to": new, "state": item.get("state", new),
        "value": item.get("value"), "detail": item.get("detail"), "stale_at": item.get("stale_at"), "why": why,
        # for the add-on itself: why its status cannot be read
        "addon_kind": None if iid else a.get("kind"), "reason": None if iid else a.get("reason"),
        "code": None if iid else a.get("code"),
    }


def _recent(entry, now):
    last = entry.get("last") if isinstance(entry.get("last"), dict) else {}
    return any(isinstance(v, (int, float)) and now - v < FLAP for v in last.values())


def baseline(snapshot, now):
    """The memory after the first message (the summary): every problem as told, an accepted
    finding as accepted."""
    out = {}
    for key, (lvl, item, f) in keys_of(snapshot).items():
        if lvl in PROBLEM:
            pkg = _parts(key)[0]
            out[key] = {"level": lvl, "at": now, "last": {}, "label": snapshot[pkg]["label"]}
            if f:
                out[key].update(status="accepted" if f["accepted"] else "open", title=f["title"], item=item["title"])
            elif item:
                out[key]["title"] = item["title"]
                if item.get("findings") is not None:
                    out[key]["findings"] = True
    return out


# ---------------------------------------------------------------------------
# Messages

def _unreadable(lang, e):
    """Why the status of an add-on cannot be read, or None."""
    reason = e.get("reason") if e.get("addon_kind") == "invalid" else e.get("addon_kind")
    key = "reason." + str(reason)
    return t(lang, key, code=e.get("code")) if key in T["en"] else None


def _head(lang, e):
    """What happened: the state now (worse, again, expired), the state instead of the one before
    (better), or OK again."""
    if e["kind"] in ("worse", "again", "expired"):
        return t(lang, "head." + e["kind"], state=t(lang, "state." + e["to"]))
    if e["kind"] == "better":
        return t(lang, "head.better", state=t(lang, "state." + e["to"]), before=t(lang, "state." + e["from"]))
    return t(lang, "head.resolved")


def _line(lang, e):
    """One change as two lines: what and where, then the detail. A finding: what happened, the
    add-on and the finding, then its item (and why it is gone, or since when it is out of date)."""
    if e["kind"] == "unchecked":
        first = f"{t(lang, 'head.unchecked')} · {e['label']} · {t(lang, 'unchecked.why')}"
        n = e.get("open") or 0
        return first + ("\n" + t(lang, "unchecked.one" if n == 1 else "unchecked.other", n=n) if n else "")
    if e["kind"] in ("lost", "regained"):
        # An expected add-on or check: what happened, the add-on and the check, then why.
        first = f"{t(lang, 'head.' + e['kind'])} · {e['label']}" + (f" · {e['title']}" if e["title"] else "")
        if e["kind"] == "regained":
            return first
        return first + "\n" + t(lang, "cov." + e["coverage"]) + (f": {e['note']}" if e.get("note") else "")
    resolved = e["kind"] == "resolved"
    head = _head(lang, e)
    if e.get("fid"):
        second = e["item"]
        if resolved and e["why"]:
            second = (second + ": " if second else "") + t(lang, "why." + e["why"])
        elif not resolved and e["stale_at"]:
            second = (second + "; " if second else "") + t(lang, "stale", when=when(lang, e["stale_at"]))
        return f"{head} · {e['label']} · {e['title']}" + ("\n" + second if second else "")
    if e["id"]:
        title, detail = e["title"] or e["id"], e["detail"]
    elif resolved:
        title, detail = (t(lang, "unreadable") if e["why"] else t(lang, "readable")), None
    else:
        title, detail = t(lang, "unreadable"), _unreadable(lang, e)
    first = f"{head} · {e['label']} · {title}" + (f" ({e['value']})" if e["value"] and not resolved else "")
    if resolved:
        second = t(lang, "why." + e["why"]) if e["why"] else None
    elif e["stale_at"]:
        second = t(lang, "stale", when=when(lang, e["stale_at"])) + (f"; {detail}" if detail else "")
    else:
        second = detail
    return first + ("\n" + second if second else "")


def _fit(lang, head, lines, tail):
    """head, as many lines as fit into BODY_MAX bytes (MAX_LINES at most), "and N more", tail."""
    body, used = [], len(((head or "") + tail).encode("utf-8")) + 64
    for i, line in enumerate(lines):
        size = len(line.encode("utf-8")) + 2
        if i >= MAX_LINES or used + size > BODY_MAX:
            body.append(t(lang, "more", n=len(lines) - i))
            break
        body.append(line)
        used += size
    return "\n\n".join(x for x in [head, "\n\n".join(body), tail] if x)


def problems_of(snapshot):
    """The problems of a snapshot as changes from OK, the most severe first; an item that lists
    findings by its open ones (accepted findings are not told)."""
    out = []
    for key, (lvl, item, f) in keys_of(snapshot).items():
        if lvl not in PROBLEM or (f and f["accepted"]):
            continue
        a = snapshot[_parts(key)[0]]
        out.append(_fevent(key, a, item, f, None, "worse", "ok", lvl) if f else _event(key, a, item, "ok", lvl))
    out.sort(key=lambda e: (-hslib.rank(e["to"]), e["label"].lower(), e["key"]))
    return out


def message(lang, host, events=None, snapshot=None, summary=False, gaps=()):
    """{title, body, priority, tags} of the message for events, or of the summary of a
    snapshot (the first message after setting up), which names the gaps in what the host's
    profile expects as well (gaps: events of coverage_changes())."""
    footer = t(lang, "footer", host=host)
    if summary:
        found = problems_of(snapshot or {}) + sorted(gaps, key=lambda e: (e["label"].lower(), e["key"]))
        intro = t(lang, "summary.intro") + "\n\n"
        if found:
            head = intro + t(lang, "summary.problems")
        elif snapshot:
            head = intro + t(lang, "summary.clean", n=len(snapshot))
        else:
            head = intro + t(lang, "summary.none")
        top = max((e["to"] for e in found), key=hslib.rank) if found else "ok"
        return {"title": t(lang, "title.summary", host=host), "body": _fit(lang, head, [_line(lang, e) for e in found],
                                                                           footer),
                "priority": PRIORITY[top] if found else 3, "tags": [TAGS[top]]}
    if len(events) == 1 and events[0]["kind"] in ("lost", "regained"):
        title = t(lang, "title." + events[0]["kind"], host=host, label=events[0]["label"])
    elif len(events) == 1 and events[0]["kind"] == "unchecked":
        title = t(lang, "title.unchecked", host=host, label=events[0]["label"])
    elif len(events) == 1:
        e = events[0]
        what = e["title"] if e["id"] and e["title"] else t(lang, "readable" if e["kind"] == "resolved" and
                                                             not e["why"] and not e["id"] else "unreadable")
        if e["id"] and not e["title"]:
            what = e["id"]
        title = t(lang, "title.one", host=host, label=e["label"], title=what)
    else:
        title = t(lang, "title.many", host=host, n=len(events))
    top = max((e["to"] for e in events), key=hslib.rank) if events else "ok"
    return {"title": title, "body": _fit(lang, None, [_line(lang, e) for e in events], footer),
            "priority": PRIORITY[top], "tags": [TAGS[top]]}


def test_message(lang, host):
    return {"title": t(lang, "title.test", host=host), "body": t(lang, "test.body", host=host) + "\n\n" +
            t(lang, "footer", host=host), "priority": 3, "tags": ["bell"]}


def probe_message(lang, host):
    return {"title": t(lang, "title.probe", host=host), "body": t(lang, "probe.body", host=host),
            "priority": 4, "tags": ["warning"]}


# ---------------------------------------------------------------------------
# The channels

class NetError(Exception):
    """A request that got no HTTP answer: reason is "tls", "timeout" or "network"."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, never followed: it could take the token to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http(method, url, body=None, headers=None, timeout=HTTP_TIMEOUT, limit=65536):
    """One HTTP request: (status, body bytes); raises NetError when there is no answer."""
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(
        context=ssl.create_default_context()))
    h = {"User-Agent": "cockpit-host-settings"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read(limit)
    except urllib.error.HTTPError as e:
        try:
            data = e.read(limit)
        except OSError:
            data = b""
        return e.code, data
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, ssl.SSLError):
            raise NetError("tls") from None
        if isinstance(reason, socket.timeout):
            raise NetError("timeout") from None
        raise NetError("network") from None
    except ssl.SSLError:
        raise NetError("tls") from None
    except socket.timeout:
        raise NetError("timeout") from None
    except (OSError, ValueError):
        raise NetError("network") from None


def _publish(cfg, msg, token, request):
    doc = {"topic": cfg["ntfy_topic"], "title": msg["title"], "message": msg["body"], "priority": msg["priority"],
           "tags": msg["tags"]}
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return request("POST", cfg["ntfy_url"] + "/", json.dumps(doc, ensure_ascii=False).encode("utf-8"), headers)


def _outcome(code):
    if 200 <= code < 300:
        return True, "sent"
    if code in (401, 403):
        return False, "rejected"
    if code == 429:
        return False, "rate-limited"
    if code == 413:
        return False, "too-large"
    return False, f"http-{code}"


def send_ntfy(cfg, msg, token, request=http):
    """{ok, code, reason} of publishing msg to the topic (JSON to the server's root URL)."""
    try:
        code, _ = _publish(cfg, msg, token, request)
    except NetError as e:
        return {"ok": False, "code": None, "reason": e.reason}
    ok, reason = _outcome(code)
    return {"ok": ok, "code": code, "reason": reason}


def mail_from(fqdn):
    return "cockpit-host-settings@" + (fqdn or "localhost")


def send_mail(cfg, msg, fqdn, smtp=None):
    """{ok, code, reason} of handing msg to the local MTA by SMTP on localhost:25."""
    m = EmailMessage()
    m["Subject"] = msg["title"]
    m["From"] = "Host Settings <" + mail_from(fqdn) + ">"
    m["To"] = cfg["mail_to"]
    m["Date"] = formatdate(usegmt=True)
    m["Message-ID"] = make_msgid(domain=fqdn or "localhost")
    m["Auto-Submitted"] = "auto-generated"
    m.set_content(msg["body"], charset="utf-8")
    factory = smtp or (lambda: smtplib.SMTP("localhost", 25, local_hostname=fqdn or None, timeout=HTTP_TIMEOUT))
    try:
        with factory() as s:
            s.send_message(m)
    except ConnectionRefusedError:
        return {"ok": False, "code": None, "reason": "no-mta"}
    except smtplib.SMTPRecipientsRefused:
        return {"ok": False, "code": None, "reason": "refused"}
    except smtplib.SMTPResponseException as e:
        return {"ok": False, "code": e.smtp_code, "reason": "refused" if 500 <= e.smtp_code < 600 else "smtp"}
    except smtplib.SMTPException:
        return {"ok": False, "code": None, "reason": "smtp"}
    except socket.timeout:
        return {"ok": False, "code": None, "reason": "timeout"}
    except OSError:
        return {"ok": False, "code": None, "reason": "network"}
    return {"ok": True, "code": None, "reason": "sent"}


# ---------------------------------------------------------------------------
# "Check connection" (concept 3.6)

STEPS = ("reach", "tls", "publish", "anon-read", "anon-write", "token")
# Reading a topic without waiting: the messages of the last minute, which are thrown away. Not
# since=latest: ntfy 2.11 answers it with 400 (job e2e, on a topic open to reading).
READ_PROBE = "poll=1&since=1m"


def check_ntfy(cfg, token, lang, host, request=http, steps=STEPS):
    """[{id, result, code, reason}] for the steps asked for, in their order; result is ok,
    warning, info, failed or skipped. "anon-write" publishes a message without a login, which
    is the warning itself should it be accepted: only on the page's explicit request."""
    out = []
    base, topic = cfg["ntfy_url"], cfg["ntfy_topic"]

    def step(sid, result, reason=None, code=None):
        out.append({"id": sid, "result": result, "code": code, "reason": reason})

    reached = True
    if "reach" in steps:
        try:
            code, _ = request("GET", base + "/v1/health")
            step("reach", "ok", "answered", code)
        except NetError as e:
            step("reach", "failed", e.reason)
            reached = False
    if "tls" in steps:
        step("tls", "ok" if base.startswith("https://") else "warning", "https" if base.startswith("https://")
             else "unencrypted")
    for sid in ("publish", "anon-read", "anon-write", "token"):
        if sid not in steps:
            continue
        if not reached:
            step(sid, "skipped", "unreachable")
            continue
        try:
            if sid == "publish":
                code, _ = _publish(cfg, test_message(lang, host), token, request)
                ok, reason = _outcome(code)
                step(sid, "ok" if ok else "failed", "no-token" if reason == "rejected" and not token else reason,
                     code)
            elif sid == "anon-read":
                code, _ = request("GET", f"{base}/{topic}/json?" + READ_PROBE)
                if 200 <= code < 300:
                    step(sid, "warning", "readable", code)
                elif code in (401, 403):
                    step(sid, "ok", "protected", code)
                else:
                    step(sid, "info", "unknown", code)
            elif sid == "anon-write":
                code, _ = _publish(cfg, probe_message(lang, host), None, request)
                if 200 <= code < 300:
                    step(sid, "warning", "writable", code)
                elif code in (401, 403):
                    step(sid, "ok", "protected", code)
                else:
                    step(sid, "info", "unknown", code)
            elif sid == "token":
                # Whether the token may read the topic as well. /v1/account does not list what
                # `ntfy access` grants (the job e2e showed that), so the token tries to read.
                if not token:
                    step(sid, "skipped", "no-token")
                    continue
                code, _ = request("GET", f"{base}/{topic}/json?" + READ_PROBE, None,
                                  {"Authorization": "Bearer " + token})
                if 200 <= code < 300:
                    step(sid, "info", "can-read", code)
                elif code in (401, 403):
                    step(sid, "ok", "write-only", code)
                else:
                    step(sid, "info", "unknown", code)
        except NetError as e:
            step(sid, "failed" if sid == "publish" else "info", e.reason)
    return out


def access_of(steps, before=None):
    """The protection of the topic from the steps of a check: warning when it is readable or
    writable without a login, ok when both are closed, none when not known. A check without
    the write step keeps the write result of the last full check (`before`), and a step that
    found nothing out keeps the result before it."""
    found = {s["id"]: s["result"] for s in steps}
    before = before if isinstance(before, dict) else {}
    known = ("ok", "warning")
    read, write = found.get("anon-read"), found.get("anon-write")
    if read not in known and before.get("anon_read") in known:
        read = before["anon_read"]
    if write not in known and before.get("anon_write") in known:
        write = before["anon_write"]
    if "warning" in (read, write):
        state = "warning"
    elif read == "ok" and write == "ok":
        state = "ok"
    else:
        state = "none"
    return {"state": state, "anon_read": read, "anon_write": write}
