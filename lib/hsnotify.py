# SPDX-License-Identifier: Apache-2.0
"""Host Settings, the Cockpit add-on: what the notifier does.

Lives in /usr/lib/cockpit-host-settings (root-owned). Standard library only,
no side effects on import. host-settings-notify.py runs it as the service's
own user, never as root; the commands, the network and the clock come in as
arguments, so the tests can stand in for all of them.

  discover()    the add-ons with a status entry in /usr/share/cockpit/*/manifest.json
  collect()     their status verbs (`COMMAND status --lang xx`), judged as Status Board does
  changes()     what changed since the last message, per item (add-on and id), with an hour
                against flapping
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
FORMAT = 1
FLAP = 3600                        # the same change of the same item again only after an hour
CHECK_EVERY = 86400                # the topic's protection without a login: read once a day
VERB_LIMIT = 10                    # seconds per status verb (the contract asks for 2 at most)
HTTP_TIMEOUT = 20
BODY_MAX = 3800                    # bytes; ntfy turns longer messages into attachments (4096)
MAX_LINES = 20
PROBLEM = ("warning", "danger")
ERROR_KINDS = ("rejected", "untrusted", "manifest", "invalid", "missing", "failed", "timeout", "unsupported")
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
        "why.gone": "no longer reported", "why.removed": "the add-on is no longer installed",
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
        "why.gone": "wird nicht mehr gemeldet", "why.removed": "das Add-on ist nicht mehr installiert",
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
    version, summary, items}. kind is "ok" for a valid answer, "noaccess" for exit 77
    (shown as Unknown, as on the board), else one of ERROR_KINDS (a warning)."""
    out = {"label": label or pkg, "kind": "ok", "reason": None, "code": None, "state": "none",
           "version": None, "summary": None, "items": []}

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
    out.update(label=status["label"] or out["label"], state=ev["state"], version=status["version"],
               summary=status["summary"],
               items=[{"id": i["id"], "title": i["title"], "state": i["state"], "value": i["value"],
                       "detail": i["detail"], "link": i["link"], "stale_at": i["stale_at"]} for i in ev["items"]])
    return out


def collect(lang, now, run=hsproc.run, root="/usr/share/cockpit", lstat=os.lstat):
    """{pkg: read_addon(...)} for every add-on with a status entry."""
    return {pkg: read_addon(pkg, label, entry, lang, now, run, lstat) for pkg, label, entry in discover(root)}


# ---------------------------------------------------------------------------
# What changed

def keys_of(snapshot):
    """{key: (level, what)} of a snapshot. An add-on is "PKG#" (a warning while its status
    cannot be read), each of its items "PKG#ID"."""
    cur = {}
    for pkg, a in snapshot.items():
        cur[pkg + "#"] = ("warning" if a["kind"] in ERROR_KINDS else "ok", None)
        if a["kind"] == "ok":
            for it in a["items"]:
                cur[pkg + "#" + it["id"]] = (level(it["state"]), it)
    return cur


def changes(sent, snapshot, now):
    """(events, sent): what to tell, and the new memory of what was told. `sent` is
    {key: {level, at, last: {"FROM>TO": time}}}; a key that is not there was OK. A change
    that was told for the same key less than FLAP seconds ago waits (its key keeps the old
    level), so a flapping item gives one message an hour at most. The items of an add-on
    whose status cannot be read are left as they were: unreadable is no reason for OK."""
    cur = keys_of(snapshot)
    events, out = [], {}
    for key in sorted(set(cur) | set(sent)):
        pkg = key.partition("#")[0]
        old_entry = sent.get(key) if isinstance(sent.get(key), dict) else {}
        old = old_entry.get("level") if old_entry.get("level") in PROBLEM else "ok"
        why = None
        if key in cur:
            new, item = cur[key]
        elif pkg in snapshot and snapshot[pkg]["kind"] != "ok":
            if old_entry:
                out[key] = old_entry
            continue
        else:
            new, item, why = "ok", None, "gone" if pkg in snapshot else "removed"
        if old == new:
            if old != "ok" or _recent(old_entry, now):
                out[key] = old_entry
            continue
        change = old + ">" + new
        last = old_entry.get("last") if isinstance(old_entry.get("last"), dict) else {}
        if isinstance(last.get(change), (int, float)) and now - last[change] < FLAP:
            if old_entry:
                out[key] = old_entry
            continue
        out[key] = {"level": new, "at": now, "last": dict(last, **{change: now})}
        events.append(_event(key, snapshot.get(pkg) or {}, item, old, new, why, old_entry))
        out[key]["label"] = events[-1]["label"]
        if events[-1]["title"]:
            out[key]["title"] = events[-1]["title"]
    order = {"worse": 0, "better": 1, "resolved": 2}
    events.sort(key=lambda e: (order[e["kind"]], -hslib.rank(e["to"]), e["label"].lower(), e["key"]))
    return events, out


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
    """The memory after the first message (the summary): every problem as told."""
    out = {}
    for key, (lvl, item) in keys_of(snapshot).items():
        if lvl in PROBLEM:
            pkg = key.partition("#")[0]
            out[key] = {"level": lvl, "at": now, "last": {}, "label": snapshot[pkg]["label"]}
            if item:
                out[key]["title"] = item["title"]
    return out


# ---------------------------------------------------------------------------
# Messages

def _unreadable(lang, e):
    """Why the status of an add-on cannot be read, or None."""
    reason = e.get("reason") if e.get("addon_kind") == "invalid" else e.get("addon_kind")
    key = "reason." + str(reason)
    return t(lang, key, code=e.get("code")) if key in T["en"] else None


def _line(lang, e):
    """One change as two lines: what and where, then the detail."""
    resolved = e["kind"] == "resolved"
    if e["id"]:
        title, detail = e["title"] or e["id"], e["detail"]
    elif resolved:
        title, detail = (t(lang, "unreadable") if e["why"] else t(lang, "readable")), None
    else:
        title, detail = t(lang, "unreadable"), _unreadable(lang, e)
    if e["kind"] == "worse":
        head = t(lang, "head.worse", state=t(lang, "state." + e["to"]))
    elif e["kind"] == "better":
        head = t(lang, "head.better", state=t(lang, "state." + e["to"]), before=t(lang, "state." + e["from"]))
    else:
        head = t(lang, "head.resolved")
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
    """The problems of a snapshot as changes from OK, the most severe first."""
    out = []
    for key, (lvl, item) in keys_of(snapshot).items():
        if lvl in PROBLEM:
            out.append(_event(key, snapshot[key.partition("#")[0]], item, "ok", lvl))
    out.sort(key=lambda e: (-hslib.rank(e["to"]), e["label"].lower(), e["key"]))
    return out


def message(lang, host, events=None, snapshot=None, summary=False):
    """{title, body, priority, tags} of the message for events, or of the summary of a
    snapshot (the first message after setting up)."""
    footer = t(lang, "footer", host=host)
    if summary:
        found = problems_of(snapshot or {})
        intro = t(lang, "summary.intro") + "\n\n"
        if found:
            head = intro + t(lang, "summary.problems")
        elif snapshot:
            head = intro + t(lang, "summary.clean", n=len(snapshot))
        else:
            head = intro + t(lang, "summary.none")
        top = found[0]["to"] if found else "ok"
        return {"title": t(lang, "title.summary", host=host), "body": _fit(lang, head, [_line(lang, e) for e in found],
                                                                           footer),
                "priority": PRIORITY[top] if found else 3, "tags": [TAGS[top]]}
    if len(events) == 1:
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
