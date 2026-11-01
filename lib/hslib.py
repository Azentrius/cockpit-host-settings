# SPDX-License-Identifier: Apache-2.0
"""Host Settings, the Cockpit add-on: the rules the notifier and the page's
helper share.

Lives in /usr/lib/cockpit-host-settings (root-owned). Standard library only,
no side effects on import; nothing here runs a command or uses the network.

- The status contract of the Azentrius add-ons (schema 1), read exactly as
  Status Board reads it (cockpit-status-board, src/lib/status.js): every
  answer is checked, control, zero-width and bidirectional characters go,
  every text is cut to the board's limits, and an item past its stale_after
  counts as a warning. The optional parts (features, checks, findings, a
  host's profile) are read as validateExtension() reads them, and an item is
  never better than its open findings (itemState()). test/notify_test.py
  holds these rules against the board's own fixtures (test/fixtures/board,
  tools/board-fixtures.sha256).
- What the board knows about an add-on (its coverage), as the board's
  coverageOf(), checksCoverage() and checkCoverage() judge it, and the checks
  a host's profile names that an answer does not report
  (notReportedChecks()).
- The settings file /etc/cockpit-host-settings/notify.conf.
- A host's target profile, /etc/cockpit-host-settings/profile.conf, and the
  catalog of roles.
"""

import json
import math
import re
from datetime import datetime, timedelta, timezone

STATES = ("none", "ok", "info", "warning", "danger")
RANK = {s: i for i, s in enumerate(STATES)}

# As the board: a package folder below /usr/share/cockpit, an item id, a place inside the
# add-on's page, the status command of a manifest. Python's $ also matches before a final
# line break, so every pattern is used with fullmatch.
PACKAGE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
ITEM_ID_RE = re.compile(r"[a-z0-9-]{1,40}")
LINK_RE = re.compile(r"#/[A-Za-z0-9/_-]{0,80}")
COMMAND_RE = re.compile(r"/usr/lib/cockpit-[a-z0-9][a-z0-9-]{0,40}(?:/[A-Za-z0-9_][A-Za-z0-9._-]{0,80}){1,3}")
TIME_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?(\.[0-9]{1,9})?"
                     r"(Z|[+-][0-9]{2}:[0-9]{2})")

LIMITS = {"label": 60, "version": 40, "summary": 200, "title": 80, "value": 40, "detail": 200, "reason": 300}
MAX_ITEMS = 50
MAX_PACKAGES = 200
MAX_OUTPUT = 64 * 1024
MAX_STALE_AFTER = 10 * 366 * 86400

# Removed from every text, as the board's INVISIBLE_RE: control characters other than tab and
# line breaks (those become spaces), the soft hyphen, the bidirectional controls and the
# zero-width characters. Built from code points, so this file holds none of them itself.
_INVISIBLE = ((0x00, 0x08), (0x0B, 0x0C), (0x0E, 0x1F), (0x7F, 0x9F), (0xAD, 0xAD), (0x61C, 0x61C),
              (0x180E, 0x180E), (0x200B, 0x200F), (0x2028, 0x202E), (0x2060, 0x206F), (0xFEFF, 0xFEFF),
              (0xFFF9, 0xFFFB))
INVISIBLE_RE = re.compile("[" + "".join(re.escape(chr(a)) + ("-" + re.escape(chr(b)) if b != a else "")
                                        for a, b in _INVISIBLE) + "]")
_BREAKS_RE = re.compile("[\t\n\r]+")
_SPACES_RE = re.compile(" {2,}")
ELLIPSIS = "…"


def rank(state):
    return RANK.get(state, 0) if isinstance(state, str) else 0


def worse(a, b):
    return b if rank(b) > rank(a) else a


def is_state(s):
    return isinstance(s, str) and s in RANK


def clean_text(value, limit):
    """A text from an add-on as it may be shown or sent, or None when there is none."""
    if not isinstance(value, str):
        return None
    s = _BREAKS_RE.sub(" ", value)
    s = INVISIBLE_RE.sub("", s)
    s = _SPACES_RE.sub(" ", s).strip()
    if not s:
        return None
    if len(s) > limit:
        s = s[:limit - 1].rstrip() + ELLIPSIS
    return s


def parse_time(value):
    """An ISO 8601 time with a zone ("2026-10-19T18:36:00Z") as seconds since the epoch, or None."""
    if not isinstance(value, str):
        return None
    m = TIME_RE.fullmatch(value)
    if not m:
        return None
    y, mo, d, h, mi, s, frac, zone = m.groups()
    try:
        tz = timezone.utc
        if zone != "Z":
            sign = 1 if zone[0] == "+" else -1
            tz = timezone(sign * timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])))
        at = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=tz)
    except ValueError:
        return None
    # As Date.parse: milliseconds at most.
    return at.timestamp() + (int((frac[1:] + "000")[:3]) / 1000 if frac else 0)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_int(v):
    return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and math.isfinite(v)
                                                                and v.is_integer())


def _js_number(v):
    """String(v) of a JSON number, as JavaScript writes it."""
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e21:
        return str(int(v))
    return str(v) if isinstance(v, int) else repr(v)


# ---------------------------------------------------------------------------
# The status contract, version 1 (as validateStatus and evaluate of the board)

def _reject_constant(name):
    raise ValueError(name)


def validate_item(raw):
    if not isinstance(raw, dict):
        return None
    iid = raw.get("id")
    if not isinstance(iid, str) or not ITEM_ID_RE.fullmatch(iid):
        return None
    if not is_state(raw.get("state")):
        return None
    title = clean_text(raw.get("title"), LIMITS["title"])
    if not title:
        return None
    value = raw.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        value = _js_number(value)
    link = raw.get("link")
    stale = raw.get("stale_after")
    return {
        "id": iid,
        "state": raw["state"],
        "title": title,
        "value": clean_text(value, LIMITS["value"]),
        "detail": clean_text(raw.get("detail"), LIMITS["detail"]),
        "link": _link_of(link),
        "checked_at": parse_time(raw.get("checked_at")),
        "stale_after": _stale_after_of(stale),
        "findings": None,
        "listing": "none",
    }


def _link_of(v):
    return v if isinstance(v, str) and LINK_RE.fullmatch(v) else None


def _stale_after_of(v):
    return int(v) if _is_int(v) and 0 < v <= MAX_STALE_AFTER else None


# ---------------------------------------------------------------------------
# The optional parts of version 1: checks, findings, a host's profile (as validateExtension of
# the board). Only what an add-on declares in `features` is read; what breaks the rules is left
# out and counted, it never makes the answer unreadable.

FEATURES = ("checks", "findings", "profile")
CHECK_STATES = ("checked", "partial", "failed", "unconfigured", "noaccess", "unsupported", "notapplicable")
# A finding's id: the add-on's rule and the resource ("port.5432-tcp"), never a text, a count or
# a time, so it stays the same in every language and order.
FID_RE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,63}")
EXPECT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,40}(?:/[a-z0-9-]{1,40})?")
ROLE_RE = re.compile(r"[a-z0-9-]{1,40}")
EXPOSURES = ("internet", "restricted", "internal", "local")
FIXES = ("available", "none", "unknown")
MAX_CHECKS = 50
MAX_FINDINGS = 100                 # per answer, over all its items
MAX_ROLES = 10
MAX_EXPECT = 100
MAX_SKIP = 50
MAX_ACCEPT = 90 * 86400            # an acceptance counts for at most 90 days after the check
MAX_TOTAL = 1000000


def validate_check(raw, item_ids):
    """One check of `checks`, or None. item: the id of the item that shows its result, kept only
    when the answer has that item."""
    if not isinstance(raw, dict):
        return None
    cid = raw.get("id")
    if not isinstance(cid, str) or not ITEM_ID_RE.fullmatch(cid):
        return None
    state = raw.get("state")
    if not isinstance(state, str) or state not in CHECK_STATES:
        return None
    title = clean_text(raw.get("title"), LIMITS["title"])
    if not title:
        return None
    item = raw.get("item")
    return {
        "id": cid,
        "state": state,
        "title": title,
        "item": item if isinstance(item, str) and item in item_ids else None,
        "checked_at": parse_time(raw.get("checked_at")),
        "stale_after": _stale_after_of(raw.get("stale_after")),
        "reason": clean_text(raw.get("reason"), LIMITS["detail"]),
        "link": _link_of(raw.get("link")),
    }


def validate_finding(raw, ref):
    """One finding of an item, or None. ref: the time of its check (seconds), which an
    acceptance may not run more than MAX_ACCEPT beyond; without it no acceptance counts."""
    if not isinstance(raw, dict):
        return None
    fid = raw.get("fid")
    if not isinstance(fid, str) or not FID_RE.fullmatch(fid):
        return None
    if not is_state(raw.get("state")):
        return None
    title = clean_text(raw.get("title"), LIMITS["title"])
    if not title:
        return None
    until = parse_time(raw.get("accepted_until"))
    exposure, fix = raw.get("exposure"), raw.get("fix")
    return {
        "fid": fid,
        "state": raw["state"],
        "title": title,
        "since": parse_time(raw.get("since")),
        "exposure": exposure if isinstance(exposure, str) and exposure in EXPOSURES else None,
        "fix": fix if isinstance(fix, str) and fix in FIXES else None,
        "exploited": "known" if raw.get("exploited") == "known" else None,
        "accepted_until": until if until is not None and ref is not None and until <= ref + MAX_ACCEPT else None,
        "link": _link_of(raw.get("link")),
    }


def validate_profile(raw, drop):
    """The profile of a host (only Host Settings sends one): {roles, expect, skip, set_at}, or
    None. Entries that break the rules, come twice or go past the limits are left out and
    counted in drop["n"]."""
    if not isinstance(raw, dict):
        return None

    def strings(v, pattern, limit):
        every = v if isinstance(v, list) else []
        kept = list(dict.fromkeys(x for x in every if isinstance(x, str) and pattern.fullmatch(x)))[:limit]
        drop["n"] += len(every) - len(kept)
        return kept

    skip, seen = [], set()
    raw_skip = raw.get("skip") if isinstance(raw.get("skip"), list) else []
    for s in raw_skip:
        check = s.get("check") if isinstance(s, dict) else None
        if len(skip) >= MAX_SKIP or not isinstance(check, str) or not EXPECT_RE.fullmatch(check) or check in seen:
            continue
        seen.add(check)
        skip.append({"check": check, "reason": clean_text(s.get("reason"), LIMITS["detail"]),
                     "until": parse_time(s.get("until"))})
    drop["n"] += len(raw_skip) - len(skip)
    return {"roles": strings(raw.get("roles"), ROLE_RE, MAX_ROLES),
            "expect": strings(raw.get("expect"), EXPECT_RE, MAX_EXPECT), "skip": skip,
            "set_at": parse_time(raw.get("set_at"))}


LISTINGS = ("listed", "broken", "none")


def validate_extension(data, status, raws):
    """The optional parts of a valid answer, set on status: features, checks, profile,
    ext_dropped, and each item's findings ({complete, total, list} or None) and listing. raws
    are the answer's items in the order of status["items"].

    listing says why an item's findings are what they are (Host Settings only; the board does
    not need it): "listed" for a findings object that can be read, "broken" for an item that
    sends `findings` the rules leave out (the add-on does not declare the feature, or it is no
    object, `complete` is no boolean, `list` is no list), "none" for an item that sends none (an
    add-on that never lists findings, an older version of one, or an item without findings).
    The notifier keeps what it told about the findings of an item that lists none and tells a
    broken one nothing better (hsnotify.changes())."""
    for item, raw_item in zip(status["items"], raws):
        item["listing"] = "broken" if "findings" in raw_item else "none"
    declared = data.get("features")
    features = [f for f in FEATURES if f in declared] if isinstance(declared, list) else []
    drop = {"n": 0}
    checks = None
    if "checks" in features:
        if isinstance(data.get("checks"), list):
            checks, ids = [], set()
            item_ids = {i["id"] for i in status["items"]}
            for raw in data["checks"]:
                c = validate_check(raw, item_ids) if len(checks) < MAX_CHECKS else None
                if not c or c["id"] in ids:
                    drop["n"] += 1
                    continue
                ids.add(c["id"])
                checks.append(c)
        else:
            drop["n"] += 1
    if "findings" in features:
        # complete only where a check of state "checked" shows the item's result
        owner = {c["item"]: c for c in (checks or []) if c["item"] is not None}
        fids, listed = set(), 0
        for item, raw_item in zip(status["items"], raws):
            if "findings" not in raw_item:
                continue
            raw = raw_item["findings"]
            if not isinstance(raw, dict) or not isinstance(raw.get("complete"), bool) or \
                    not isinstance(raw.get("list"), list):
                drop["n"] += 1
                continue
            ref = item["checked_at"] if item["checked_at"] is not None else status["checked_at"]
            found, complete = [], raw["complete"]
            for r in raw["list"]:
                f = validate_finding(r, ref) if listed < MAX_FINDINGS else None
                if not f or f["fid"] in fids:
                    drop["n"] += 1
                    complete = False
                    continue
                fids.add(f["fid"])
                found.append(f)
                listed += 1
            check = owner.get(item["id"])
            if not check or check["state"] != "checked":
                complete = False
            total = raw.get("total")
            # the answer itself counts findings that are not in the list ("complete": true,
            # "total": 1, "list": []); so does a total past MAX_TOTAL, which is not kept
            if _is_int(total) and total > len(found):
                complete = False
            total = int(total) if _is_int(total) and len(found) <= total <= MAX_TOTAL else len(found)
            item["findings"] = {"complete": complete, "total": total, "list": found}
            item["listing"] = "listed"
    profile = None
    if "profile" in features and data.get("addon") == "host-settings":
        profile = validate_profile(data.get("profile"), drop)
        if profile is None:
            drop["n"] += 1
    status.update(features=features, checks=checks, profile=profile, ext_dropped=drop["n"])
    return status


def validate_status(output, pkg=None):
    """The output of `status --lang xx` of the package pkg (text): (True, status) with every
    field checked and cut, or (False, (reason, detail)). Unknown fields are ignored; an item
    that breaks the rules is left out (status["dropped"] counts them), and so is what breaks
    the rules of the optional parts (status["ext_dropped"])."""
    if not isinstance(output, str):
        return False, ("not-text", None)
    if len(output) > MAX_OUTPUT:
        return False, ("too-large", None)
    try:
        data = json.loads(output, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return False, ("not-json", None)
    if not isinstance(data, dict):
        return False, ("not-object", None)
    schema = data.get("schema")
    if not (_is_int(schema) and schema == 1):
        detail = None
        if isinstance(schema, (int, float)) and not isinstance(schema, bool) and math.isfinite(schema):
            detail = clean_text(_js_number(schema), 12)
        elif isinstance(schema, str):
            detail = clean_text(schema, 12)
        return False, ("schema", detail)
    addon = data.get("addon")
    if not isinstance(addon, str) or not PACKAGE_RE.fullmatch(addon):
        return False, ("addon", None)
    if pkg is not None and addon != pkg:
        return False, ("addon-mismatch", addon)
    if not is_state(data.get("state")):
        return False, ("state", None)
    if not isinstance(data.get("items"), list):
        return False, ("items", None)
    items, raws, seen, dropped = [], [], set(), 0
    for raw in data["items"]:
        item = validate_item(raw) if len(items) < MAX_ITEMS else None
        if not item or item["id"] in seen:
            dropped += 1
            continue
        seen.add(item["id"])
        items.append(item)
        raws.append(raw)
    return True, validate_extension(data, {
        "addon": addon,
        "label": clean_text(data.get("label"), LIMITS["label"]),
        "version": clean_text(data.get("version"), LIMITS["version"]),
        "checked_at": parse_time(data.get("checked_at")),
        "state": data["state"],
        "summary": clean_text(data.get("summary"), LIMITS["summary"]),
        "items": items,
        "dropped": dropped,
    }, raws)


def accepted(finding, now):
    """Whether a finding is accepted at the time now: its acceptance counts (validate_finding)
    and has not ended. It ends on its day."""
    return finding["accepted_until"] is not None and finding["accepted_until"] > now


def item_state(item, now):
    """An item's state at the time now, never better than its open findings: those not
    accepted, and those whose acceptance has ended (the board's itemState())."""
    state = item["state"]
    for f in (item.get("findings") or {}).get("list", ()):
        if not accepted(f, now):
            state = worse(state, f["state"])
    return state


def evaluate(status, now):
    """What a valid status means at the time now (seconds), as the board's evaluate():
    state (never better than the items, at least warning when an item is out of date),
    stale_since, by_age, key (the most important item) and items (each with the state it
    has at that time and stale_at). An item with findings is never better than its open
    ones (item_state)."""
    stale_since = None
    ranked = []
    reported = status["state"]
    for index, item in enumerate(status["items"]):
        state, stale_at = item_state(item, now), None
        reported = worse(reported, state)
        if item["checked_at"] is not None and item["stale_after"] is not None:
            at = item["checked_at"] + item["stale_after"]
            if now > at:
                stale_at = at
                stale_since = at if stale_since is None else min(stale_since, at)
                state = worse(state, "warning")
        ranked.append((item, index, state, stale_at))
    state = reported if stale_since is None else worse(reported, "warning")
    top = sorted(ranked, key=lambda r: (-rank(r[2]), 0 if r[0]["value"] else 1, r[1]))
    key = dict(top[0][0], state=top[0][2], stale_at=top[0][3]) if top else None
    return {
        "state": state,
        "stale_since": stale_since,
        "by_age": stale_since is not None and rank(reported) < rank("warning"),
        "key": key,
        "items": [dict(item, state=state, stale_at=stale_at) for item, _, state, stale_at in ranked],
    }


# ---------------------------------------------------------------------------
# Coverage: what the board knows about an add-on on a host (the board's checkCoverage(),
# checksCoverage(), tileOf() and coverageOf(); contract, "Checks and coverage"). Times are seconds
# here, milliseconds there.

# The gaps a check can show, worst first; a check that is "checked" and in time shows none, one
# that is "notapplicable" does not count.
CHECK_GAPS = ("noaccess", "unconfigured", "failed", "unsupported", "stale", "partial")


def check_rank(coverage):
    if coverage == "checked":
        return 0
    return len(CHECK_GAPS) - CHECK_GAPS.index(coverage) if coverage in CHECK_GAPS else len(CHECK_GAPS) + 1


def check_coverage(check, now):
    """What one check says at the time now: {coverage, stale_at}; "stale" for a checked or partial
    check past checked_at + stale_after (stale_at: since when)."""
    at = check["checked_at"] + check["stale_after"] \
        if check.get("checked_at") is not None and check.get("stale_after") is not None else None
    stale = at is not None and now > at and check["state"] in ("checked", "partial")
    return {"coverage": "stale" if stale else check["state"], "stale_at": at if stale else None}


def checks_coverage(status, now, skipped=()):
    """The coverage of a valid answer from its checks at the time now: {coverage, worst, gaps},
    the worst of the checks that apply (all but "notapplicable"), the first of the worst in the
    add-on's order, and every check with a gap, each with its coverage and stale_at. None when
    the answer has no checks, or none that applies: then its items decide.
    skipped: ids of checks a host's profile leaves out on purpose, which do not apply either
    (the board marks them not applicable in its copy of the answer instead)."""
    checks = status.get("checks")
    if not isinstance(checks, list):
        return None
    judged = [dict(c, **check_coverage(c, now)) for c in checks
              if c["state"] != "notapplicable" and c["id"] not in skipped]
    if not judged:
        return None
    gaps = [c for c in judged if c["coverage"] != "checked"]
    worst = None
    for c in gaps:
        if worst is None or check_rank(c["coverage"]) > check_rank(worst["coverage"]):
            worst = c
    return {"coverage": worst["coverage"] if worst else "checked", "worst": worst, "gaps": gaps}


def not_reported_checks(checks, pkg, expect):
    """The checks of the add-on pkg that a host's profile names (expect: its entries,
    "security/exposure" among them) and that its answer does not report (checks: the answer's
    checks, or None for an answer without them), in the order of the profile: the board's
    notReportedChecks(). Each one is a check made up for the gap: state "unsupported", the id as
    its title, nothing else of its own, and not_reported True ("Expected check is not reported").
    A check the answer reports in any state, notapplicable included, is no gap here, and no id is
    made up that the profile does not name."""
    have = {c["id"] for c in checks or ()}
    out = []
    for entry in expect:
        name, sep, cid = entry.partition("/")
        if not sep or name != pkg or cid in have:
            continue
        have.add(cid)
        out.append({"id": cid, "state": "unsupported", "title": cid, "item": None, "checked_at": None,
                    "stale_after": None, "reason": None, "link": None, "not_reported": True})
    return out


def tile_of(result, now):
    """What the board's tileOf() makes of the result of a status call, as far as coverage needs
    it: {kind, state, word, status, eval}. result is {"kind": "ok", "status": a valid status}, or
    {"kind": "noaccess"} (exit 77), {"kind": "rejected"} (a command the manifest may not name),
    or any other kind (the add-on said nothing usable)."""
    kind = result.get("kind")
    if kind == "ok":
        ev = evaluate(result["status"], now)
        # An answer that rates nothing has checked nothing yet: a gap, not Unknown.
        if ev["state"] == "none":
            return {"kind": "status", "state": "warning", "word": "unchecked", "status": result["status"], "eval": ev}
        return {"kind": "status", "state": ev["state"], "word": "stale" if ev["by_age"] else ev["state"],
                "status": result["status"], "eval": ev}
    if kind in ("noaccess", "rejected"):
        return {"kind": kind, "state": "warning", "word": kind}
    return {"kind": "error", "state": "warning", "word": "unreadable"}


def coverage_of(tile, now, skipped=()):
    """What the board knows about an add-on (its coverage), from its tile at the time now:
    checked, unchecked (a valid answer that rates nothing), stale, noaccess, unreadable, and from
    checks partial, failed, unconfigured, unsupported (the worst check that applies). None for a
    tile that is no installed add-on with a status entry. skipped: as checks_coverage()."""
    kind = tile.get("kind")
    if kind == "status":
        from_checks = checks_coverage(tile["status"], now, skipped)
        if from_checks:
            return from_checks["coverage"]
        if tile["word"] == "unchecked":
            return "unchecked"
        return "stale" if tile["eval"]["stale_since"] is not None else "checked"
    if kind == "noaccess":
        return "noaccess"
    if kind in ("error", "rejected"):
        return "unreadable"
    return None


def status_entry(manifest):
    """What a manifest says about the status contract: ("command", path), ("rejected", None)
    for a status entry whose command is not allowed, or None without one."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("azentrius"), dict) or \
            "status" not in manifest["azentrius"]:
        return None
    argv = manifest["azentrius"]["status"]
    cmd = argv[0] if isinstance(argv, list) and argv else None
    if isinstance(cmd, str) and COMMAND_RE.fullmatch(cmd):
        return "command", cmd
    return "rejected", None


def menu_label(manifest):
    """The first menu, tools or dashboard label of a manifest."""
    if not isinstance(manifest, dict):
        return None
    for section in ("menu", "tools", "dashboard"):
        entries = manifest.get(section) if isinstance(manifest.get(section), dict) else {}
        for entry in entries.values():
            label = clean_text(entry.get("label"), LIMITS["label"]) if isinstance(entry, dict) else None
            if label:
                return label
    return None


def status_argv(command, lang):
    """Only the checked command, then always "status --lang LANG"."""
    return [command, "status", "--lang", "de" if lang == "de" else "en"]


# ---------------------------------------------------------------------------
# The settings: /etc/cockpit-host-settings/notify.conf (key = value, root, 0644)

CONFIG_KEYS = ("ntfy_url", "ntfy_topic", "mail_to", "language")
URL_RE = re.compile(r"(https?)://([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]{2,45}\])"
                    r"(?::([0-9]{1,5}))?((?:/[A-Za-z0-9._~-]+)*)/?")
TOPIC_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAIL_RE = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                     r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*")
LANGUAGES = ("en", "de")
URL_MAX = 200
MAIL_MAX = 254
TOKEN_RE = re.compile(r"[A-Za-z0-9_.~+/=-]{1,256}")
NTFY_DOCS = "https://docs.ntfy.sh/install/"


def valid_url(url):
    """The ntfy server's address without a trailing slash, or None: http or https, a host name
    or an address, an optional port and path; no user, no query, no fragment."""
    if not isinstance(url, str) or len(url) > URL_MAX:
        return None
    m = URL_RE.fullmatch(url)
    if not m or (m.group(3) and not 0 < int(m.group(3)) < 65536):
        return None
    return url.rstrip("/")


def valid_topic(topic):
    return isinstance(topic, str) and bool(TOPIC_RE.fullmatch(topic))


def valid_mail(addr):
    return isinstance(addr, str) and len(addr) <= MAIL_MAX and bool(MAIL_RE.fullmatch(addr))


def valid_token(token):
    return isinstance(token, str) and bool(TOKEN_RE.fullmatch(token))


def parse_config(text):
    """The settings from notify.conf: (config, bad line numbers). Unknown keys and lines that
    break the rules are left out; the last good value of a key counts."""
    cfg = {"ntfy_url": None, "ntfy_topic": None, "mail_to": None, "language": "en"}
    bad = []
    for n, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        ok = bool(sep) and key in CONFIG_KEYS
        if ok and key == "ntfy_url":
            value = valid_url(value)
            ok = value is not None
        elif ok and key == "ntfy_topic":
            ok = valid_topic(value)
        elif ok and key == "mail_to":
            ok = valid_mail(value)
        elif ok and key == "language":
            ok = value in LANGUAGES
        if ok:
            cfg[key] = value
        else:
            bad.append(n)
    return cfg, bad


def format_config(cfg):
    lines = ["# Host Settings: where messages about the Azentrius add-ons of this host go.",
             "# Written by the page (host-settings.py set ...); the ntfy token is in ntfy.token.",
             "# SPDX-License-Identifier: Apache-2.0"]
    for key in CONFIG_KEYS:
        value = cfg.get(key)
        if value:
            lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def channels(cfg):
    """The channels the settings set up, in a fixed order."""
    out = []
    if cfg.get("ntfy_url") and cfg.get("ntfy_topic"):
        out.append("ntfy")
    if cfg.get("mail_to"):
        out.append("mail")
    return out


# ---------------------------------------------------------------------------
# A host's target profile: /etc/cockpit-host-settings/profile.conf (key = value, root, 0644)
#
# What this host is expected to check: its roles, and with them the add-ons it should have, extra
# add-ons or checks, and checks left out on purpose. The status verb sends it as `profile` (status
# contract, "Optional: checks, findings and a host's profile"); the notifier tells when an
# expected check is no longer possible.
#
#   roles = web
#   expect = security/exposure
#   skip = update-gaps/cve until 2026-12-31: network data off on purpose
#   set_at = 2026-10-25T10:00:00Z
#
# A profile is set when the file has a valid `roles` line (it may name no role: the add-ons of
# every host only). As Status Board reads it: an `expect` of a check ("security/exposure") makes
# its add-on expected, and a skip names one check of an add-on, never an add-on alone.

# Every host: the way messages go out, updates, what is reachable from where, scheduled jobs.
BASE_EXPECT = ("host-settings", "update-gaps", "security", "cronjobs")
# The roles and what each expects beyond the add-ons of every host. Status Board, which marks the
# bastion, has no status entry of its own, so "bastion" adds nothing that reports a status.
ROLES = (("web", ("image-updates",)), ("database", ("image-updates",)), ("build", ()), ("bastion", ()))
ROLE_EXPECT = dict(ROLES)
# The product names of the add-ons the roles name, for one that is not installed.
ADDON_LABELS = {"host-settings": "Host Settings", "update-gaps": "Update Gaps", "security": "Security Monitor",
                "cronjobs": "Cron Jobs", "image-updates": "Image Update Monitor"}
PROFILE_KEYS = ("roles", "expect", "skip", "set_at")
# A skip names one check of an add-on ("update-gaps/cve"): Status Board lets only such a skip take
# effect, an add-on alone cannot be left out.
SKIP_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,40}/[a-z0-9-]{1,40}")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
SKIP_REASON_MAX = LIMITS["detail"]
_ROLE_SPLIT_RE = re.compile(r"[,\s]+")
PROFILE_HEADER = ("# Host Settings: the target profile of this host (roles, expected add-ons and checks, checks left",
                  "# out on purpose). Written by the page (host-settings.py profile ...); comments and other lines stay.",
                  "# SPDX-License-Identifier: Apache-2.0")


def parse_date(value):
    """A day "YYYY-MM-DD" as the seconds of its start (00:00 UTC), or None."""
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        return None
    try:
        return datetime(int(value[:4]), int(value[5:7]), int(value[8:10]), tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def parse_roles(value):
    """The roles of a `roles` line ("web, database"; none at all is allowed) as a list without
    repeats, or None when one is not in the catalog or there are more than MAX_ROLES."""
    names = list(dict.fromkeys(r for r in _ROLE_SPLIT_RE.split(value) if r))
    if len(names) > MAX_ROLES or any(not ROLE_RE.fullmatch(r) or r not in ROLE_EXPECT for r in names):
        return None
    return names


def clean_reason(value):
    """The reason of a skip as it is kept: one line of text, cleaned, cut to 200, or None."""
    return clean_text(value, SKIP_REASON_MAX)


def parse_skip(value):
    """The value of a `skip` line, "ADDON/CHECK[ until YYYY-MM-DD]: REASON", as {check, until,
    reason} (until "YYYY-MM-DD" or None), or None when it breaks the rules (an add-on alone among
    them)."""
    left, sep, reason = value.partition(":")
    words = left.split()
    reason = clean_reason(reason)
    if not sep or not reason or len(words) not in (1, 3) or not SKIP_RE.fullmatch(words[0]):
        return None
    until = None
    if len(words) == 3:
        if words[1] != "until" or parse_date(words[2]) is None:
            return None
        until = words[2]
    return {"check": words[0], "until": until, "reason": reason}


def _profile_line(raw):
    """One line of profile.conf: (ident, entry) for a valid line of the profile (ident: ("roles",),
    ("expect", item), ("skip", check) or ("set_at",)), ("bad", None) for a line that is not
    understood, (None, None) for an empty line or a comment."""
    line = raw.strip()
    if not line or line.startswith("#"):
        return None, None
    key, sep, value = line.partition("=")
    key, value = key.strip(), value.strip()
    if not sep or key not in PROFILE_KEYS:
        return "bad", None
    if key == "roles":
        roles = parse_roles(value)
        return (("roles",), roles) if roles is not None else ("bad", None)
    if key == "expect":
        return (("expect", value), value) if EXPECT_RE.fullmatch(value) else ("bad", None)
    if key == "skip":
        s = parse_skip(value)
        return (("skip", s["check"]), s) if s else ("bad", None)
    at = parse_time(value)
    return (("set_at",), iso(at)) if at is not None else ("bad", None)


def resolve_expect(roles, extra=()):
    """What a host with these roles and extra expectations should have: the add-ons of every host,
    those of its roles, then the extra ones, each once, at most MAX_EXPECT."""
    out = list(BASE_EXPECT)
    for r in roles or ():
        out.extend(ROLE_EXPECT.get(r, ()))
    out.extend(extra)
    return list(dict.fromkeys(out))[:MAX_EXPECT]


def parse_profile(text):
    """The profile from profile.conf: (profile, bad line numbers). profile is {roles, extra, skip,
    set_at}: roles None when the file sets no profile; extra the expectations beyond the roles;
    skip [{check, until, reason}]. A line that is not one of the four keys with a valid value is
    left out and counted; of `roles` and `set_at` the last valid line counts; `expect` and `skip`
    come once per add-on or check (a later `skip` of the same check counts instead of the earlier
    one). Lines past the limits (MAX_EXPECT expected, MAX_SKIP skips) count as bad."""
    prof = {"roles": None, "extra": [], "skip": [], "set_at": None}
    bad, extra, skips = [], {}, {}
    for n, raw in enumerate((text or "").splitlines(), 1):
        ident, entry = _profile_line(raw)
        if ident is None:
            continue
        if ident == "bad":
            bad.append(n)
        elif ident[0] == "roles":
            prof["roles"] = entry
        elif ident[0] == "set_at":
            prof["set_at"] = entry
        elif ident[0] == "expect":
            extra.setdefault(entry, n)
        else:
            skips.pop(entry["check"], None)
            skips[entry["check"]] = (n, entry)
    for item, n in extra.items():
        have = resolve_expect(prof["roles"], prof["extra"])
        if item in have or len(have) < MAX_EXPECT:
            prof["extra"].append(item)
        else:
            bad.append(n)
    for n, entry in sorted(skips.values(), key=lambda x: x[0]):
        if len(prof["skip"]) < MAX_SKIP:
            prof["skip"].append(entry)
        else:
            bad.append(n)
    return prof, sorted(bad)


def profile_set(prof):
    return isinstance(prof, dict) and prof.get("roles") is not None


def skip_active(skip, now):
    """Whether a skip counts at the time now: without an end, or before its day (it ends on its
    day at 00:00 UTC, as an acceptance does)."""
    return skip["until"] is None or now < parse_date(skip["until"])


def _profile_lines(prof):
    """The profile's lines as [(ident, entry, line)] in the order they are written."""
    if not profile_set(prof):
        return []
    out = [(("roles",), list(prof["roles"]), "roles = " + ", ".join(prof["roles"]))]
    out += [(("expect", x), x, "expect = " + x) for x in prof["extra"]]
    out += [(("skip", s["check"]), s, "skip = " + s["check"] + (" until " + s["until"] if s["until"] else "") + ": " +
             s["reason"]) for s in prof["skip"]]
    if prof.get("set_at"):
        out.append((("set_at",), prof["set_at"], "set_at = " + prof["set_at"]))
    return out


def format_profile(text, prof):
    """profile.conf with the profile prof (None or no roles: no profile), line by line: comments,
    empty lines and every line that is not a valid line of the profile stay as they are, where
    they are; a valid line stays as it is while it still holds, is changed in place when it
    holds something else now, and goes when it no longer holds (or comes twice); new lines go to
    the end."""
    want = {ident: (entry, line) for ident, entry, line in _profile_lines(prof)}
    lines = (text or "").splitlines()
    out, placed = ([] if lines else list(PROFILE_HEADER)), set()
    for raw in lines:
        ident, entry = _profile_line(raw)
        if ident is None or ident == "bad":
            out.append(raw)
        elif ident in want and ident not in placed:
            placed.add(ident)
            out.append(raw if entry == want[ident][0] else want[ident][1])
    out += [line for ident, (_, line) in want.items() if ident not in placed]
    return "\n".join(out) + "\n" if out else ""


def profile_answer(prof, mtime=None):
    """The `profile` of the status answer (contract, "Optional: checks, findings and a host's
    profile"), or None without a profile: the roles, what they expect with the extra
    expectations (resolved), every skip with its reason and, when it has one, its end as a time
    (the day at 00:00 UTC), and when the profile was set (set_at, else the file's time)."""
    if not profile_set(prof):
        return None
    skip = []
    for s in prof["skip"]:
        e = {"check": s["check"], "reason": s["reason"]}
        if s["until"]:
            e["until"] = s["until"] + "T00:00:00Z"
        skip.append(e)
    out = {"roles": list(prof["roles"]), "expect": resolve_expect(prof["roles"], prof["extra"]), "skip": skip}
    at = prof.get("set_at") or (iso(mtime) if mtime is not None else None)
    if at:
        out["set_at"] = at
    return out
