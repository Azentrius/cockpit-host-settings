#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""E2E-1, step 10: what the core path (test/e2e-suite/core.py) left open, in a real Cockpit with
Status Board's real package and a second host over SSH (test/e2e-suite/setup.sh). Adapted from
Status Board's test/e2e_cockpit.py (the shell's frames, the tasks, the jumps) and Security
Monitor's test/e2e_session.py (Cockpit's login, administrative access, a user without it).
Playwright drives Chromium through Cockpit's own login on https://localhost:9090:

  1. As ADMIN_USER with administrative access the board opens on Needs attention: the finding
     6379/tcp of Security Monitor is a task of this host, a danger, and there is no finding task
     of e2e-peer; e2e-peer's target profile expects Security Monitor, which it does not have: a
     task of e2e-peer ("Security Monitor is missing", a warning) and none of this host.
  2. "Open in Security Monitor" on that task: the shell shows the Security Monitor page it had
     loaded hidden after the login (the same frame, not loaded again) at #/exposure/6379-tcp,
     with that port's row marked and in view.
  3. The tab Hosts and coverage with "Only what needs attention" on: e2e-peer's missing Security
     Monitor and its coverage line stay.
  4. As PLAIN_USER (no administrative access): the same task, and its jump opens Security Monitor
     with the summary only: no row of a port, none of the rules or addresses behind it; the board
     shows none of them either.
  5. No JavaScript error and no failed request of the board's or Security Monitor's files.

H1 (with a Status Board that has the rule, notReportedChecks in lib/status.js): the profile of
this host expects security/suite-unreported (core.py, step 12), which Security Monitor does not
report. In 1 that is a warning task of this host's coverage that names the check as not reported;
in 3, with "Only what needs attention" on, this host's Security Monitor tile stays and its
coverage line names the check as not reported. Without the rule this is noted and not checked, unless the version
manifest requires "h1".

The menu editor (scenario "menu", with a Host Settings that has it: hsmenu.py and the Cockpit tab)
runs after that with a browser of its own, also when the board's part could not. Cockpit's own
pages (Overview, Logs, Services) come from cockpit-system, which setup.sh installs:

  6. As ADMIN_USER with administrative access, in English: Host Settings' Cockpit tab, "For all
     users", lists every entry of the navigation under the name the navigation shows. Logs
     hidden, Services renamed and moved past the entry next to it, saved through the dialog
     (menu apply system): /etc/cockpit/systemd.override.json holds just that. "Reload Cockpit"
     reloads the whole shell, and its navigation has no Logs and Services under the new name, in
     its new place.
  7. As PLAIN_USER (no administrative access), in German: the navigation has the change for all
     users. The tab offers only "Just for me" and shows Cockpit's own labels translated, as the
     navigation does (../manifests-i18n.js answered 200 to the page's frame); Logs, hidden for all
     users, has no button. Services renamed just for this user, saved (menu apply user, as the
     user): the user's own file holds only that, and after "Reload Cockpit" the user's navigation
     shows that name over the one for all users.
  8. ADMIN_USER again: the name for all users, not the other user's own. Both files removed through
     empty plans of the helper; after a reload the navigation is the one before the test.
  9. No JavaScript error and no failed request of Host Settings' files.

Without the menu editor this is noted and not checked, unless the version manifest requires
"menu". The files are removed at the end also when a check failed.

A missing prerequisite (finding 6379/tcp not open, no e2e-peer, no user) fails the test.
Environment: ADMIN_USER, ADMIN_PW, PLAIN_USER, PLAIN_PW, MANIFEST (the version manifest of the
run). Writes the screenshots, assertions.md, assertions.json and scenarios.json to the folder
given; exit 1 if a check failed.

    python3 test/e2e-suite/ui.py OUT/ui
"""
import json
import os
import pwd
import re
import subprocess
import sys
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

URL = "https://localhost:9090"
E = os.environ
OUT = sys.argv[1] if len(sys.argv) > 1 else "e2e-suite/ui"
BOARD = 'iframe[name="cockpit1:localhost/status-board"]'
SECURITY_NAME = "cockpit1:localhost/security"
SECURITY = 'iframe[name="%s"]' % SECURITY_NAME
SM = "/usr/lib/cockpit-security-monitor/security.py"
MACHINES = "/etc/cockpit/machines.d/99-e2e-suite.json"
FID = "port.6379-tcp"
TASK = "localhost/security/finding:" + FID
GAP = "e2e-peer/security/coverage"
ROW = "sm-port-tcp-6379"
LINK = "#/exposure/6379-tcp"
# What only administrative access shows: the public address and the chains of the rules.
PROTECTED = ("203.0.113.10", "ufw-user-input", "ufw-before-input")
BOARD_RULES = "/usr/share/cockpit/status-board/lib/status.js"
PROFILE = "/etc/cockpit-host-settings/profile.conf"
UNREPORTED = "suite-unreported"              # core.py, step 12
GAP_HERE = "localhost/security/coverage"
# The menu editor (scenario "menu"): Host Settings' Cockpit tab and Cockpit's own pages.
MENU = "Menu editor"                         # the start of every check of the scenario
HS = "/usr/lib/cockpit-host-settings/host-settings.py"
HS_MENU = "/usr/lib/cockpit-host-settings/hsmenu.py"
HS_PAGE = "/usr/share/cockpit/host-settings/index.html"
HS_NAME = "cockpit1:localhost/host-settings"
HS_FRAME = 'iframe[name="%s"]' % HS_NAME
SYSTEM_FILE = "/etc/cockpit/systemd.override.json"
OVERVIEW, LOGS, SERVICES = "systemd/menu/index", "systemd/menu/logs", "systemd/menu/services"
FOR_ALL, FOR_ME = "Units for all", "My units"  # the names Services gets for all users, just for PLAIN_USER
# Cockpit's default folders for the helper, as the bridge of a Cockpit session has them.
CLEAN_ENV = ["env", "-u", "XDG_CONFIG_HOME", "-u", "XDG_CONFIG_DIRS", "-u", "XDG_DATA_HOME", "-u", "XDG_DATA_DIRS"]
os.makedirs(OUT, exist_ok=True)

checks = []
problems = []
menu_problems = []
shots = [0]
LAST = {"page": None}
scenarios = {}


def check(ok, what, detail=""):
    detail = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)
    checks.append({"check": what, "ok": bool(ok), "detail": detail[:2000]})
    print(("ok    " if ok else "FAIL  ") + what + ("" if ok else ": " + detail[:800]), flush=True)
    if not ok and E.get("GITHUB_ACTIONS"):
        print("::error title=E2E-1 step 10::" + (what + ": " + detail).replace("\n", " ")[:900], flush=True)
    return bool(ok)


def note(text):
    checks.append({"note": text})
    print("      " + text, flush=True)


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def shot(page, label):
    shots[0] += 1
    page.screenshot(path=os.path.join(OUT, "%02d-%s.png" % (shots[0], label)), full_page=True)


def watch(page, who, paths=("/status-board/", "/security/"), into=None):
    """JavaScript errors and failed requests of the files below paths, into a list (problems)."""
    into = problems if into is None else into

    def ours(where):
        return any(p in (where or "") for p in paths)

    page.on("console", lambda m: into.append([who, "console", m.text, (m.location or {}).get("url", "")])
            if m.type == "error" and ours((m.location or {}).get("url")) else None)
    page.on("pageerror", lambda e: into.append([who, "pageerror", str(e), ""]) if ours(e.stack) else None)
    page.on("response", lambda r: into.append([who, "http %d" % r.status, "", r.url])
            if r.status >= 400 and ours(r.url) else None)


def wait_js(target, expr, timeout=30):
    """True once expr holds in the page or frame (Cockpit's CSP blocks wait_for_function's eval,
    so this polls with evaluate, as Status Board's e2e test does)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if target.evaluate("() => !!(" + expr + ")"):
                return True
        except Exception:  # noqa: BLE001 - a frame that navigates answers again a moment later
            pass
        time.sleep(0.1)
    return False


def poll(fn, seconds):
    end = time.time() + seconds
    while True:
        try:
            v = fn()
        except Exception:  # noqa: BLE001
            v = None
        if v or time.time() > end:
            return v
        time.sleep(0.25)


def shell_frame(page, selector, timeout=45):
    """The shell's iframe that matches selector once it has loaded (data-loaded), or None."""
    try:
        el = page.wait_for_selector(selector + "[data-loaded]", state="attached", timeout=timeout * 1000)
    except PlaywrightTimeout:
        return None
    return page.frame(name=el.get_attribute("name"))


def wait_idle(b, timeout=90):
    """True once the board has loaded every host (Refresh is enabled again)."""
    done = wait_js(b, "document.querySelector('#refresh') && !document.querySelector('#refresh').disabled", timeout)
    time.sleep(0.5)
    return done


def tasks(b):
    return b.evaluate("""() => Array.from(document.querySelectorAll('.sb-task')).map(t => ({
        key: t.dataset.task,
        group: (t.closest('.sb-group') || { dataset: {} }).dataset.group || null,
        title: ((t.querySelector('.sb-task__title') || {}).innerText || '').trim(),
        where: ((t.querySelector('.sb-task__where') || {}).innerText || '').trim(),
        detail: ((t.querySelector('.sb-task__detail') || {}).innerText || '').trim(),
        sub: ((t.querySelector('.sb-task__sub') || {}).innerText || '').trim()}))""")


def login(browser, user, pw, lang="en-us"):
    """A new browser session of user in Cockpit, in English or (lang "de-de") in German."""
    ctx = browser.new_context(ignore_https_errors=True, locale="de-DE" if lang == "de-de" else "en-US",
                              viewport={"width": 1440, "height": 1000})
    ctx.add_cookies([{"name": "CockpitLang", "value": lang, "url": URL + "/"}])
    page = ctx.new_page()
    page.set_default_timeout(90000)
    watch(page, user)
    LAST["page"] = page
    page.goto(URL + "/", wait_until="domcontentloaded")
    page.fill("#login-user-input", user)
    page.fill("#login-password-input", pw)
    page.click("#login-button")
    page.wait_for_selector("iframe, #login-error-message:not(:empty)", state="attached")
    if not page.locator("iframe").count():
        raise RuntimeError("login of %s refused: %s" % (user, page.locator("#login-error-message").inner_text()))
    return ctx, page


def access_button(page):
    b = page.get_by_role("button", name=re.compile(r"^(Limited|Administrative) access$"))
    b.first.wait_for()
    return b.first


def admin_access(page, pw):
    if "Limited" in access_button(page).inner_text():
        access_button(page).click()
        dlg = page.get_by_role("dialog")
        dlg.locator("input[type=password]").fill(pw)
        dlg.get_by_role("button", name=re.compile("Authenticate")).click()
    page.get_by_role("button", name="Administrative access").wait_for()
    return True


def open_board(page):
    page.goto(URL + "/status-board")
    b = shell_frame(page, BOARD)
    if b is None:
        shot(page, "no-board")
        raise RuntimeError("the shell did not load Status Board (frame cockpit1:localhost/status-board)")
    check(wait_idle(b), "the board has loaded every host")
    return b


def jump_from_task(page, b):
    """Clicks "Open in Security Monitor" on the task of finding 6379/tcp; the Security Monitor frame
    once the shell shows it at the finding's link, or None."""
    b.click('[data-task="%s"] .sb-task__act button' % TASK)
    ok = wait_js(page, "location.pathname === '/security' && location.hash === '%s'" % LINK, 30)
    check(ok, "the shell goes to Security Monitor at %s" % LINK, page.url)
    return page.frame(name=SECURITY_NAME)


def prerequisites():
    missing = [k for k in ("ADMIN_USER", "ADMIN_PW", "PLAIN_USER", "PLAIN_PW") if not E.get(k)]
    check(not missing, "prerequisite: the users of the test are given", missing)
    p = subprocess.run(["sudo", "-n", "runuser", "-u", "nobody", "--", SM, "status", "--lang", "en"],
                       capture_output=True, text=True)
    try:
        doc = json.loads(p.stdout)
    except ValueError:
        doc = {}
    item = next((i for i in doc.get("items") or [] if i.get("id") == "exposure"), {})
    found = item.get("findings") if isinstance(item.get("findings"), dict) else {}
    f = next((x for x in found.get("list") or [] if isinstance(x, dict) and x.get("fid") == FID), {})
    check(f.get("state") == "danger", "prerequisite: finding %s is open in Security Monitor's status (core path)" % FID,
          {"exit": p.returncode, "finding": f, "stderr": p.stderr[-300:]})
    check('"e2e-peer"' in read(MACHINES), "prerequisite: e2e-peer in the host switcher's list", MACHINES)
    return all(c["ok"] for c in checks if "check" in c)


def required_scenarios():
    """The scenarios the version manifest of the run requires."""
    try:
        with open(E.get("MANIFEST", ""), encoding="utf-8") as f:
            return json.load(f).get("required_scenarios") or []
    except (OSError, ValueError):
        return []


def h1_board():
    """True when the board's part of H1 is checked: Status Board has the rule and the profile
    expects security/suite-unreported. Without the rule it is noted, or fails when the version
    manifest requires h1; a profile without the expectation fails (core.py step 12 sets it)."""
    required = required_scenarios()
    what = "H1 on the board, a check the profile names that the add-on does not report"
    if "notReportedChecks" not in read(BOARD_RULES):
        if "h1" in required:
            scenarios["h1-board"] = {"result": "failed", "what": what, "why": "required, not supported"}
            check(False, "%s: the Status Board of this run has the rule (the version manifest requires h1)" % what)
        else:
            scenarios["h1-board"] = {"result": "not supported", "what": what,
                                     "why": "the Status Board of this run does not have the rule; not required by the manifest"}
            note("%s: not checked, the Status Board of this run does not have the rule (not required by the manifest)" % what)
        return False
    expected = any(line.split("=", 1)[-1].strip() == "security/" + UNREPORTED
                   for line in read(PROFILE).splitlines() if line.strip().startswith("expect"))
    scenarios["h1-board"] = {"result": "running", "what": what}
    check(expected, "prerequisite of H1: the profile expects security/%s (core.py, step 12)" % UNREPORTED, read(PROFILE))
    return expected


def main():
    hostname = read("/proc/sys/kernel/hostname").strip()
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # 1. The administrator: the tasks of both hosts.
        ctx, page = login(browser, E["ADMIN_USER"], E["ADMIN_PW"])
        check(admin_access(page, E["ADMIN_PW"]), "%s: administrative access" % E["ADMIN_USER"])
        b = open_board(page)
        check(b.evaluate("document.querySelector('#tab-tasks').getAttribute('aria-selected')") == "true",
              "the board opens on Needs attention")
        ts = tasks(b)
        keys = {t["key"]: t for t in ts}
        note("tasks: " + json.dumps([[t["group"], t["key"]] for t in ts]))
        for t in ts:
            if t["key"].endswith(("/unreachable", "/locked", "/discovery")):
                note("%s: %s %s %s" % (t["key"], t["title"], t["detail"], t["sub"]))
        check(not [t["key"] for t in ts if t["key"].startswith("e2e-peer/") and t["key"].count("/") == 1
                   and t["key"] != "e2e-peer/profile"], "the board reaches e2e-peer over SSH",
              [t for t in ts if t["key"].startswith("e2e-peer/") and t["key"].count("/") == 1])
        t = keys.get(TASK) or {}
        check(t.get("group") == "danger", "finding 6379/tcp: a task of this host (localhost), a danger", t)
        check(hostname in t.get("where", "") and "Security Monitor" in t.get("where", ""),
              "the task names this host (%s) and Security Monitor" % hostname, t.get("where"))
        check(not [k for k in keys if k.startswith("e2e-peer/") and "/finding:" in k], "no finding task on e2e-peer",
              [k for k in keys if k.startswith("e2e-peer/")])
        g = keys.get(GAP) or {}
        check(g.get("group") == "warning" and "Security Monitor" in g.get("title", ""),
              "e2e-peer: Security Monitor missing although its profile expects it, a warning task", g)
        here = keys.get(GAP_HERE) or {}
        check(g.get("title") and here.get("title") != g.get("title"), "on this host Security Monitor is not missing", here)
        h1 = h1_board()
        if h1:
            check(here.get("group") == "warning" and UNREPORTED in here.get("title", "") and "not reported" in here.get("title", ""),
                  "H1: this host's coverage task, a warning, names %s as not reported" % UNREPORTED, here)
            note("H1: the task: %s / %s / %s" % (here.get("title"), here.get("detail"), here.get("sub")))
        shot(page, "tasks-admin")

        # 2. The jump into the open frame, to the port.
        sm = shell_frame(page, SECURITY)
        check(sm is not None and not page.locator(SECURITY).is_visible(),
              "the shell has loaded Security Monitor's page hidden (for its menu icon) before the jump")
        if sm is not None:
            sm.evaluate("() => { window.e2eSuiteMark = true; }")
        f = jump_from_task(page, b)
        check(f is not None and f == sm, "the same frame shows it", (f.url if f else "no frame"))
        if f is not None:
            marked = poll(lambda: f.evaluate("() => { const r = document.getElementById('%s');"
                                             " return r && r.getAttribute('aria-current') === 'location'; }" % ROW), 90)
            check(marked, "the row of 6379/tcp is marked (aria-current=location)")
            time.sleep(1)
            in_view = f.evaluate("() => { const r = document.getElementById('%s'); if (!r) return false;"
                                 " const b = r.getBoundingClientRect(); return b.top >= 0 && b.top < window.innerHeight; }" % ROW)
            kept = f.evaluate("() => window.e2eSuiteMark === true")
            check(bool(in_view) and kept and page.locator(SECURITY).is_visible(),
                  "in view, in the frame that was open, not loaded again", {"in_view": in_view, "not_reloaded": kept})
            body = f.evaluate("() => document.body.innerText")
            row = f.evaluate("() => (document.getElementById('%s') || {}).innerText || ''" % ROW)
            note("the row with administrative access: " + " ".join(row.split())[:300])
            note("details only administrative access shows, on the page now: %s" % [x for x in PROTECTED if x in body])
        shot(page, "port-admin")

        # 3. Hosts and coverage, only what needs attention.
        if f is not None:
            f.evaluate("() => cockpit.jump('/status-board#/hosts')")
        else:
            page.goto(URL + "/status-board#/hosts")
        check(wait_js(page, "location.pathname === '/status-board'", 20), "back on the board", page.url)
        b = shell_frame(page, BOARD)
        check(b is not None and wait_js(b, "location.hash === '#/hosts' && document.querySelector('.sb-host')", 30),
              "the tab Hosts and coverage")
        if b is not None:
            if not b.evaluate("document.querySelector('#only-notable').checked"):
                b.click("#notable-wrap")
            check(wait_js(b, "document.querySelector('#board').dataset.onlyNotable === 'true'", 10),
                  "Only what needs attention is on")
            tile = b.evaluate("""() => { const t = document.querySelector('[data-key="e2e-peer/security"]');
                if (!t) return null; const l = t.querySelector('.sb-tile__head .az-label');
                return { text: t.innerText.trim(), cls: l ? l.className : '' }; }""")
            check(bool(tile) and "az-label--warning" in tile.get("cls", ""),
                  "with the switch on, e2e-peer still shows Security Monitor as missing, a warning", tile)
            line = b.evaluate("""() => { const s = document.querySelector('[data-host="e2e-peer"] .sb-host__figure');
                return s ? s.innerText.trim() : null; }""")
            check(bool(line) and "Security Monitor" in line, "and its coverage line names the gap", line)
            if h1:
                mine = b.evaluate("""() => { const t = document.querySelector('[data-key="localhost/security"]');
                    if (!t) return null; const l = t.querySelector('.sb-tile__head .az-label');
                    const h = t.closest('[data-host]'); const f = h ? h.querySelector('.sb-host__figure') : null;
                    return { cls: l ? l.className : '', line: f ? f.innerText.trim() : null }; }""")
                # The tile itself is a danger here (finding 6379/tcp); the gap is in the coverage line.
                check(bool(mine) and "%s not reported" % UNREPORTED in (mine.get("line") or ""),
                      "H1: with the switch on, this host's Security Monitor stays and its coverage line names %s as not "
                      "reported" % UNREPORTED, mine)
            summary = b.evaluate("() => (document.querySelector('#coverage') || {}).innerText || ''")
            note("the board's coverage: " + " ".join(summary.split())[:300])
        shot(page, "hosts-only-notable")
        ctx.close()

        # 4. Without administrative access.
        ctx, page = login(browser, E["PLAIN_USER"], E["PLAIN_PW"])
        check("Limited" in access_button(page).inner_text(), "%s: limited access" % E["PLAIN_USER"])
        b = open_board(page)
        keys = {t["key"]: t for t in tasks(b)}
        check(keys.get(TASK, {}).get("group") == "danger", "the same task without administrative access (the status needs no root)",
              keys.get(TASK))
        text = b.evaluate("() => document.body.innerText")
        check(not [x for x in PROTECTED if x in text], "the board shows none of the protected details",
              [x for x in PROTECTED if x in text])
        shot(page, "tasks-plain")
        f = jump_from_task(page, b) if TASK in keys else None
        if f is not None:
            limited = poll(lambda: "Shown without administrative access" in f.evaluate(
                "() => (document.getElementById('sm-status') || {}).innerText || ''"), 60)
            check(limited, "Security Monitor says it is shown without administrative access")
            rows = f.evaluate("() => document.querySelectorAll('tr[id^=\"sm-port-\"]').length")
            body = f.evaluate("() => document.body.innerText")
            check(rows == 0 and not [x for x in PROTECTED if x in body],
                  "no row of a port, none of the rules or addresses behind the finding",
                  {"rows": rows, "found": [x for x in PROTECTED if x in body]})
            note("what it shows: " + " ".join(body.split())[:400])
        shot(page, "port-plain")
        ctx.close()
        browser.close()


# ---------------------------------------------------------------------------
# The menu editor (scenario "menu")

def menu_scenario():
    """True when the Host Settings of this run has the menu editor (hsmenu.py and the Cockpit tab).
    Without it this is noted, or fails when the version manifest requires "menu"."""
    what = "Menu editor, Cockpit's navigation changed through its override files in a real Cockpit"
    if os.path.exists(HS_MENU) and "hs-tab-cockpit" in read(HS_PAGE):
        scenarios["menu"] = {"result": "running", "what": what}
        return True
    if "menu" in required_scenarios():
        scenarios["menu"] = {"result": "failed", "what": what, "why": "required, not supported"}
        check(False, "%s: the Host Settings of this run has it (the version manifest requires menu)" % MENU)
    else:
        scenarios["menu"] = {"result": "not supported", "what": what,
                             "why": "the Host Settings of this run has no menu editor; not required by the manifest"}
        note("%s: not checked, the Host Settings of this run has none (not required by the manifest)" % MENU)
    return False


def as_root(argv, user=None, stdin=None):
    """argv through sudo, as root or (runuser) as user, with Cockpit's default XDG folders."""
    prefix = ["sudo", "-n"] + (["runuser", "-u", user, "--"] if user else []) + CLEAN_ENV
    return subprocess.run(prefix + argv, input=stdin, capture_output=True, text=True)


def nav(page):
    """The shell's navigation of this host in its order, [[path, label], ...]: the path of each
    link without a host or a hash (/system/logs), the name it shows."""
    return page.evaluate("""() => Array.from(document.querySelectorAll('#host-apps li a[href]')).map((a) => [
        a.getAttribute('href').split('#')[0].replace('/@localhost/', '/'),
        ((a.querySelector('.nav-item-name') || a).textContent || '').trim()])""")


def nav_shown(page, timeout=60):
    """The navigation once it lists Cockpit's Overview (/system), or [] after timeout seconds."""
    def ready():
        n = nav(page)
        return n if any(p == "/system" for p, _ in n) else None
    return poll(ready, timeout) or []


def open_editor(page):
    """Host Settings at its Cockpit tab (/host-settings/cockpit) once the menu editor lists
    Cockpit's Services: its frame."""
    page.goto(URL + "/host-settings/cockpit")
    f = shell_frame(page, HS_FRAME)
    if f is None:
        shot(page, "no-host-settings")
        raise RuntimeError("the shell did not load Host Settings (frame %s)" % HS_NAME)
    if not wait_js(f, "document.querySelector('tr[data-id=\"%s\"]')" % SERVICES, 90):
        shot(page, "no-menu")
        raise RuntimeError("the Cockpit tab does not list %s: %s" % (SERVICES, f.evaluate("() => document.body.innerText")[:600]))
    return f


def menu_rows(f, section):
    """The editor's list of a group (dashboard, menu, tools) in its order: id, label, off (hidden),
    path (the shell's link of the package and key the row names), buttons (enabled ones)."""
    return f.evaluate("""(s) => {
        const head = document.getElementById('hs-menu-' + s);
        const list = head && head.nextElementSibling;
        if (!list || !list.querySelectorAll) return [];
        return Array.from(list.querySelectorAll('tr[data-id]')).map((r) => {
            const cell = r.querySelector('td');
            const subs = cell ? Array.from(cell.querySelectorAll('.hs-sub')) : [];
            const where = (subs.length ? subs[subs.length - 1].textContent : '').split(' · ');
            const name = cell ? cell.querySelector('strong') : null;
            return { id: r.dataset.id, label: (name ? name.textContent : '').trim(), off: r.classList.contains('is-off'),
                     path: '/' + where[0] + (where[1] === 'index' ? '' : '/' + where[1]),
                     buttons: r.querySelectorAll('button[data-menu]:not(:disabled)').length };
        });
    }""", section)


def unlike_nav(f, shown):
    """How many entries the editor shows that the navigation has as well, and those whose label
    differs there: (count, [[path, editor, navigation], ...])."""
    labels = dict(shown)
    count, differ = 0, []
    for section in ("dashboard", "menu", "tools"):
        for r in menu_rows(f, section):
            if r["off"] or r["path"] not in labels:
                continue
            count += 1
            if r["label"] != labels[r["path"]]:
                differ.append([r["path"], r["label"], labels[r["path"]]])
    return count, differ


def rename(f, entry, name):
    """"Rename" on an entry, the name given: (the name the dialog offered, whether it closed)."""
    f.click('tr[data-id="%s"] button[data-menu="rename"]' % entry)
    f.wait_for_selector("#hs-dialog[open] #hs-f-label")
    was = f.input_value("#hs-f-label")
    f.fill("#hs-f-label", name)
    f.click("#hs-dialog-confirm")
    return was, wait_js(f, "!document.getElementById('hs-dialog').open", 10)


def save_menu(f):
    """"Save changes", then Save in the dialog: (whether it succeeded, the changes in words, the
    dialog's result and log)."""
    f.click("#hs-menu-save")
    f.wait_for_selector("#hs-dialog[open] #hs-dialog-confirm")
    words = f.evaluate("() => Array.from(document.querySelectorAll('#hs-menu-changes li')).map((li) => li.textContent)")
    f.click("#hs-dialog-confirm")
    done = wait_js(f, "document.querySelector('#hs-dialog-result.is-ok, #hs-dialog-result.is-error')", 90)
    out = f.evaluate("""() => ({ ok: !!document.querySelector('#hs-dialog-result.is-ok'),
        result: document.getElementById('hs-dialog-result').textContent,
        log: document.getElementById('hs-dialog-log').textContent })""")
    f.click("#hs-dialog-cancel")
    return done and out["ok"], words, out


def reload_cockpit(page, f):
    """The page's "Reload Cockpit": True once the whole shell is a new document with its
    navigation (window.top reloaded from the page's frame)."""
    if not wait_js(f, "document.getElementById('hs-menu-reload')", 30):
        return False
    page.evaluate("() => { window.e2eMenuMark = true; }")
    try:
        f.click("#hs-menu-reload")
    except Exception:  # noqa: BLE001 - the frame goes away with the shell it reloads
        pass
    return wait_js(page, "!window.e2eMenuMark && document.querySelector('#host-apps li a[href]')", 90)


def frame_name(response):
    try:
        return response.frame.name
    except Exception:  # noqa: BLE001 - a request without a frame
        return ""


def menu_reset():
    """The override files of systemd the test made, removed through empty plans of the helper: as
    root for all users, as PLAIN_USER just for that user. What could not be removed."""
    left = []
    for scope, user in (("system", None), ("user", E.get("PLAIN_USER"))):
        show = as_root([HS, "menu", "show"], user)
        try:
            files = json.loads(show.stdout).get("files") or []
        except ValueError:
            left.append("%s: menu show: %s" % (scope, show.stderr.strip()[-300:]))
            continue
        for f in files:
            if f.get("scope") != scope or f.get("dir") != "systemd":
                continue
            plan = json.dumps({"base": {"systemd": f.get("sha256") or ""}, "set": {}})
            r = as_root([HS, "menu", "apply", scope], user, plan + "\n")
            if r.returncode:
                left.append("%s: exit %d: %s" % (scope, r.returncode, (r.stdout + r.stderr).strip()[-300:]))
            elif as_root(["test", "-e", str(f.get("path"))]).returncode == 0:
                left.append("%s: %s is still there" % (scope, f.get("path")))
    return left


def menu_editor():
    """The menu editor in a real Cockpit, with a browser of its own; a screenshot when a step
    could not run."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            menu_steps(browser)
        except Exception:
            try:
                if LAST["page"] is not None:
                    shot(LAST["page"], "menu-failed")
            except Exception:  # noqa: BLE001
                pass
            raise
        browser.close()


def menu_steps(browser):
    """Steps 6 to 8 of the docstring."""
    plain_file = os.path.join(pwd.getpwnam(E["PLAIN_USER"]).pw_dir, ".config", "cockpit", "systemd.override.json")
    # 6. The administrator, in English: Logs hidden, Services renamed and moved, for all users.
    ctx, page = login(browser, E["ADMIN_USER"], E["ADMIN_PW"])
    watch(page, E["ADMIN_USER"], ("/host-settings/",), menu_problems)
    before = nav_shown(page)
    note("%s: the navigation before: %s" % (MENU, json.dumps(before, ensure_ascii=False)))
    check(admin_access(page, E["ADMIN_PW"]), "%s: %s, administrative access" % (MENU, E["ADMIN_USER"]))
    labels = dict(before)
    check("/system/logs" in labels and "/system/services" in labels and not os.path.exists(SYSTEM_FILE),
          "%s: Cockpit's navigation has Logs and Services, and there is no %s" % (MENU, SYSTEM_FILE), before)
    f = open_editor(page)
    pressed = f.evaluate("() => { const b = document.getElementById('hs-menu-scope-system');"
                         " return b ? b.getAttribute('aria-pressed') : null; }")
    check(pressed == "true", "%s: with administrative access the tab changes the menu for all users" % MENU, pressed)
    count, differ = unlike_nav(f, before)
    check(count >= 3 and not differ, "%s: the tab shows each entry the navigation has, under the name it has there"
          % MENU, {"entries": count, "differ": differ})
    f.click('tr[data-id="%s"] button[data-menu="hide"]' % LOGS)
    check(wait_js(f, "document.querySelector('tr[data-id=\"%s\"].is-off')" % LOGS, 10),
          "%s: Hide leaves Logs in its place, greyed" % MENU)
    was, closed = rename(f, SERVICES, FOR_ALL)
    check(closed and was == labels.get("/system/services"), "%s: Rename offers the name Services has and takes %s"
          % (MENU, FOR_ALL), {"offered": was, "closed": closed})
    # Moved past the entry next to it that the navigation shows (down, else up).
    system = menu_rows(f, "menu")
    k = [r["id"] for r in system].index(SERVICES)
    step, other = None, None
    if k + 1 < len(system) and not system[k + 1]["off"] and system[k + 1]["path"] in labels:
        step, other = "down", system[k + 1]
    elif k > 0 and not system[k - 1]["off"] and system[k - 1]["path"] in labels:
        step, other = "up", system[k - 1]
    if other:
        f.click('tr[data-id="%s"] button[data-menu="%s"]' % (SERVICES, step))
        now = [r["id"] for r in menu_rows(f, "menu")]
        check((now.index(SERVICES) > now.index(other["id"])) == (step == "down"),
              "%s: Move %s puts %s past %s in the list" % (MENU, step, FOR_ALL, other["label"]), now)
    else:
        note("%s: no entry shown next to Services; its order is not checked" % MENU)
    ok, words, out = save_menu(f)
    note("%s: the dialog: %s" % (MENU, " / ".join(words)))
    check(ok, "%s: Save with administrative access (menu apply system)" % MENU, out)
    check(any("Logs" in w for w in words) and any(FOR_ALL in w for w in words),
          "%s: the dialog names the changes in words" % MENU, words)
    try:
        doc = json.loads(read(SYSTEM_FILE))
    except ValueError:
        doc = None
    sec = doc.get("menu") if isinstance(doc, dict) and isinstance(doc.get("menu"), dict) else {}
    svc = sec.get("services") if isinstance(sec.get("services"), dict) else {}
    check(isinstance(doc, dict) and list(doc) == ["menu"] and sorted(sec) == ["logs", "services"] and sec["logs"] is None
          and svc.get("label") == FOR_ALL and set(svc) <= {"order", "label"} and ("order" in svc) == bool(other),
          "%s: %s holds Logs hidden and the name%s of Services, nothing else"
          % (MENU, SYSTEM_FILE, " and the order" if other else ""), read(SYSTEM_FILE))
    check(reload_cockpit(page, f), "%s: Reload Cockpit reloads the whole shell from the page's frame" % MENU)
    after = nav_shown(page)
    labels = dict(after)
    paths = [x[0] for x in after]
    check("/system/logs" not in labels and labels.get("/system/services") == FOR_ALL,
          "%s: the navigation no longer shows Logs and shows Services as %s" % (MENU, FOR_ALL), after)
    if other:
        check("/system/services" in paths and other["path"] in paths
              and (paths.index("/system/services") > paths.index(other["path"])) == (step == "down"),
              "%s: and %s %s %s, as moved" % (MENU, FOR_ALL, "after" if step == "down" else "before", other["label"]),
              after)
    shot(page, "menu-admin")
    ctx.close()

    # 7. Without administrative access, in German: just for this user.
    ctx, page = login(browser, E["PLAIN_USER"], E["PLAIN_PW"], "de-de")
    watch(page, E["PLAIN_USER"], ("/host-settings/",), menu_problems)
    i18n = []
    page.on("response", lambda r: i18n.append([r.status, r.url])
            if r.url.split("?")[0].endswith("/manifests-i18n.js") and frame_name(r) == HS_NAME else None)
    shown = nav_shown(page)
    labels = dict(shown)
    check("/system/logs" not in labels and labels.get("/system/services") == FOR_ALL,
          "%s: %s sees the change for all users" % (MENU, E["PLAIN_USER"]), shown)
    f = open_editor(page)
    scope = f.evaluate("""() => ({ buttons: document.querySelectorAll('#hs-menu-scope-system, #hs-menu-scope-user').length,
        text: (document.getElementById('hs-menu') || {}).textContent || '' })""")
    check(scope["buttons"] == 0 and "Nur für mich" in scope["text"],
          "%s: without administrative access the scope is fixed to Just for me (Nur für mich)" % MENU, scope["text"][:400])

    def translated():
        n, d = unlike_nav(f, shown)
        first = next((r for r in menu_rows(f, "menu") if r["id"] == OVERVIEW), {})
        return [n, first.get("label")] if n >= 3 and not d and first.get("label") not in (None, "", "Overview") else None

    got = poll(translated, 30)
    count, differ = unlike_nav(f, shown)
    check(bool(got), "%s: in German the tab shows Cockpit's own labels translated, as the navigation does" % MENU,
          {"entries": count, "differ": differ, "overview": labels.get("/system")})
    note("%s: Overview in German: %s" % (MENU, labels.get("/system")))
    check(any(s == 200 for s, _ in i18n), "%s: ../manifests-i18n.js answered the page's frame with 200" % MENU, i18n)
    logs = next((r for r in menu_rows(f, "menu") if r["id"] == LOGS), {})
    check(logs.get("off") is True and logs.get("buttons") == 0,
          "%s: Logs, hidden for all users, cannot be shown just for this user" % MENU, logs)
    was, closed = rename(f, SERVICES, FOR_ME)
    check(closed and was == FOR_ALL, "%s: Rename offers the name for all users and takes %s" % (MENU, FOR_ME),
          {"offered": was, "closed": closed})
    ok, words, out = save_menu(f)
    note("%s: the dialog: %s" % (MENU, " / ".join(words)))
    check(ok, "%s: Save without administrative access, as the user (menu apply user)" % MENU, out)
    r = as_root(["cat", plain_file])
    try:
        mine = json.loads(r.stdout)
    except ValueError:
        mine = None
    owner = as_root(["stat", "-c", "%U %a", plain_file]).stdout.strip()
    check(mine == {"menu": {"services": {"label": FOR_ME}}} and owner == "%s 644" % E["PLAIN_USER"],
          "%s: %s holds only the user's name of Services, and is the user's (0644)" % (MENU, plain_file),
          {"file": r.stdout[:300], "owner": owner})
    check(reload_cockpit(page, f), "%s: Reload Cockpit without administrative access" % MENU)
    shown = nav_shown(page)
    labels = dict(shown)
    check("/system/logs" not in labels and labels.get("/system/services") == FOR_ME,
          "%s: %s's navigation shows Services as %s, over the name for all users" % (MENU, E["PLAIN_USER"], FOR_ME), shown)
    shot(page, "menu-plain-de")
    ctx.close()

    # 8. The administrator again: the other user's name stays with that user; then back.
    ctx, page = login(browser, E["ADMIN_USER"], E["ADMIN_PW"])
    watch(page, E["ADMIN_USER"], ("/host-settings/",), menu_problems)
    shown = nav_shown(page)
    check(dict(shown).get("/system/services") == FOR_ALL,
          "%s: %s still sees %s, not the name just for %s" % (MENU, E["ADMIN_USER"], FOR_ALL, E["PLAIN_USER"]), shown)
    left = menu_reset()
    check(not left, "%s: empty plans of the helper remove both files" % MENU, left)
    page.reload()
    again = nav_shown(page)
    check(again == before, "%s: after a reload the navigation is Cockpit's own again, as before the test" % MENU,
          {"before": before, "now": again})
    ctx.close()


try:
    if prerequisites():
        main()
except Exception as e:  # noqa: BLE001 - a step that could not run fails
    check(False, "step 10 ran to its end", "%s: %s" % (type(e).__name__, e))
    try:
        if LAST["page"] is not None:
            shot(LAST["page"], "failed")
    except Exception:  # noqa: BLE001
        pass
check(not problems, "no JavaScript error and no failed request of the board's or Security Monitor's files", problems)

# The menu editor, after the board's part and also when that could not run; its files go at the end
# in any case.
try:
    if menu_scenario():
        try:
            menu_editor()
        finally:
            leftover = menu_reset()
            check(not leftover, "%s: no override file of the test is left" % MENU, leftover)
        check(not menu_problems, "%s: no JavaScript error and no failed request of Host Settings' files" % MENU, menu_problems)
except Exception as e:  # noqa: BLE001 - a step that could not run fails
    check(False, "%s: ran to its end" % MENU, "%s: %s" % (type(e).__name__, e))

with open(os.path.join(OUT, "assertions.json"), "w", encoding="utf-8") as fh:
    json.dump(checks, fh, indent=1, ensure_ascii=False)
failed = [c for c in checks if "check" in c and not c["ok"]]
for name, s in scenarios.items():
    if s["result"] == "running":
        mine = [c for c in failed if (c["check"].startswith(MENU) if name == "menu" else "H1" in c["check"])]
        s["result"] = "failed" if mine else "passed"
with open(os.path.join(OUT, "scenarios.json"), "w", encoding="utf-8") as fh:
    json.dump(scenarios, fh, indent=1, ensure_ascii=False)
md = ["## E2E-1, step 10 (real Cockpit)", "",
      "%d checks, %d failed." % (sum(1 for c in checks if "check" in c), len(failed)), "",
      "| Check | Result |", "|---|---|"]
for c in checks:
    cell = (c.get("check") or c.get("note") or "").replace("|", "\\|").replace("\n", " ")
    if "check" in c:
        md.append("| %s | %s |" % (cell, "ok" if c["ok"] else "FAILED: " + c["detail"][:400].replace("|", "\\|").replace("\n", " ")))
    else:
        md.append("| %s | note |" % cell[:600])
with open(os.path.join(OUT, "assertions.md"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(md) + "\n")
print("\nE2E-1 step 10: %s" % ("PASSED" if not failed else "FAILED"))
sys.exit(1 if failed else 0)
