# SPDX-License-Identifier: Apache-2.0
"""Host Settings, the Cockpit add-on: the rules the notifier and the page's
helper share.

Lives in /usr/lib/cockpit-host-settings (root-owned). Standard library only,
no side effects on import; nothing here runs a command or uses the network.

- The status contract of the Azentrius add-ons (schema 1), read exactly as
  Status Board reads it (cockpit-status-board, src/lib/status.js): every
  answer is checked, control, zero-width and bidirectional characters go,
  every text is cut to the board's limits, and an item past its stale_after
  counts as a warning. test/notify_test.py holds these rules against the
  board's own fixtures (test/fixtures/board, tools/board-fixtures.sha256).
- The settings file /etc/cockpit-host-settings/notify.conf.
"""

import fnmatch
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
        "link": link if isinstance(link, str) and LINK_RE.fullmatch(link) else None,
        "checked_at": parse_time(raw.get("checked_at")),
        "stale_after": int(stale) if _is_int(stale) and 0 < stale <= MAX_STALE_AFTER else None,
    }


def validate_status(output, pkg=None):
    """The output of `status --lang xx` of the package pkg (text): (True, status) with every
    field checked and cut, or (False, (reason, detail)). Unknown fields are ignored; an item
    that breaks the rules is left out (status["dropped"] counts them)."""
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
    items, seen, dropped = [], set(), 0
    for raw in data["items"]:
        item = validate_item(raw) if len(items) < MAX_ITEMS else None
        if not item or item["id"] in seen:
            dropped += 1
            continue
        seen.add(item["id"])
        items.append(item)
    return True, {
        "addon": addon,
        "label": clean_text(data.get("label"), LIMITS["label"]),
        "version": clean_text(data.get("version"), LIMITS["version"]),
        "checked_at": parse_time(data.get("checked_at")),
        "state": data["state"],
        "summary": clean_text(data.get("summary"), LIMITS["summary"]),
        "items": items,
        "dropped": dropped,
    }


def evaluate(status, now):
    """What a valid status means at the time now (seconds), as the board's evaluate():
    state (never better than the items, at least warning when an item is out of date),
    stale_since, by_age, key (the most important item) and items (each with the state it
    has at that time and stale_at)."""
    stale_since = None
    ranked = []
    for index, item in enumerate(status["items"]):
        state, stale_at = item["state"], None
        if item["checked_at"] is not None and item["stale_after"] is not None:
            at = item["checked_at"] + item["stale_after"]
            if now > at:
                stale_at = at
                stale_since = at if stale_since is None else min(stale_since, at)
                state = worse(state, "warning")
        ranked.append((item, index, state, stale_at))
    reported = status["state"]
    for item in status["items"]:
        reported = worse(reported, item["state"])
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


def topic_matches(pattern, topic):
    """Whether an ntfy access pattern (with * as wildcard) covers the topic."""
    return isinstance(pattern, str) and fnmatch.fnmatchcase(topic, pattern)
