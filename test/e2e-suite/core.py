#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""E2E-1, steps 1 to 9 and 11 to 13: Security Monitor's real scan and Host Settings' real notifier
against a real ntfy server, on a throw-away machine prepared by test/e2e-suite/setup.sh (CI,
reusable workflow suite.yml; never a server). Step 10 is test/e2e-suite/ui.py, which runs after
this script and sees the state it leaves. As root:

    sudo env MANIFEST=OUT/manifest.json python3 -B test/e2e-suite/core.py OUT/core

The two listeners are real sockets of a real process on 203.0.113.10 (listen.py, a unit of its own),
5432/tcp (A) and 6379/tcp (B), both allowed by a UFW rule, so the scan follows a new connection
from the public address on pub0 to an ACCEPT and rates each a danger from the internet
(databases are dangerous ports). Every scan is the page's "Refresh" verb (security.py scan, the
real unit), every run of the notifier the page's "run" verb (the real unit, as its own user).

  0. Prerequisites: the packages of the version manifest, UFW, pub0, ntfy, the helpers.
  1. Baseline without the test ports: a full scan; the host's target profile (no role); the first
     run of the notifier sends its summary; the notifier's timer is then stopped (test only: a run
     of the timer between a change and its check would take the change), the messages counted.
  2. Listener A: finding A in the status, exactly one new message, A open in last.json.
  3. Listener B while A stays open: finding B, exactly one new message although the item was a
     danger already.
  4. Unchanged again, in English and in German (the notifier's language): no message. Security
     Monitor sorts its findings by state and port, so their order changes only in a replay: its
     last German answer, findings reversed, through the stand-in (security_stub.py): no message,
     A and B in last.json as before.
  5. A closed, a full scan: A resolved by a complete check (one message), B still open.
  6. The packet filter listing fails where the scan collects it: a drop-in in /run puts
     no_packet_filter.sh over iptables-save, ip6tables-save and nft inside the scan's unit only.
     The check is partial, the list not complete, B not resolved, no message; the coverage in
     last.json says Security Monitor's exposure is partial.
  7. Listing restored, B closed, a full scan: B resolved only now (one message).
  8. B open again: within the hour against flapping no message. Then the time of B's last change
     ok>danger in last.json moves back by more than that hour (a test-only change of the
     notifier's state, under its lock; the product's rule stays as it is): "again", one message.
  9. The stand-in answers for Security Monitor with a list that is not a list, then with a total
     the list does not reach (both with the item OK): no message, B stays open, A stays
     resolved; the real answer again: no message.
 11. No access: the stand-in ends with exit 77 (Security Monitor's status verb without access):
     one message "Check no longer possible" for Security Monitor, which the profile expects;
     nothing resolved, A and B as before. The real answer again: one message "Check possible
     again", B still open, nothing told twice.
 12. H1, a check the profile names that the add-on does not report: the profile expects
     security/suite-unreported, which Security Monitor never reports. With a Host Settings that
     has the rule (hslib.not_reported_checks): one message "Check no longer possible", the
     expected check is not reported; last.json has the gap with its check id; B as before; an
     unchanged run: no message. The expectation stays for step 10, where a Status Board with
     the rule shows the gap as a task.
 13. H5, two channels, one failing: e-mail set up next to ntfy, with no mail server on port 25.
     Listener C (3306/tcp, a third port UFW allows) is a new finding: ntfy takes its one message,
     e-mail fails; the status item of e-mail is a warning and the run's outcome names the failing
     channel. An unchanged run: no message to ntfy, e-mail still failed, the outcome still names
     it. Then a mail server (smtp_sink.py) and "Send a test message": e-mail recovers, once, by
     the test; the next run sends nothing to either channel, names no failing channel and
     records no second recovery. E-mail is taken out again, C closed and scanned.

Steps 12 and 13 need a Host Settings with those rules. Without them the step notes so and checks
nothing of the rule, unless the version manifest of the run requires the scenario
(required_scenarios: "h1", "h5"; test/e2e-suite/suite-manifest.json), then it fails.

Writes to OUT: assertions.md and assertions.json; scenarios.json (which of the scenarios h1 and
h5 ran, passed, failed or were not supported); messages.json (every message on the topic);
status/ (the status answers of both add-ons as nobody, at each point); last/ (last.json at each
point); scan/ (what each scan found for the test ports, and its sources); stub/ (what the
stand-in answered); environment.json. No credential is written there. Exit 1 when a check
failed; the test stops after the first step with a failed check (the next ones build on it).
"""
import copy
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

OUT = sys.argv[1] if len(sys.argv) > 1 else "e2e-suite/core"
MANIFEST = os.environ.get("MANIFEST", "")
HS = "/usr/lib/cockpit-host-settings/host-settings.py"
SM = "/usr/lib/cockpit-security-monitor/security.py"
LAST = "/var/lib/cockpit-host-settings/last.json"
HS_LOCK = "/var/lib/cockpit-host-settings/.lock"
SM_STATE = "/var/lib/cockpit-security-monitor/state.json"
LIB = "/usr/local/lib/e2e-suite"
RUN = "/run/e2e-suite"
NTFY = "http://127.0.0.1:8090"
TOPIC = "cockpit-e2e"
NOTIFY_UNIT = "cockpit-host-settings-notify.service"
NOTIFY_TIMER = "cockpit-host-settings-notify.timer"
SCAN_UNIT = "cockpit-security-scan.service"
SCAN_DROPIN = "/run/systemd/system/cockpit-security-scan.service.d/e2e-suite.conf"
NOTIFY_DROPIN = "/run/systemd/system/cockpit-host-settings-notify.service.d/e2e-suite.conf"
STUB_ANSWER = LIB + "/stub-answer.json"
STUB_EXIT = LIB + "/stub-exit"
FLAP = 3600                                  # hsnotify.FLAP
PATH = "/usr/sbin:/usr/bin:/sbin:/bin"       # the scan's PATH (secproc.PATH)
PACKAGES = ("cockpit-security-monitor", "cockpit-host-settings", "cockpit-status-board")
A = (5432, "port.5432-tcp")
B = (6379, "port.6379-tcp")
C = (3306, "port.3306-tcp")
HS_LIB = "/usr/lib/cockpit-host-settings"
UNREPORTED = "suite-unreported"              # a check id Security Monitor never reports (step 12)
MAIL_TO = "suite@example.org"                # step 13: e-mail goes to localhost:25 whatever the address
MAIL_DIR = RUN + "/mail"                     # what smtp_sink.py took
SMTP_UNIT = "e2e-suite-smtp"

checks = []
current = {"n": 0, "title": ""}
counter = {"n": 0}
scenarios = {}
required = set()


class StepFailed(Exception):
    pass


def key(fid):
    return "security#exposure/" + fid


# ---------------------------------------------------------------------------
# The record of the test

def begin(n, title):
    current.update(n=n, title=title)
    print("\n== %d. %s" % (n, title), flush=True)


def note(text):
    checks.append({"step": current["n"], "title": current["title"], "note": text})
    print("      " + text, flush=True)


def check(ok, what, detail=""):
    detail = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)
    checks.append({"step": current["n"], "title": current["title"], "check": what, "ok": bool(ok), "detail": detail[:2000]})
    print(("ok    " if ok else "FAIL  ") + what + ("" if ok else ": " + detail[:800]), flush=True)
    if not ok and os.environ.get("GITHUB_ACTIONS"):
        print("::error title=E2E-1 step %d::%s" % (current["n"], (what + ": " + detail).replace("\n", " ")[:900]), flush=True)
    return bool(ok)


def done():
    failed = any(not c["ok"] for c in checks if c["step"] == current["n"] and "check" in c)
    for name, s in scenarios.items():
        if s.get("step") == current["n"] and s.get("result") == "running":
            s["result"] = "failed" if failed else "passed"
    if failed:
        raise StepFailed(current["n"])


def supported(name):
    """Whether the packages of this run have the rule of scenario name: "h1" (Host Settings tells
    a check the profile names that the add-on does not report, hslib.not_reported_checks) or "h5"
    (Host Settings keeps each channel's health, hschannels.py)."""
    if name == "h1":
        try:
            with open(HS_LIB + "/hslib.py", encoding="utf-8") as f:
                return "def not_reported_checks(" in f.read()
        except OSError:
            return False
    if name == "h5":
        return os.path.exists(HS_LIB + "/hschannels.py")
    return False


def scenario(name, what):
    """True when the step of scenario name runs its checks: the packages have the rule. Without
    it the step notes so, or fails when the version manifest requires the scenario."""
    if supported(name):
        scenarios[name] = {"step": current["n"], "result": "running", "what": what}
        return True
    if name in required:
        scenarios[name] = {"step": current["n"], "result": "failed", "what": what, "why": "required, not supported"}
        check(False, "%s: the Host Settings of this run has the rule (the version manifest requires %s)" % (what, name))
    else:
        scenarios[name] = {"step": current["n"], "result": "not supported", "what": what,
                           "why": "the Host Settings of this run does not have the rule; not required by the manifest"}
        note("%s: not checked, the Host Settings of this run does not have the rule (not required by the manifest)" % what)
    return False


def save(rel, doc):
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")


def name(label):
    counter["n"] += 1
    return "%02d-%02d-%s" % (current["n"], counter["n"], label)


# ---------------------------------------------------------------------------
# The machine

def run(*argv, inp=None, ok=(0,)):
    p = subprocess.run(list(argv), input=inp, capture_output=True, text=True)
    if p.returncode not in ok:
        raise RuntimeError("%s: exit %d\n%s\n%s" % (" ".join(argv), p.returncode, p.stdout[-1500:], p.stderr[-1500:]))
    return p.stdout


def listening(port):
    return bool(run("ss", "-Hltn", "sport", "=", ":%d" % port).strip())


def listener(port, on):
    """Starts or stops the listener on port (a transient unit of its own); True once the socket
    is there, or gone."""
    unit = "e2e-suite-listen-%d" % port
    if on:
        run("systemd-run", "--quiet", "--collect", "--unit", unit, "/usr/bin/python3", "-I", LIB + "/listen.py", str(port))
    else:
        run("systemctl", "stop", unit, ok=(0, 5))
    for _ in range(50):
        if listening(port) == on:
            return True
        time.sleep(0.2)
    return False


def dropin(path, body):
    if body is None:
        if os.path.exists(path):
            os.unlink(path)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
    run("systemctl", "daemon-reload")


def scan(label):
    """A full scan through the page's verb (the real unit); what it found for the test ports and
    how each source went, saved (state.json itself keeps addresses and rules: not saved)."""
    run(SM, "scan")
    with open(SM_STATE, encoding="utf-8") as f:
        st = json.load(f)
    ports = {}
    for p in st.get("ports") or []:
        if p.get("proto") == "tcp" and p.get("port") in (A[0], B[0], C[0]):
            inet = ((p.get("reach") or {}).get("4") or {}).get("internet") or {}
            ports[p["port"]] = {"state": p.get("state"), "why": p.get("why"), "internet": inet.get("result"),
                                "reason": inet.get("reason"), "path": inet.get("path")}
    sources = {k: {kk: vv for kk, vv in v.items() if kk in ("state", "partial", "missing", "reason")}
               for k, v in (st.get("sources") or {}).items()}
    out = {"generated": st.get("generated"), "sources": sources, "test_ports": ports,
           "public": (st.get("network") or {}).get("public")}
    save("scan/%s.json" % name(label), out)
    return out


def status(cmd, addon, lang, label):
    doc = json.loads(run("runuser", "-u", "nobody", "--", cmd, "status", "--lang", lang))
    save("status/%s-%s-%s.json" % (name(label), addon, lang), doc)
    return doc


def answers(label):
    """Both add-ons' status as nobody (Security Monitor in both languages), saved; Security
    Monitor's English answer."""
    sm = status(SM, "security", "en", label)
    status(SM, "security", "de", label)
    status(HS, "host-settings", "en", label)
    return sm


def exposure(doc):
    """(item, check, findings object, {fid: finding}) of Security Monitor's exposure."""
    item = next((i for i in doc.get("items") or [] if i.get("id") == "exposure"), {})
    chk = next((c for c in doc.get("checks") or [] if c.get("id") == "exposure"), {})
    found = item.get("findings") if isinstance(item.get("findings"), dict) else {}
    listed = found.get("list") if isinstance(found.get("list"), list) else []
    return item, chk, found, {f.get("fid"): f for f in listed if isinstance(f, dict)}


def notify():
    run("systemctl", "reset-failed", NOTIFY_UNIT, ok=(0, 1, 5))
    run(HS, "run")


def last(label=None):
    with open(LAST, encoding="utf-8") as f:
        doc = json.load(f)
    if label:
        save("last/%s.json" % name(label), doc)
    return doc


def entry(doc, k):
    e = (doc.get("sent") or {}).get(k)
    return e if isinstance(e, dict) else None


def brief(e):
    return None if not e else {k: e.get(k) for k in ("level", "status", "last")}


def coverage(doc, pkg):
    return next((c for c in doc.get("coverage") or [] if isinstance(c, dict) and c.get("id") == pkg), None)


def seen_summary(doc):
    return (((doc.get("seen") or {}).get("security") or {}).get("summary")) or ""


def age_change(k, change, seconds):
    """The test-only control of step 8: the time of one change of one key in last.json moves back
    by seconds, under the notifier's own lock (a run waits, or exits 75), owner and mode kept."""
    fd = os.open(HS_LOCK, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        st = os.stat(LAST)
        with open(LAST, encoding="utf-8") as f:
            doc = json.load(f)
        before = doc["sent"][k]["last"][change]
        doc["sent"][k]["last"][change] = before - seconds
        tmp = LAST + ".e2e-suite"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, 0o600)
        os.replace(tmp, LAST)
        return before, before - seconds
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def stub(answer, why):
    """Security Monitor's status verb answers `answer` for the notifier's unit only."""
    with open(STUB_ANSWER, "w", encoding="utf-8") as f:
        json.dump(answer, f, ensure_ascii=False)
    os.chmod(STUB_ANSWER, 0o644)
    save("stub/%s.json" % name(why), answer)
    dropin(NOTIFY_DROPIN, "[Service]\n# E2E-1 (test only): a stand-in for Security Monitor's status verb, one run\n"
                          "BindReadOnlyPaths=%s/security_stub.py:%s\n" % (LIB, SM))


def stub_exit(code, why):
    """Security Monitor's status verb ends with code and prints nothing, for the notifier's unit
    only."""
    with open(STUB_EXIT, "w", encoding="utf-8") as f:
        f.write("%d\n" % code)
    os.chmod(STUB_EXIT, 0o644)
    save("stub/%s.json" % name(why), {"exit": code})
    dropin(NOTIFY_DROPIN, "[Service]\n# E2E-1 (test only): a stand-in for Security Monitor's status verb, one run\n"
                          "BindReadOnlyPaths=%s/security_stub.py:%s\n" % (LIB, SM))


def unstub():
    dropin(NOTIFY_DROPIN, None)
    for path in (STUB_ANSWER, STUB_EXIT):
        if os.path.exists(path):
            os.unlink(path)


def journal_new():
    """The lines the notifier's unit wrote to the journal since the last call (a cursor file in
    /run; the first call returns everything so far)."""
    return run("journalctl", "--no-pager", "-o", "cat", "-u", NOTIFY_UNIT, "--cursor-file", RUN + "/journal.cursor",
               ok=(0, 1)).splitlines()


def run_line():
    """The line of the run that just ended, as the notifier writes it to the journal: "N add-ons[,
    not readable: ...][, message: CHANNEL RESULT, ...][, failing: CHANNEL WHY, ...]"."""
    seen = []
    for _ in range(25):
        seen += journal_new()
        # Not systemd's "Finished ..." line, whose unit description names the add-ons as well.
        found = [line for line in seen if re.match(r"[0-9]+ add-ons\b", line)]
        if found:
            return found[-1]
        time.sleep(0.2)
    return ""


def channels(doc):
    c = doc.get("channels")
    return c if isinstance(c, dict) else {}


def item_state(doc, iid):
    return next((i.get("state") for i in doc.get("items") or [] if i.get("id") == iid), None)


def mails():
    return sorted(os.listdir(MAIL_DIR)) if os.path.isdir(MAIL_DIR) else []


def packet_filter_tools():
    """The programs behind iptables-save, ip6tables-save and nft, as the scan's PATH finds them."""
    found = {}
    for t in ("iptables-save", "ip6tables-save", "nft"):
        p = shutil.which(t, path=PATH)
        if p:
            found[t] = os.path.realpath(p)
    return found


# ---------------------------------------------------------------------------
# The messages

def token():
    with open(RUN + "/reader.token", encoding="utf-8") as f:
        return f.read().strip()


def messages():
    req = urllib.request.Request("%s/%s/json?poll=1" % (NTFY, TOPIC), headers={"Authorization": "Bearer " + token()})
    with urllib.request.urlopen(req, timeout=20) as r:
        lines = r.read().decode("utf-8").splitlines()
    out = []
    for line in lines:
        if line.strip():
            m = json.loads(line)
            if m.get("event") == "message":
                out.append({k: m.get(k) for k in ("id", "time", "title", "message", "priority", "tags")})
    return out


def text(m):
    return (m.get("title") or "") + "\n" + (m.get("message") or "")


class Topic:
    """The messages on the topic, and which are new since the last look."""

    def __init__(self):
        self.seen = {m["id"] for m in messages()}

    def new(self, label):
        ms = messages()
        fresh = [m for m in ms if m["id"] not in self.seen]
        self.seen |= {m["id"] for m in fresh}
        if fresh:
            save("messages/%s.json" % name(label), fresh)
        note("%d new message(s)%s" % (len(fresh), "".join("; " + text(m).replace("\n", " / ")[:300] for m in fresh)))
        return fresh


topic = None


# ---------------------------------------------------------------------------
# The steps

def step0():
    begin(0, "Prerequisites")
    for path in (HS, SM, LIB + "/listen.py", LIB + "/security_stub.py", LIB + "/no_packet_filter.sh", RUN + "/reader.token"):
        check(os.path.exists(path), "present: " + path)
    check(not os.path.exists(LAST), "the notifier has not run yet (no last.json)")
    want = {}
    if check(bool(MANIFEST) and os.path.exists(MANIFEST), "the version manifest of this run", MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            doc = json.load(f)
        want = {c.get("package"): c.get("version") for c in doc.get("components", [])}
        required.update(doc.get("required_scenarios") or [])
        note("scenarios the manifest requires: %s; h1 supported: %s, h5 supported: %s"
             % (sorted(required) or "none", supported("h1"), supported("h5")))
    got = {p: run("dpkg-query", "-W", "-f", "${Version}", p, ok=(0, 1)).strip() for p in PACKAGES}
    check(all(want.get(p) and got[p] == want[p] for p in PACKAGES), "installed: the packages of the manifest",
          {"installed": got, "manifest": want})
    check(not listening(A[0]) and not listening(B[0]), "nothing listens on 5432/tcp or 6379/tcp yet")
    ufw = run("ufw", "status")
    check("Status: active" in ufw and "5432/tcp" in ufw and "6379/tcp" in ufw, "UFW on, 5432/tcp and 6379/tcp allowed", ufw)
    addr = run("ip", "-br", "addr", "show", "pub0")
    check("203.0.113.10/24" in addr, "the public address 203.0.113.10/24 on pub0", addr)
    check(run("systemctl", "is-active", NOTIFY_TIMER, ok=(0, 3)).strip() != "active", "the notifier's timer is off")
    tools = packet_filter_tools()
    check("iptables-save" in tools and "nft" in tools, "iptables-save and nft are there (step 6 replaces them)", tools)
    try:
        with urllib.request.urlopen(NTFY + "/v1/health", timeout=10) as r:
            health = json.loads(r.read().decode("utf-8"))
    except OSError as e:
        health = {"error": str(e)}
    check(health.get("healthy") is True, "ntfy answers", health)
    env = {"system": run("sh", "-c", ". /etc/os-release && echo \"$PRETTY_NAME\"").strip(), "kernel": run("uname", "-r").strip(),
           "packages": got,
           "cockpit": run("dpkg-query", "-W", "-f", "${Version}", "cockpit-ws", ok=(0, 1)).strip(),
           "python3": run("python3", "--version").strip(), "iptables": run("iptables", "--version").strip(),
           "nft": run("nft", "--version", ok=(0, 1)).strip(), "ufw": run("ufw", "version").splitlines()[0],
           "docker": run("docker", "version", "--format", "{{.Server.Version}}", ok=(0, 1)).strip(),
           "ntfy": run("docker", "inspect", "-f", "{{.Config.Image}}", "ntfy", ok=(0, 1)).strip(),
           "packet_filter_tools": tools}
    save("environment.json", env)
    note(json.dumps(env, ensure_ascii=False))
    done()


def step1():
    global topic
    begin(1, "Baseline without the test ports")
    sc = scan("baseline")
    check(sc["sources"].get("rules", {}).get("state") == "ok", "the scan read the packet filter", sc["sources"].get("rules"))
    check((sc.get("public") or {}).get("4") is True, "the scan sees a public IPv4 address (pub0)", sc.get("public"))
    check(not sc["test_ports"], "neither test port in the scan", sc["test_ports"])
    sm = answers("baseline")
    item, chk, found, fids = exposure(sm)
    check(chk.get("state") == "checked", "check exposure: checked", chk)
    check(found.get("complete") is True, "its list of findings is complete", {k: v for k, v in found.items() if k != "list"})
    check(A[1] not in fids and B[1] not in fids, "no finding A or B yet", sorted(fids))
    note("the host's target profile: no role (the add-ons of every host expected)")
    run(HS, "profile", "roles")
    note("the first run of the notifier (install timer: the page's verb)")
    run(HS, "install", "timer")
    run("systemctl", "stop", NOTIFY_TIMER)
    note("test only: the notifier's timer stopped after its first run; every later run is the page's run verb, "
         "started by the test, so that no run of the timer falls between a change and its check")
    ms = messages()
    summary = [m for m in ms if "messages set up" in (m.get("title") or "")]
    check(len(summary) == 1, "the first run sent its summary", [m.get("title") for m in ms])
    if summary:
        save("messages/%s.json" % name("summary"), summary)
    doc = last("baseline")
    check(bool(doc.get("baseline_at")), "last.json: baseline_at", doc.get("baseline_at"))
    check(entry(doc, key(A[1])) is None and entry(doc, key(B[1])) is None, "last.json: nothing about A or B")
    cov = coverage(doc, "security")
    check(cov is not None and not any(g.get("id") == "exposure" for g in cov.get("gaps") or []),
          "last.json: the coverage of Security Monitor's exposure has no gap", cov)
    topic = Topic()
    note("messages on the topic: %d" % len(topic.seen))
    done()


def step2():
    begin(2, "Listener A: finding A, exactly one message")
    check(listener(A[0], True), "listener A on 203.0.113.10:5432")
    sc = scan("A-open")
    pa = sc["test_ports"].get(A[0]) or {}
    check(pa.get("internet") == "open" and pa.get("state") == "danger" and pa.get("why") == "public-danger",
          "the scan: 5432/tcp open from the internet, a danger", pa)
    sm = answers("A-open")
    item, chk, found, fids = exposure(sm)
    fa = fids.get(A[1]) or {}
    check(fa.get("state") == "danger" and fa.get("exposure") == "internet" and fa.get("link") == "#/exposure/5432-tcp",
          "the status: finding A, a danger from the internet, with its link", fa)
    check(item.get("state") == "danger" and chk.get("state") == "checked" and found.get("complete") is True,
          "the status: item exposure a danger, check checked, list complete",
          {"item": item.get("state"), "check": chk.get("state"), "complete": found.get("complete")})
    notify()
    new = topic.new("A")
    check(len(new) == 1, "exactly one new message")
    t = text(new[0]) if new else ""
    check("5432/tcp" in t and "6379/tcp" not in t and "Danger" in t, "it names A as a danger", t)
    doc = last("A-open")
    ea = entry(doc, key(A[1]))
    check(ea is not None and ea.get("status") == "open" and ea.get("level") == "danger", "last.json: A open, a danger", brief(ea))
    done()


def step3():
    begin(3, "Listener B while A stays open: finding B, exactly one message")
    before = exposure(status(SM, "security", "en", "before-B"))[0].get("state")
    check(before == "danger", "the item exposure is a danger already", before)
    check(listener(B[0], True), "listener B on 203.0.113.10:6379")
    sc = scan("B-open")
    pb = sc["test_ports"].get(B[0]) or {}
    check(pb.get("internet") == "open" and pb.get("state") == "danger", "the scan: 6379/tcp open from the internet, a danger", pb)
    sm = answers("B-open")
    item, chk, found, fids = exposure(sm)
    check((fids.get(B[1]) or {}).get("state") == "danger" and (fids.get(A[1]) or {}).get("state") == "danger",
          "the status: findings A and B, both dangers", sorted(fids))
    check(item.get("state") == "danger", "the item stays a danger", item.get("state"))
    notify()
    new = topic.new("B")
    check(len(new) == 1, "exactly one new message")
    t = text(new[0]) if new else ""
    check("6379/tcp" in t and "5432/tcp" not in t, "it names B, not A", t)
    doc = last("B-open")
    eb, ea = entry(doc, key(B[1])), entry(doc, key(A[1]))
    check(eb is not None and eb.get("status") == "open" and eb.get("level") == "danger", "last.json: B open, a danger", brief(eb))
    check(ea is not None and ea.get("status") == "open", "last.json: A still open", brief(ea))
    done()


def step4():
    begin(4, "Unchanged again, in another language and order: no message")
    keep = {k: brief(entry(last(), k)) for k in (key(A[1]), key(B[1]))}
    scan("repeat-en")
    answers("repeat-en")
    notify()
    check(not topic.new("repeat-en"), "unchanged, in English: no message")
    run(HS, "set", "language", "de")
    note("the notifier's language: German (it asks the add-ons with --lang de)")
    scan("repeat-de")
    real_de = status(SM, "security", "de", "repeat-de")
    notify()
    check(not topic.new("repeat-de"), "unchanged, in German: no message")
    doc = last("repeat-de")
    titles = [i.get("title") for i in ((doc.get("seen") or {}).get("security") or {}).get("items", [])]
    check("Aus dem Internet erreichbar" in titles, "the notifier read Security Monitor's German answer", titles)
    order = [f.get("fid") for f in exposure(real_de)[2].get("list") or []]
    note("Security Monitor's own order (state, then port): %s" % order)
    replay = copy.deepcopy(real_de)
    item = exposure(replay)[0]
    item["findings"]["list"].reverse()
    replay["summary"] = "E2E replay, findings reversed: " + (real_de.get("summary") or "")[:120]
    shown = [f.get("fid") for f in item["findings"]["list"]]
    check(len(order) >= 2 and shown != order and sorted(shown) == sorted(order), "the replay lists the same findings in another order", shown)
    stub(replay, "reverse-order")
    try:
        notify()
    finally:
        unstub()
    check(not topic.new("reverse-order"), "the same answer with its findings reversed: no message")
    doc = last("reverse-order")
    check(seen_summary(doc).startswith("E2E replay"), "the notifier read the replay", seen_summary(doc))
    now = {k: brief(entry(doc, k)) for k in keep}
    check(now == keep, "last.json: A and B as before", {"before": keep, "now": now})
    run(HS, "set", "language", "en")
    note("the notifier's language: English again")
    done()


def step5():
    begin(5, "A closed, a full scan: A resolved, B still open")
    check(listener(A[0], False), "listener A stopped, 5432/tcp closed")
    sc = scan("A-closed")
    check(sc["sources"].get("rules", {}).get("state") == "ok" and A[0] not in sc["test_ports"],
          "the scan read the packet filter and no longer finds 5432/tcp", sc["test_ports"])
    sm = answers("A-closed")
    item, chk, found, fids = exposure(sm)
    check(A[1] not in fids and (fids.get(B[1]) or {}).get("state") == "danger", "the status: A gone, B a danger", sorted(fids))
    check(chk.get("state") == "checked" and found.get("complete") is True, "check checked, list complete",
          {"check": chk.get("state"), "complete": found.get("complete"), "total": found.get("total")})
    notify()
    new = topic.new("A-resolved")
    check(len(new) == 1, "exactly one new message")
    t = text(new[0]) if new else ""
    check("OK again" in t and "5432/tcp" in t and "no longer found by a complete check" in t and "6379/tcp" not in t,
          "it says A is OK again, no longer found by a complete check", t)
    doc = last("A-closed")
    ea, eb = entry(doc, key(A[1])), entry(doc, key(B[1]))
    check(ea is not None and ea.get("status") == "resolved", "last.json: A resolved", brief(ea))
    check(eb is not None and eb.get("status") == "open" and eb.get("level") == "danger", "last.json: B still open", brief(eb))
    done()


def step6():
    begin(6, "The packet filter listing fails at the collection step: partial, B not resolved")
    tools = packet_filter_tools()
    body = "[Service]\n# E2E-1 step 6 (test only): the packet filter listing fails for this one scan\n" + \
        "".join("BindReadOnlyPaths=%s/no_packet_filter.sh:%s\n" % (LIB, p) for p in sorted(set(tools.values())))
    save("stub/%s.json" % name("scan-dropin"), {"path": SCAN_DROPIN, "body": body, "tools": tools})
    check(listening(B[0]), "listener B still open: the port is really there")
    dropin(SCAN_DROPIN, body)
    try:
        prop = run("systemctl", "show", SCAN_UNIT, "-p", "BindReadOnlyPaths", "--value")
        check(all(p in prop for p in tools.values()), "the scan's unit has the stand-ins over %s" % ", ".join(sorted(tools)), prop)
        sc = scan("no-packet-filter")
    finally:
        dropin(SCAN_DROPIN, None)
    check(sc["sources"].get("rules", {}).get("state") == "error", "the scan's source rules: error", sc["sources"].get("rules"))
    check((sc["test_ports"].get(B[0]) or {}).get("internet") == "unknown", "6379/tcp: not known from the internet",
          sc["test_ports"].get(B[0]))
    host = subprocess.run(["iptables-save"], capture_output=True, text=True)
    check(host.returncode == 0 and "*filter" in host.stdout, "outside the scan's unit iptables-save works as before")
    sm = answers("no-packet-filter")
    item, chk, found, fids = exposure(sm)
    check(chk.get("state") == "partial", "check exposure: partial", chk)
    check(found.get("complete") is not True and B[1] not in fids, "the list is not complete and does not name B",
          {"complete": found.get("complete"), "fids": sorted(fids)})
    check(item.get("state") == "none", "the item exposure: not assessable (none)", item.get("state"))
    notify()
    check(not topic.new("no-packet-filter"), "no message")
    doc = last("no-packet-filter")
    eb = entry(doc, key(B[1]))
    check(eb is not None and eb.get("status") == "open" and eb.get("level") == "danger", "last.json: B still open, a danger", brief(eb))
    cov = coverage(doc, "security") or {}
    gap = next((g for g in cov.get("gaps") or [] if g.get("id") == "exposure"), {})
    check(cov.get("coverage") not in (None, "checked") and gap.get("coverage") == "partial",
          "last.json: the coverage of Security Monitor is not checked, its exposure partial", cov)
    done()


def step7():
    begin(7, "Listing restored, B closed, a full scan: B resolved only now")
    check(not os.path.exists(SCAN_DROPIN), "the drop-in of step 6 is gone")
    check(listener(B[0], False), "listener B stopped, 6379/tcp closed")
    sc = scan("B-closed")
    check(sc["sources"].get("rules", {}).get("state") == "ok", "the scan reads the packet filter again", sc["sources"].get("rules"))
    check(not sc["test_ports"], "neither test port in the scan", sc["test_ports"])
    sm = answers("B-closed")
    item, chk, found, fids = exposure(sm)
    check(chk.get("state") == "checked" and found.get("complete") is True and B[1] not in fids and A[1] not in fids,
          "check checked, list complete, neither A nor B", {"check": chk.get("state"), "fids": sorted(fids)})
    notify()
    new = topic.new("B-resolved")
    check(len(new) == 1, "exactly one new message")
    t = text(new[0]) if new else ""
    check("OK again" in t and "6379/tcp" in t and "no longer found by a complete check" in t and "5432/tcp" not in t,
          "it says B is OK again, no longer found by a complete check", t)
    doc = last("B-closed")
    eb = entry(doc, key(B[1]))
    check(eb is not None and eb.get("status") == "resolved", "last.json: B resolved", brief(eb))
    cov = coverage(doc, "security") or {}
    check(not any(g.get("id") == "exposure" for g in cov.get("gaps") or []), "last.json: Security Monitor's exposure covered again", cov)
    done()


def step8():
    begin(8, "B open again: the hour against flapping, then again")
    check(listener(B[0], True), "listener B on 203.0.113.10:6379 again")
    scan("B-reopened")
    sm = answers("B-reopened")
    check((exposure(sm)[3].get(B[1]) or {}).get("state") == "danger", "the status: finding B, a danger")
    notify()
    check(not topic.new("B-held"), "within the hour after B turned a danger last: no message")
    doc = last("B-held")
    eb = entry(doc, key(B[1])) or {}
    told = (eb.get("last") or {}).get("ok>danger")
    check(eb.get("status") == "resolved" and isinstance(told, (int, float)) and time.time() - told < FLAP,
          "last.json: B waits as resolved, its change ok>danger told less than an hour ago", brief(eb))
    if isinstance(told, (int, float)):
        before, after = age_change(key(B[1]), "ok>danger", FLAP + 60)
        note("test only: the time of B's change ok>danger in last.json moved from %d to %d (%d s back), under the "
             "notifier's lock" % (before, after, FLAP + 60))
    notify()
    new = topic.new("B-again")
    check(len(new) == 1, "exactly one new message")
    t = text(new[0]) if new else ""
    check("Danger, again" in t and "6379/tcp" in t and "5432/tcp" not in t, "it says B is a danger again", t)
    doc = last("B-again")
    eb = entry(doc, key(B[1])) or {}
    check(eb.get("status") == "open" and eb.get("level") == "danger" and time.time() - (eb.get("last") or {}).get("ok>danger", 0) < 600,
          "last.json: B open, a danger, its change told now", brief(eb))
    done()


def step9():
    begin(9, "Broken answers through the stand-in: B stays open")
    real = status(SM, "security", "en", "real")
    check(B[1] in exposure(real)[3], "the real answer lists B")
    keep_a = brief(entry(last(), key(A[1])))

    bad = copy.deepcopy(real)
    item, chk = exposure(bad)[:2]
    item["state"] = "ok"
    item["findings"]["list"] = B[1]
    chk["state"] = "partial"
    chk["reason"] = "E2E stand-in"
    bad["summary"] = "E2E stand-in: a list that is a text"
    stub(bad, "list-not-a-list")
    try:
        notify()
    finally:
        unstub()
    check(not topic.new("list-not-a-list"), "item OK, check partial, findings.list a text: no message")
    doc = last("list-not-a-list")
    check(seen_summary(doc).startswith("E2E stand-in"), "the notifier read the stand-in", seen_summary(doc))
    eb = entry(doc, key(B[1])) or {}
    check(eb.get("status") == "open" and eb.get("level") == "danger", "last.json: B still open, a danger", brief(eb))

    bad = copy.deepcopy(real)
    item, chk = exposure(bad)[:2]
    item["state"] = "ok"
    item["findings"] = {"complete": True, "total": 1, "list": []}
    chk["state"] = "checked"
    chk.pop("reason", None)
    bad["summary"] = "E2E stand-in: complete, total 1, empty list"
    stub(bad, "total-not-reached")
    try:
        notify()
    finally:
        unstub()
    check(not topic.new("total-not-reached"), "item OK, check checked, complete with total 1 and an empty list: no message")
    doc = last("total-not-reached")
    check(seen_summary(doc).startswith("E2E stand-in"), "the notifier read the stand-in", seen_summary(doc))
    eb = entry(doc, key(B[1])) or {}
    check(eb.get("status") == "open" and eb.get("level") == "danger", "last.json: B still open, a danger", brief(eb))

    notify()
    check(not topic.new("real-again"), "the real answer again: no message")
    doc = last("real-again")
    eb = entry(doc, key(B[1])) or {}
    check(eb.get("status") == "open" and eb.get("level") == "danger", "last.json: B open, a danger", brief(eb))
    check(brief(entry(doc, key(A[1]))) == keep_a, "last.json: A as before (resolved)", brief(entry(doc, key(A[1]))))
    done()


def step11():
    begin(11, "No access (exit 77): one message, nothing resolved; back: one message")
    keep = {k: brief(entry(last(), k)) for k in (key(A[1]), key(B[1]))}
    stub_exit(77, "no-access")
    try:
        notify()
    finally:
        unstub()
    new = topic.new("no-access")
    check(len(new) == 1, "exactly one new message")
    t = text(new[0]) if new else ""
    check("Check no longer possible" in t and "Security Monitor" in t and "nothing is readable without root" in t,
          "it says Security Monitor can no longer be checked: nothing is readable without root", t)
    doc = last("no-access")
    e = entry(doc, "security@") or {}
    check(e.get("level") == "lost" and e.get("coverage") == "noaccess", "last.json: Security Monitor's coverage lost, noaccess",
          {k: e.get(k) for k in ("level", "coverage")})
    now = {k: brief(entry(doc, k)) for k in keep}
    check(now == keep, "last.json: A and B as before (nothing resolved without access)", {"before": keep, "now": now})
    notify()
    new = topic.new("access-back")
    check(len(new) == 1, "the real answer again: exactly one new message")
    t = text(new[0]) if new else ""
    check("Check possible again" in t and "Security Monitor" in t and "6379/tcp" not in t,
          "it says Security Monitor can be checked again, and nothing about B", t)
    doc = last("access-back")
    e = entry(doc, "security@") or {}
    check(e.get("level") == "ok", "last.json: Security Monitor's coverage ok again", {k: e.get(k) for k in ("level", "coverage")})
    now = {k: brief(entry(doc, k)) for k in keep}
    check(now == keep, "last.json: A and B still as before", {"before": keep, "now": now})
    notify()
    check(not topic.new("access-unchanged"), "unchanged: no message")
    done()


def step12():
    begin(12, "H1: the profile expects a check Security Monitor does not report")
    item = "security/" + UNREPORTED
    reported = [c.get("id") for c in status(SM, "security", "en", "h1-before").get("checks") or []]
    check(UNREPORTED not in reported, "Security Monitor does not report a check %s" % UNREPORTED, reported)
    keep_b = brief(entry(last(), key(B[1])))
    run(HS, "profile", "expect", "add", item)
    expect = json.loads(run(HS, "profile", "show")).get("expect") or []
    check(item in expect, "the profile expects %s" % item, expect)
    note("the expectation stays for step 10 (the board)")
    rule = scenario("h1", "H1, a check the profile names that the add-on does not report")
    notify()
    new = topic.new("h1-expected")
    if not rule:
        note("Host Settings without the rule: %d new message(s)" % len(new))
        done()
        return
    check(len(new) == 1, "exactly one new message")
    t = text(new[0]) if new else ""
    check("Check no longer possible" in t and UNREPORTED in t and "the expected check is not reported" in t,
          "it names the check: no longer possible, the expected check is not reported", t)
    doc = last("h1-expected")
    e = entry(doc, "security@" + UNREPORTED) or {}
    check(e.get("level") == "lost" and e.get("coverage") == "unsupported",
          "last.json: the check's own entry security@%s, lost, unsupported" % UNREPORTED,
          {k: e.get(k) for k in ("level", "coverage", "title")})
    row = coverage(doc, item) or {}
    check(row.get("coverage") == "unsupported" and row.get("not_reported") is True,
          "last.json: the coverage of %s is a gap, not reported" % item, row)
    check(brief(entry(doc, key(B[1]))) == keep_b, "last.json: finding B as before", brief(entry(doc, key(B[1]))))
    notify()
    check(not topic.new("h1-unchanged"), "unchanged: no message")
    e2 = entry(last("h1-unchanged"), "security@" + UNREPORTED) or {}
    check(e2.get("level") == "lost" and e2.get("last") == e.get("last"), "last.json: still lost, told once",
          {k: e2.get(k) for k in ("level", "last")})
    done()


def step13():
    begin(13, "H5: two channels, e-mail fails; the failure stays until e-mail accepts again")
    if not scenario("h5", "H5, the health of each channel"):
        done()
        return
    check(not listening(25), "nothing listens on port 25 (no mail server yet)")
    run(HS, "set", "mail", MAIL_TO)
    note("e-mail set up next to ntfy (%s, by SMTP to localhost:25)" % MAIL_TO)
    journal_new()
    check(listener(C[0], True), "listener C on 203.0.113.10:3306")
    sc = scan("C-open")
    pc = sc["test_ports"].get(C[0]) or {}
    check(pc.get("internet") == "open" and pc.get("state") == "danger", "the scan: 3306/tcp open from the internet, a danger", pc)
    check((exposure(answers("C-open"))[3].get(C[1]) or {}).get("state") == "danger", "the status: finding C, a danger")
    notify()
    line = run_line()
    note("the run: " + line)
    new = topic.new("C")
    check(len(new) == 1 and "3306/tcp" in text(new[0]), "ntfy took exactly one new message, about C",
          [text(m) for m in new])
    check(not mails(), "no mail server took anything")
    doc = last("C-mail-failed")
    ch = channels(doc)
    failed = (ch.get("mail") or {}).get("failed") or {}
    check(bool(failed.get("since")) and failed.get("target") == MAIL_TO, "last.json: e-mail failed, for %s" % MAIL_TO, ch.get("mail"))
    check(bool((ch.get("ntfy") or {}).get("ok")) and not (ch.get("ntfy") or {}).get("failed"), "last.json: ntfy ok", ch.get("ntfy"))
    check("failing: mail" in line, "the run's outcome (what a watchdog gets) names the failing e-mail", line)
    hs = status(HS, "host-settings", "en", "C-mail-failed")
    check(item_state(hs, "mail") == "warning" and item_state(hs, "ntfy") == "ok",
          "Host Settings' status: e-mail a warning, ntfy OK", [(i.get("id"), i.get("state")) for i in hs.get("items") or []])

    notify()
    line = run_line()
    note("the run: " + line)
    check(not topic.new("unchanged-1"), "unchanged: no message to ntfy")
    doc = last("unchanged-1")
    f2 = (channels(doc).get("mail") or {}).get("failed") or {}
    check(f2.get("since") == failed.get("since") and not (channels(doc).get("mail") or {}).get("recovered"),
          "last.json: e-mail still failed since the same time, no recovery", channels(doc).get("mail"))
    check("failing: mail" in line, "the run with nothing to send still names the failing e-mail", line)
    check(item_state(status(HS, "host-settings", "en", "unchanged-1"), "mail") == "warning", "Host Settings' status: e-mail still a warning")

    run("systemd-run", "--quiet", "--collect", "--unit", SMTP_UNIT, "/usr/bin/python3", "-I", LIB + "/smtp_sink.py", MAIL_DIR)
    check(poll_true(lambda: listening(25), 10), "a mail server on 127.0.0.1:25 (smtp_sink.py)")
    out = run(HS, "test", "mail", ok=(0, 1))
    res = next((json.loads(x[7:]) for x in out.splitlines() if x.startswith("RESULT ")), {})
    save("channels/%s.json" % name("test-mail"), res)
    check((res.get("result") or {}).get("ok") is True, "Send a test message: e-mail accepted it", res.get("result"))
    check(len(mails()) == 1, "the mail server took the test message", mails())
    doc = last("test-mail")
    m = channels(doc).get("mail") or {}
    rec = m.get("recovered") or {}
    check(not m.get("failed") and rec.get("via") == "test" and rec.get("since") == failed.get("since"),
          "last.json: e-mail recovered by the test, from the failure of before", m)
    check(item_state(status(HS, "host-settings", "en", "test-mail"), "mail") == "ok", "Host Settings' status: e-mail OK")

    notify()
    line = run_line()
    note("the run: " + line)
    check(not topic.new("unchanged-2"), "the next run: no message to ntfy")
    check(len(mails()) == 1, "and none to e-mail (C is not sent again to the channel that failed)", mails())
    m2 = channels(last("unchanged-2")).get("mail") or {}
    check(not m2.get("failed") and m2.get("recovered") == rec, "last.json: e-mail OK, still the one recovery", m2)
    check("failing:" not in line, "the run names no failing channel", line)

    run(HS, "unset", "mail")
    run("systemctl", "stop", SMTP_UNIT, ok=(0, 5))
    check(listener(C[0], False), "listener C stopped, 3306/tcp closed")
    sc = scan("C-closed")
    check(C[0] not in sc["test_ports"], "the scan no longer finds 3306/tcp", sc["test_ports"])
    note("e-mail taken out, the mail server stopped, C closed (the notifier does not run again before step 10)")
    done()


def poll_true(fn, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.2)
    return False


def report(complete):
    try:
        save("messages.json", messages())
    except (OSError, ValueError) as e:
        print("messages not read:", e)
    save("assertions.json", checks)
    for s in scenarios.values():
        if s.get("result") == "running":
            s["result"] = "not finished"
    save("scenarios.json", scenarios)

    def cell(s):
        return str(s).replace("|", "\\|").replace("\n", " ")

    failed = [c for c in checks if "check" in c and not c["ok"]]
    md = ["## E2E-1, steps 1 to 9 and 11 to 13 (core path)", "",
          "%d checks, %d failed%s." % (sum(1 for c in checks if "check" in c), len(failed),
                                       "" if complete else ", stopped before step 13 ended"), "",
          "Scenarios: " + (", ".join("%s %s" % (k, v["result"]) for k, v in sorted(scenarios.items())) or "none") + ".", "",
          "| Step | Check | Result |", "|---|---|---|"]
    for c in checks:
        if "check" in c:
            md.append("| %d | %s | %s |" % (c["step"], cell(c["check"]),
                                            "ok" if c["ok"] else "FAILED: " + cell(c["detail"][:400])))
        else:
            md.append("| %d | %s | note |" % (c["step"], cell(c["note"][:600])))
    with open(os.path.join(OUT, "assertions.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")


def main():
    os.makedirs(OUT, exist_ok=True)
    complete = False
    try:
        for fn in (step0, step1, step2, step3, step4, step5, step6, step7, step8, step9, step11, step12, step13):
            fn()
        complete = True
    except StepFailed:
        pass
    except Exception as e:  # noqa: BLE001 - a step that could not run to its end fails
        check(False, "the step ran to its end", "%s: %s" % (type(e).__name__, e))
    finally:
        for path in (SCAN_DROPIN, NOTIFY_DROPIN):
            if os.path.exists(path):
                dropin(path, None)
        report(complete)
    failed = any("check" in c and not c["ok"] for c in checks)
    print("\nE2E-1 core path: %s" % ("ALL STEPS PASSED" if complete and not failed else "FAILED"))
    return 0 if complete and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
