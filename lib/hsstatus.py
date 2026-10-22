# SPDX-License-Identifier: Apache-2.0
"""The status contract of the Azentrius add-ons (schema 1) for Host Settings
itself: whether messages are set up, whether the last one went out, whether
the ntfy topic is closed to anyone without a login, and when the notifier
last ran. Status Board reads it through `host-settings.py status --lang xx`.

Lives in /usr/lib/cockpit-host-settings (root-owned). Standard library only,
no side effects on import, nothing here runs a command.

The notifier writes status.json (0644): the English document plus the German
texts under "i18n" (a field readers of schema 1 ignore). No addresses, no
server names, no topics: only channels, states and times.
"""

import os
import re
from datetime import datetime, timezone

import hslib

SCHEMA = 1
ADDON = "host-settings"
LABEL = "Host Settings"
STALE_AFTER = 900        # seconds: the notifier runs every 5 minutes; three runs missed is out of date
LINK = "#/notifications"
PAGE_JS = (os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "plugin", "settings.js"),
           "/usr/share/cockpit/host-settings/settings.js")

T = {
    "en": {
        "channel.ntfy": "ntfy", "channel.mail": "e-mail", "and": " and ",
        "summary.ok": "Messages go to {channels}.",
        "summary.found": "Needs attention: {list}.",
        "summary.missing": "Not set up yet.",
        "summary.off": "The regular check is off.",
        "summary.unreadable": "The state of Host Settings could not be read.",
        "setup.title": "Channels",
        "setup.ok": "Messages about the add-ons of this host go to {channels}.",
        "setup.none": "No channel is set up: set up ntfy or e-mail in Host Settings.",
        "delivery.title": "Last message",
        "delivery.none": "No message sent yet.",
        "delivery.ok": "Sent on {when}.",
        "delivery.failed": "Could not be sent: {list}.",
        "access.title": "Protection of the topic",
        "access.none": "Not checked yet: “Check connection” in Host Settings.",
        "access.ok": "Closed to anyone without a login.",
        "access.read": "Anyone who knows the topic can read the messages.",
        "access.write": "Anyone who knows the topic can send messages to it.",
        "access.both": "Anyone who knows the topic can read the messages and send messages to it.",
        "run.title": "Notifier",
        "run.ok": {"one": "{n} add-on read every 5 minutes.", "other": "{n} add-ons read every 5 minutes."},
        "run.old": "The last run is older than 15 minutes.",
        "missing.title": "Not set up",
        "missing.detail": "Set up a channel and the regular check in Host Settings.",
        "off.title": "Switched off",
        "off.detail": "No messages from this host until the regular check is set up again.",
        "unreadable.title": "State not readable",
        "unreadable.detail": "Open Host Settings with administrative access.",
        "reason.sent": "sent", "reason.rejected": "refused (token or rights)", "reason.no-token": "no token",
        "reason.rate-limited": "too many messages", "reason.too-large": "too large", "reason.tls": "certificate",
        "reason.timeout": "no answer", "reason.network": "not reachable", "reason.no-mta": "no mail server on localhost:25",
        "reason.refused": "refused", "reason.smtp": "mail server error", "reason.http": "HTTP {code}",
    },
    "de": {
        "channel.ntfy": "ntfy", "channel.mail": "E-Mail", "and": " und ",
        "summary.ok": "Nachrichten gehen an {channels}.",
        "summary.found": "Braucht Aufmerksamkeit: {list}.",
        "summary.missing": "Noch nicht eingerichtet.",
        "summary.off": "Die regelmäßige Prüfung ist ausgeschaltet.",
        "summary.unreadable": "Der Stand von Host Settings ließ sich nicht lesen.",
        "setup.title": "Kanäle",
        "setup.ok": "Nachrichten über die Add-ons dieses Hosts gehen an {channels}.",
        "setup.none": "Kein Kanal eingerichtet: Richten Sie ntfy oder E-Mail in Host Settings ein.",
        "delivery.title": "Letzte Nachricht",
        "delivery.none": "Noch keine Nachricht verschickt.",
        "delivery.ok": "Verschickt am {when}.",
        "delivery.failed": "Ließ sich nicht verschicken: {list}.",
        "access.title": "Schutz des Themas",
        "access.none": "Noch nicht geprüft: „Verbindung prüfen“ in Host Settings.",
        "access.ok": "Ohne Anmeldung geschlossen.",
        "access.read": "Wer das Thema kennt, kann die Nachrichten lesen.",
        "access.write": "Wer das Thema kennt, kann Nachrichten hineinschicken.",
        "access.both": "Wer das Thema kennt, kann die Nachrichten lesen und Nachrichten hineinschicken.",
        "run.title": "Notifier",
        "run.ok": {"one": "{n} Add-on alle 5 Minuten gelesen.", "other": "{n} Add-ons alle 5 Minuten gelesen."},
        "run.old": "Der letzte Lauf ist älter als 15 Minuten.",
        "missing.title": "Nicht eingerichtet",
        "missing.detail": "Richten Sie in Host Settings einen Kanal und die regelmäßige Prüfung ein.",
        "off.title": "Ausgeschaltet",
        "off.detail": "Keine Nachrichten von diesem Host, bis die regelmäßige Prüfung wieder eingerichtet ist.",
        "unreadable.title": "Stand nicht lesbar",
        "unreadable.detail": "Öffnen Sie Host Settings mit administrativem Zugriff.",
        "reason.sent": "verschickt", "reason.rejected": "abgelehnt (Token oder Rechte)", "reason.no-token": "kein Token",
        "reason.rate-limited": "zu viele Nachrichten", "reason.too-large": "zu groß", "reason.tls": "Zertifikat",
        "reason.timeout": "keine Antwort", "reason.network": "nicht erreichbar",
        "reason.no-mta": "kein Mailserver auf localhost:25", "reason.refused": "abgelehnt",
        "reason.smtp": "Fehler des Mailservers", "reason.http": "HTTP {code}",
    },
}
MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def t(lang, key, **kw):
    s = T[lang][key]
    if isinstance(s, dict):
        s = s["one"] if kw.get("n") == 1 else s["other"]
    return s.format(**kw)


def version():
    """The add-on's version, from const VERSION in the page's settings.js."""
    for p in PAGE_JS:
        try:
            with open(p, encoding="utf-8") as f:
                m = re.search(r'^\s*const VERSION = "([0-9A-Za-z.+~-]{1,40})";', f.read(1 << 20), re.M)
        except OSError:
            continue
        if m:
            return m.group(1)
    return "unknown"


def when(lang, ts):
    d = datetime.fromtimestamp(ts, timezone.utc)
    if lang == "de":
        return f"{d.day}.{d.month}. um {d:%H:%M} UTC"
    return f"{d.day} {MONTHS_EN[d.month - 1]} at {d:%H:%M} UTC"


def reason_text(lang, r):
    reason = (r or {}).get("reason") or "network"
    if reason.startswith("http-"):
        return t(lang, "reason.http", code=reason[5:])
    key = "reason." + reason
    return t(lang, key) if key in T["en"] else reason


def channels_text(lang, chans):
    return t(lang, "and").join(t(lang, "channel." + c) for c in chans)


def items_of(info, lang):
    """The items for what the notifier knows (`info`): channels, delivery {at, results},
    access {state, anon_read, anon_write} or None, run {at, addons}."""
    at = info["run"]["at"]
    chans = info.get("channels") or []
    out = []

    def item(iid, state, detail, value=None):
        i = {"id": iid, "state": state, "title": t(lang, iid + ".title"), "detail": detail, "link": LINK,
             "checked_at": at, "stale_after": STALE_AFTER}
        if value is not None:
            i["value"] = str(value)
        return i

    if chans:
        out.append(item("setup", "ok", t(lang, "setup.ok", channels=channels_text(lang, chans)), len(chans)))
    else:
        out.append(item("setup", "none", t(lang, "setup.none")))

    d = info.get("delivery")
    if not d or not d.get("results"):
        out.append(item("delivery", "none", t(lang, "delivery.none")))
    else:
        bad = [(c, r) for c, r in sorted(d["results"].items()) if not r.get("ok")]
        if bad:
            lst = ", ".join(f"{t(lang, 'channel.' + c)} ({reason_text(lang, r)})" for c, r in bad)
            out.append(item("delivery", "warning", t(lang, "delivery.failed", list=lst)))
        else:
            out.append(item("delivery", "ok", t(lang, "delivery.ok", when=when(lang, hslib.parse_time(d["at"])))))

    if "ntfy" in chans:
        a = info.get("access") or {}
        read, write = a.get("anon_read") == "warning", a.get("anon_write") == "warning"
        if read or write:
            out.append(item("access", "warning", t(lang, "access.both" if read and write else
                                                     "access.read" if read else "access.write")))
        elif a.get("state") == "ok":
            out.append(item("access", "ok", t(lang, "access.ok")))
        else:
            out.append(item("access", "none", t(lang, "access.none")))

    # Without a channel the notifier reads, but tells nobody: not "OK" for the board.
    n = info["run"].get("addons", 0)
    out.append(item("run", "ok" if chans else "none", t(lang, "run.ok", n=n), n))
    return out


def overall(items):
    worst = max((hslib.rank(i.get("state")) for i in items), default=0)
    if worst >= hslib.rank("info"):
        return hslib.STATES[worst]
    return "ok" if any(i.get("state") == "ok" for i in items) else "none"


def document(info, lang, ver):
    items = items_of(info, lang)
    state = overall(items)
    found = [i for i in items if hslib.rank(i["state"]) >= hslib.rank("info")]
    chans = info.get("channels") or []
    if found:
        summary = t(lang, "summary.found", list=", ".join(i["title"] for i in found))
    elif not chans:
        summary = t(lang, "summary.missing")
    else:
        summary = t(lang, "summary.ok", channels=channels_text(lang, chans))
    return {"schema": SCHEMA, "addon": ADDON, "label": LABEL, "version": ver, "checked_at": info["run"]["at"],
            "state": state, "summary": summary, "items": items}


def none_document(lang, ver, why, checked_at=None):
    """The answer without a usable status.json: why is "missing" (never ran), "off" (the
    regular check was switched off) or "unreadable"."""
    at = checked_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    item = {"id": "setup" if why == "missing" else "run", "state": "none", "title": t(lang, why + ".title"),
            "detail": t(lang, why + ".detail"), "link": LINK, "checked_at": at, "stale_after": hslib.MAX_STALE_AFTER}
    return {"schema": SCHEMA, "addon": ADDON, "label": LABEL, "version": ver, "checked_at": at,
            "state": "none", "summary": t(lang, "summary." + why), "items": [item]}


def status_file(info, ver):
    """What status.json holds: the English document, the German texts under "i18n"."""
    en = document(info, "en", ver)
    de = document(info, "de", ver)
    en["i18n"] = {"de": {"summary": de["summary"],
                         "items": {i["id"]: {k: i[k] for k in ("title", "detail") if k in i} for i in de["items"]},
                         "run_old": t("de", "run.old")},
                  "en": {"run_old": t("en", "run.old")}}
    return en


def off_file(ver, at):
    """status.json after the regular check was switched off."""
    en = none_document("en", ver, "off", at)
    de = none_document("de", ver, "off", at)
    en["i18n"] = {"de": {"summary": de["summary"], "items": {i["id"]: {"title": i["title"], "detail": i["detail"]}
                                                             for i in de["items"]}}}
    return en


def localize(status, lang, now=None):
    """A status.json in the language asked for, without "i18n". A run older than
    STALE_AFTER turns the item `run` into a warning."""
    out = {k: v for k, v in status.items() if k != "i18n"}
    tr = (status.get("i18n") or {}).get(lang) or {}
    if lang != "en" and isinstance(tr.get("summary"), str):
        out["summary"] = tr["summary"]
    texts = tr.get("items") if isinstance(tr.get("items"), dict) and lang != "en" else {}
    items = []
    if now is None:
        now = datetime.now(timezone.utc).timestamp()
    elif isinstance(now, datetime):
        now = now.timestamp()
    for i in status.get("items") or []:
        i = dict(i)
        for k, v in (texts.get(i.get("id")) or {}).items():
            if k in ("title", "detail") and isinstance(v, str):
                i[k] = v
        at = hslib.parse_time(i.get("checked_at"))
        if i.get("id") == "run" and i.get("state") == "ok" and at is not None and now - at > STALE_AFTER:
            i["state"] = "warning"
            i["detail"] = tr.get("run_old") if isinstance(tr.get("run_old"), str) else T[lang]["run.old"]
        items.append(i)
    out["items"] = items
    out["state"] = overall(items)
    return out


def problems(status):
    """What breaks the contract in a status document of this add-on (empty when nothing)."""
    out = []
    for key in ("schema", "addon", "label", "version", "checked_at", "state", "summary", "items"):
        if key not in status:
            out.append("missing " + key)
    if status.get("schema") != SCHEMA or status.get("addon") != ADDON:
        out.append("schema or addon")
    if status.get("state") not in hslib.RANK:
        out.append("state")
    items = status.get("items")
    if not isinstance(items, list):
        return out + ["items"]
    for i in items:
        if not isinstance(i, dict) or not hslib.ITEM_ID_RE.fullmatch(str(i.get("id", ""))) or \
                i.get("state") not in hslib.RANK or not isinstance(i.get("title"), str) or \
                not isinstance(i.get("checked_at"), str) or not isinstance(i.get("stale_after"), int):
            out.append("item " + repr(i.get("id") if isinstance(i, dict) else i))
    return out
