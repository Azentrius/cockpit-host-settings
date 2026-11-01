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

A missing prerequisite (finding 6379/tcp not open, no e2e-peer, no user) fails the test.
Environment: ADMIN_USER, ADMIN_PW, PLAIN_USER, PLAIN_PW, MANIFEST (the version manifest of the
run). Writes the screenshots, assertions.md, assertions.json and scenarios.json to the folder
given; exit 1 if a check failed.

    python3 test/e2e-suite/ui.py OUT/ui
"""
import json
import os
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
os.makedirs(OUT, exist_ok=True)

checks = []
problems = []
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


def watch(page, who):
    def ours(where):
        return "/status-board/" in (where or "") or "/security/" in (where or "")

    page.on("console", lambda m: problems.append([who, "console", m.text, (m.location or {}).get("url", "")])
            if m.type == "error" and ours((m.location or {}).get("url")) else None)
    page.on("pageerror", lambda e: problems.append([who, "pageerror", str(e), ""]) if ours(e.stack) else None)
    page.on("response", lambda r: problems.append([who, "http %d" % r.status, "", r.url])
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


def login(browser, user, pw):
    ctx = browser.new_context(ignore_https_errors=True, locale="en-US", viewport={"width": 1440, "height": 1000})
    ctx.add_cookies([{"name": "CockpitLang", "value": "en-us", "url": URL + "/"}])
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


def h1_board():
    """True when the board's part of H1 is checked: Status Board has the rule and the profile
    expects security/suite-unreported. Without the rule it is noted, or fails when the version
    manifest requires h1; a profile without the expectation fails (core.py step 12 sets it)."""
    try:
        with open(E.get("MANIFEST", ""), encoding="utf-8") as f:
            required = json.load(f).get("required_scenarios") or []
    except (OSError, ValueError):
        required = []
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

with open(os.path.join(OUT, "assertions.json"), "w", encoding="utf-8") as fh:
    json.dump(checks, fh, indent=1, ensure_ascii=False)
failed = [c for c in checks if "check" in c and not c["ok"]]
for s in scenarios.values():
    if s["result"] == "running":
        s["result"] = "failed" if any("H1" in c["check"] for c in failed) else "passed"
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
