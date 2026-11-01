#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Builds a static preview of the page with made-up data.

    python3 tools/preview/build.py      -> tools/preview/out/host-settings/index.html
    open tools/preview/out/host-settings/index.html?scenario=ready&lang=de

Scenarios: fresh (nothing set up, no target profile), ready (ntfy and e-mail, the timer on, four
add-ons read, the target profile of a web server with one check left out, a watchdog in Uptime
Kuma), open (the topic is open without a login, ntfy did not accept the last message and is
failing since, the last signal did not reach the watchdog), nomta (only a sendmail command, no
mail server on localhost:25, so e-mail failed its test and is failing), menu (the Cockpit tab:
Cockpit's menu with overrides for all users and just for the user, a setting that makes an entry
without a page, a file that is not JSON), menu-noadmin (the same without administrative access).
admin=0 shows the summary without administrative access. Cockpit's base1/cockpit.js is replaced
by cockpit-stub.js, which answers the page's calls from these scenarios; the dialogs run against
it too, and manifests-i18n.js holds German names for Cockpit's own pages. Nothing touches a real
server. The output directory is generated and git-ignored.
"""
import copy
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(HERE, "out")
NOW = "2026-10-19T19:05:00Z"
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(REPO, "lib"))
import hslib  # noqa: E402
import hsstatus  # noqa: E402

PO_STUB = """// Stand-in for Cockpit's base1/po.js: ?lang=de switches the session language.
const _l = new URLSearchParams(location.search).get("lang");
if (_l && _l !== "en") cockpit.locale({"": {"language": _l, "language-direction": "ltr"}});
"""

# Stand-in for the translations of the manifests that Cockpit's bridge serves (../manifests-i18n.js).
MANIFESTS_DE = {"Overview": "Übersicht", "Logs": "Protokolle", "Storage": "Speicher", "Networking": "Netzwerk",
                "Accounts": "Konten", "Services": "Dienste", "Applications": "Anwendungen",
                "Software updates": "Software-Aktualisierungen", "Terminal": "Terminal",
                "Diagnostic reports": "Diagnoseberichte"}
MANIFESTS_I18N = ("// Stand-in for the translations of every package's manifest that Cockpit's bridge serves.\n"
                  "if (cockpit.language === \"de\") cockpit.locale(" +
                  json.dumps({k: [None, v] for k, v in MANIFESTS_DE.items()}, ensure_ascii=False) + ");\n")


# Cockpit's menu as `menu show` says it (lib/hsmenu.py, view()).
def menu_entry(section, key, folder, label, order, system=None, user=None):
    now, gone = {"label": label, "order": order}, False
    for mark in (system, user):
        if not mark:
            continue
        if mark.get("hidden"):
            gone = True
            continue
        if gone:
            now, gone = {"label": None, "order": None}, False
        now = dict(now, **{k: mark[k] for k in ("label", "order") if k in mark})
    effective = dict(label=label, order=order, hidden=True) if gone else dict(now, hidden=False)
    return {"section": section, "key": key, "package": folder, "dir": folder, "editable": True,
            "base": {"label": label, "order": order}, "system": system, "user": user, "effective": effective}


MENU = {
    "user": "admin", "uid": 1000, "cut": False, "paths": {"system": "/etc/cockpit", "user": "/home/admin/.config/cockpit"},
    "entries": [
        menu_entry("menu", "index", "systemd", "Overview", -2),
        menu_entry("menu", "logs", "systemd", "Logs", 20, {"hidden": True}),
        menu_entry("menu", "index", "storaged", "Storage", 30),
        menu_entry("menu", "index", "networkmanager", "Networking", 40),
        menu_entry("menu", "index", "users", "Accounts", 70),
        menu_entry("menu", "index", "apps", "Applications", 80),
        menu_entry("menu", "index", "packagekit", "Software updates", 90),
        menu_entry("menu", "services", "systemd", "Services", 100, None, {"order": 5}),
        menu_entry("menu", "index", "status-board", "Status Board", 101),
        menu_entry("menu", "index", "host-settings", "Host Settings", 106, {"order": 3}),
        menu_entry("tools", "terminal", "systemd", "Terminal", None, {"label": "Console"}),
        menu_entry("tools", "index", "sosreport", "Diagnostic reports", None),
    ],
    "orphans": [{"scope": "system", "dir": "users", "section": "menu", "key": "people", "phantom": True,
                 "value": {"order": 75, "label": "People"}}],
    "files": [
        {"scope": "system", "dir": "systemd", "path": "/etc/cockpit/systemd.override.json", "sha256": "a" * 64, "valid": True,
         "error": None, "foreign": 1, "served": True},
        {"scope": "system", "dir": "host-settings", "path": "/etc/cockpit/host-settings.override.json", "sha256": "b" * 64,
         "valid": True, "error": None, "foreign": 0, "served": True},
        {"scope": "system", "dir": "users", "path": "/etc/cockpit/users.override.json", "sha256": "c" * 64, "valid": True,
         "error": None, "foreign": 0, "served": True},
        {"scope": "system", "dir": "storaged", "path": "/etc/cockpit/storaged.override.json", "sha256": "d" * 64, "valid": False,
         "error": "invalid", "foreign": 0, "served": True},
        {"scope": "user", "dir": "systemd", "path": "/home/admin/.config/cockpit/systemd.override.json", "sha256": "e" * 64,
         "valid": True, "error": None, "foreign": 0, "served": True},
    ],
}

# The target profile as `show` sends it: none on a fresh server, the role web on the others.
CATALOG = {"base": list(hslib.BASE_EXPECT), "roles": [[r, list(adds)] for r, adds in hslib.ROLES],
           "labels": dict(hslib.ADDON_LABELS)}
NO_PROFILE = {"set": False, "unreadable": False, "roles": [], "extra": [], "expect": [], "skip": [], "set_at": None,
              "bad_lines": [], "catalog": CATALOG}
WEB_PROFILE = dict(NO_PROFILE, set=True, roles=["web"], expect=hslib.resolve_expect(["web"]), set_at="2026-10-18T09:00:00Z",
                   skip=[{"check": "update-gaps/cve", "until": None, "reason": "Network data off on purpose", "active": True}])
FRESH = {
    "root": True, "config": {"ntfy_url": None, "ntfy_topic": None, "mail_to": None, "language": "en"}, "bad_lines": [],
    "token": False, "timer": "inactive", "next": None, "running": False, "host": "web1",
    "smtp": {"listening": True, "sendmail": True}, "last": None, "channels": {}, "check": None, "test_mail": None,
    "docs": "https://docs.ntfy.sh/install/", "profile": NO_PROFILE,
}
TARGET = "https://ntfy.example.com/cockpit-web1"
STEPS = [{"id": "reach", "result": "ok", "code": 200, "reason": "answered"}, {"id": "tls", "result": "ok", "code": None, "reason": "https"},
         {"id": "publish", "result": "ok", "code": 200, "reason": "sent"}, {"id": "anon-read", "result": "ok", "code": 403, "reason": "protected"},
         {"id": "anon-write", "result": "ok", "code": 403, "reason": "protected"}, {"id": "token", "result": "ok", "code": 200, "reason": "write-only"}]


# The watchdog as `show` sends it: never the address.
BEAT = {"configured": True, "invalid": False, "adapter": "uptime-kuma", "encrypted": True, "local": False,
        "last": {"at": "2026-10-19T19:00:04Z", "adapter": "uptime-kuma", "signal": "success", "why": None,
                 "result": {"ok": True, "code": 200, "reason": "accepted", "attempts": 1}},
        "test": None}


def item(iid, title, state, value=None, stale=False):
    return {"id": iid, "title": title, "state": state, "value": value, "detail": None, "link": None, "stale": stale}


READY = dict(FRESH, **{
    "config": {"ntfy_url": "https://ntfy.example.com", "ntfy_topic": "cockpit-web1", "mail_to": "ops@example.com", "language": "en"},
    "token": True, "timer": "active", "next": "2026-10-19T19:10:00Z",
    "last": {
        "baseline_at": "2026-10-18T09:00:00Z",
        "run": {"at": "2026-10-19T19:00:00Z", "took_ms": 812, "addons": 4, "bad_lines": []},
        "delivery": {"at": "2026-10-19T18:30:00Z", "summary": False, "changes": 1, "title": "web1: Cron Jobs, Failed runs",
                     "results": {"ntfy": {"ok": True, "code": 200, "reason": "sent"}, "mail": {"ok": True, "code": None, "reason": "sent"}}},
        "access": {"state": "ok", "anon_read": "ok", "anon_write": "ok", "at": "2026-10-19T09:00:00Z", "target": TARGET, "full": True},
        "seen": {
            "cronjobs": {"label": "Cron Jobs", "kind": "ok", "reason": None, "code": None, "state": "danger", "version": "2.8.1",
                         "summary": None, "items": [item("failed", "Failed runs", "danger", "2"), item("cron", "cron", "ok")]},
            "image-updates": {"label": "Image Update Monitor", "kind": "ok", "reason": None, "code": None, "state": "info",
                              "version": "0.24.0", "summary": None, "items": [item("updates", "Updates available", "info", "3")]},
            "update-gaps": {"label": "Update Gaps", "kind": "ok", "reason": None, "code": None, "state": "warning",
                            "version": "0.6.0", "summary": None,
                            "items": [item("held", "Held packages", "warning", "2"), item("lists", "Package lists", "ok", stale=True)]},
            "security": {"label": "Security Monitor", "kind": "invalid", "reason": "not-json", "code": None, "state": "warning",
                         "version": None, "summary": None, "items": []},
        },
        "coverage": [{"id": pkg, "label": hslib.ADDON_LABELS[pkg], "coverage": "unreadable" if pkg == "security" else "checked",
                      "gaps": []} for pkg in hslib.resolve_expect(["web"])],
    },
    "profile": WEB_PROFILE,
    "heartbeat": BEAT,
    "check": {"at": "2026-10-19T09:00:00Z", "channel": "ntfy", "target": TARGET, "token": True, "steps": STEPS,
              "access": {"state": "ok", "anon_read": "ok", "anon_write": "ok"}},
    "test_mail": {"at": "2026-10-18T09:05:00Z", "channel": "mail", "target": "ops@example.com",
                  "result": {"ok": True, "code": None, "reason": "sent"}},
    # Each channel as `show` judges it (lib/hschannels.py, health()).
    "channels": {"ntfy": {"state": "ok", "at": "2026-10-19T18:30:00Z", "via": "run", "recovered": None},
                 "mail": {"state": "ok", "at": "2026-10-19T18:30:00Z", "via": "run", "recovered": None}},
})
OPEN = copy.deepcopy(READY)
OPEN["last"]["access"].update(state="warning", anon_read="warning", anon_write="warning")
OPEN["check"]["steps"] = [dict(s, result="warning", reason="readable" if s["id"] == "anon-read" else "writable", code=200)
                          if s["id"] in ("anon-read", "anon-write") else s for s in STEPS]
OPEN["check"]["steps"][-1] = {"id": "token", "result": "info", "code": 200, "reason": "can-read"}
OPEN["last"]["delivery"]["results"]["ntfy"] = {"ok": False, "code": 403, "reason": "rejected"}
OPEN["channels"]["ntfy"] = {"state": "failed", "since": "2026-10-19T18:30:00Z", "at": "2026-10-19T18:30:00Z", "via": "run",
                            "reason": "rejected"}
OPEN["heartbeat"]["last"]["result"] = {"ok": False, "code": None, "reason": "timeout", "attempts": 3}
NOMTA = copy.deepcopy(READY)
NOMTA["smtp"] = {"listening": False, "sendmail": True}
NOMTA["test_mail"] = {"at": "2026-10-19T18:50:00Z", "channel": "mail", "target": "ops@example.com",
                      "result": {"ok": False, "code": None, "reason": "no-mta"}}
NOMTA["channels"]["mail"] = {"state": "failed", "since": "2026-10-19T18:50:00Z", "at": "2026-10-19T18:50:00Z", "via": "test",
                             "reason": "no-mta"}


def main():
    info = {"channels": ["ntfy", "mail"], "channel_health": OPEN["channels"],
            "access": {"state": "ok"}, "run": {"at": "2026-10-19T19:00:00Z", "addons": 4},
            "heartbeat": {"invalid": False, "at": BEAT["last"]["at"], "signal": "success", "result": BEAT["last"]["result"]}}
    status = hsstatus.status_file(info, hsstatus.version())
    # The status verb adds the profile from profile.conf (host-settings.py, do_status).
    profile = hslib.profile_answer(hslib.parse_profile("roles = web\nskip = update-gaps/cve: Network data off on purpose\n"
                                                       "set_at = 2026-10-18T09:00:00Z\n")[0])
    data = {"scenarios": {"fresh": FRESH, "ready": READY, "open": OPEN, "nomta": NOMTA, "menu": READY, "menu-noadmin": READY},
            "status": {lang: dict(hsstatus.localize(status, lang, hslib.parse_time(NOW)), features=["profile"], profile=profile)
                       for lang in ("en", "de")},
            "check": {"at": NOW, "channel": "ntfy", "target": TARGET, "token": True, "steps": STEPS,
                      "access": {"state": "ok", "anon_read": "ok", "anon_write": "ok"}},
            "menu": MENU}
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(os.path.join(OUT, "base1"))
    shutil.copytree(os.path.join(REPO, "plugin"), os.path.join(OUT, "host-settings"))
    with open(os.path.join(HERE, "cockpit-stub.js"), encoding="utf-8") as f:
        stub = f.read()
    stub = stub.replace("__DATA__", json.dumps(data, ensure_ascii=False)).replace("__NOW__", json.dumps(NOW))
    with open(os.path.join(OUT, "base1", "cockpit.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write(stub)
    with open(os.path.join(OUT, "base1", "po.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write(PO_STUB)
    with open(os.path.join(OUT, "manifests-i18n.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write(MANIFESTS_I18N)
    print("Preview:", os.path.join(OUT, "host-settings", "index.html") + "?scenario=ready")


if __name__ == "__main__":
    main()
