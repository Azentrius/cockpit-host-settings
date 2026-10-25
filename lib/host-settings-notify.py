#!/usr/bin/python3 -IB
# SPDX-License-Identifier: Apache-2.0
"""Host Settings, the Cockpit add-on: the notifier.

  host-settings-notify.py run          one run (cockpit-host-settings-notify.service, every 5 minutes)
  host-settings-notify.py test ntfy    "Check connection" of the page (cockpit-host-settings-test@ntfy)
  host-settings-notify.py test mail    "Send a test message" of the page (cockpit-host-settings-test@mail)

Runs as the user cockpit-host-settings, never as root: it reads the status
verbs of the Azentrius add-ons on this host (they work without root, as the
status contract asks), tells what changed through ntfy and/or the local MTA,
and writes its state to /var/lib/cockpit-host-settings. The ntfy token comes
from systemd as the credential ntfy.token (LoadCredential=); nothing here
writes it anywhere. Formats and steps: docs/INTERNALS.md.

Exit codes: 0 done (a message that could not be sent is no error: it is in
the state and the status), 1 failed, 64 wrong call, 75 another run holds the
lock.
"""

import fcntl
import json
import os
import socket
import sys
import tempfile
import time

# -I leaves the script's directory out of sys.path; the modules come from this root-owned
# directory only, and no bytecode is written next to them.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import hslib  # noqa: E402
import hsnotify  # noqa: E402
import hsproc  # noqa: E402
import hsstatus  # noqa: E402

CONF = "/etc/cockpit-host-settings/notify.conf"
PROFILE = "/etc/cockpit-host-settings/profile.conf"
USAGE = "usage: host-settings-notify.py run | test ntfy | test mail"


class Env:
    """Everything a run touches; the tests replace what they need."""

    def __init__(self, **kw):
        self.conf = CONF
        self.profile = PROFILE
        self.token_file = os.path.join(os.environ.get("CREDENTIALS_DIRECTORY", "/nonexistent"), "ntfy.token")
        self.state_dir = os.environ.get("STATE_DIRECTORY", "/var/lib/cockpit-host-settings").split(":")[0]
        self.root = "/usr/share/cockpit"
        self.now = None
        self.http = hsnotify.http
        self.smtp = None
        self.runner = hsproc.run
        self.lstat = os.lstat
        self.host = socket.gethostname().split(".")[0] or "localhost"
        self.fqdn = None
        self.out = sys.stdout
        self.__dict__.update(kw)

    def path(self, name):
        return os.path.join(self.state_dir, name)

    def clock(self):
        return self.now if self.now is not None else time.time()

    def domain(self):
        if self.fqdn is None:
            try:
                self.fqdn = socket.getfqdn() or self.host
            except OSError:
                self.fqdn = self.host
        return self.fqdn


def read_config(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(1 << 16)
    except OSError:
        text = ""
    return hslib.parse_config(text)


def read_profile(path):
    """The host's target profile as profile_coverage() takes it ({expect, skip}), or None
    without one (or when the file cannot be read)."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(1 << 16)
    except (OSError, UnicodeDecodeError):
        return None
    prof, _ = hslib.parse_profile(text)
    if not hslib.profile_set(prof):
        return None
    return {"expect": hslib.resolve_expect(prof["roles"], prof["extra"]), "skip": prof["skip"]}


def read_token(path):
    """The ntfy token, or None (an empty file means: no token)."""
    try:
        with open(path, encoding="utf-8") as f:
            token = f.read(4096).strip()
    except (OSError, UnicodeDecodeError):
        return None
    return token if hslib.valid_token(token) else None


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def write_atomic(path, doc, mode):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + "-", suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def lock(env):
    fd = os.open(env.path(".lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def target(cfg):
    return f"{cfg['ntfy_url']}/{cfg['ntfy_topic']}" if cfg.get("ntfy_url") and cfg.get("ntfy_topic") else None


def seen_of(snapshot):
    """What the page shows of the last run: every add-on with its items."""
    out = {}
    for pkg, a in snapshot.items():
        out[pkg] = {k: a[k] for k in ("label", "kind", "reason", "code", "state", "version", "summary")}
        out[pkg]["items"] = [dict({k: i[k] for k in ("id", "title", "state", "value", "detail", "link")},
                                  stale=i["stale_at"] is not None) for i in a["items"]]
    return out


def write_status(env, last, cfg):
    if not last.get("run"):
        return
    info = {"channels": hslib.channels(cfg), "delivery": last.get("delivery"), "access": last.get("access"),
            "run": last["run"]}
    write_atomic(env.path("status.json"), hsstatus.status_file(info, hsstatus.version()), 0o644)


def deliver(env, cfg, token, msg):
    results = {}
    for ch in hslib.channels(cfg):
        if ch == "ntfy":
            results[ch] = hsnotify.send_ntfy(cfg, msg, token, env.http)
        else:
            results[ch] = hsnotify.send_mail(cfg, msg, env.domain(), env.smtp)
    return results


def do_run(env):
    started = time.monotonic()
    now = env.clock()
    cfg, bad = read_config(env.conf)
    token = read_token(env.token_file)
    last = load(env.path("last.json"))
    if last.get("format") != hsnotify.FORMAT:
        last = {}
    lang = cfg["language"]
    snapshot = hsnotify.collect(lang, now, env.runner, env.root, env.lstat)
    profile = read_profile(env.profile)
    coverage = hsnotify.profile_coverage(profile, snapshot, now, hsnotify.installed(env.root)) if profile else None
    chans = hslib.channels(cfg)
    sent = last.get("sent") if isinstance(last.get("sent"), dict) else {}
    told = None
    if chans:
        summary = not last.get("baseline_at")
        if summary:
            # The first message names the gaps in what the profile expects with the problems, and
            # takes them as told.
            gaps, told_gaps = hsnotify.coverage_changes({}, coverage, now)
            msg = hsnotify.message(lang, env.host, snapshot=snapshot, summary=True, gaps=gaps)
            new_sent = dict(hsnotify.baseline(snapshot, now), **told_gaps)
            count = len(hsnotify.problems_of(snapshot)) + len(gaps)
        else:
            # A profile set up since the last run: its gaps are changes from OK, told once.
            events, new_sent = hsnotify.changes(sent, snapshot, now, coverage)
            msg = hsnotify.message(lang, env.host, events=events) if events else None
            count = len(events)
        if msg:
            results = deliver(env, cfg, token, msg)
            last["delivery"] = {"at": hslib.iso(now), "summary": summary, "changes": count, "title": msg["title"],
                                "results": results}
            told = results
            # Nothing went out: the same changes are told on the next run.
            if any(r["ok"] for r in results.values()):
                sent = new_sent
                if summary:
                    last["baseline_at"] = hslib.iso(now)
        else:
            sent = new_sent
    # The topic's protection without a login, once a day (reading only; the write probe runs
    # on the page's request alone).
    access = last.get("access") if isinstance(last.get("access"), dict) else None
    if access and access.get("target") != target(cfg):
        access = None
    if "ntfy" in chans:
        at = hslib.parse_time((access or {}).get("at"))
        if at is None or now - at >= hsnotify.CHECK_EVERY:
            steps = hsnotify.check_ntfy(cfg, token, lang, env.host, env.http, ("reach", "tls", "anon-read"))
            access = dict(hsnotify.access_of(steps, access), at=hslib.iso(now), target=target(cfg), full=False,
                          steps=steps)
    last.update(format=hsnotify.FORMAT, sent=sent, seen=seen_of(snapshot), access=access,
                coverage=coverage[0] if coverage else None,
                run={"at": hslib.iso(now), "took_ms": int((time.monotonic() - started) * 1000),
                     "addons": len(snapshot), "bad_lines": bad})
    write_atomic(env.path("last.json"), last, 0o600)
    write_status(env, last, cfg)
    bad_addons = sorted(p for p, a in snapshot.items() if a["kind"] in hsnotify.ERROR_KINDS)
    print(f"{len(snapshot)} add-ons" + (f", not readable: {', '.join(bad_addons)}" if bad_addons else "") +
          ("" if told is None else ", message: " + ", ".join(f"{c} {r['reason']}" for c, r in sorted(told.items()))),
          file=env.out)
    return 0


def do_test(env, channel):
    now = env.clock()
    cfg, _ = read_config(env.conf)
    token = read_token(env.token_file)
    lang = cfg["language"]
    last = load(env.path("last.json"))
    if last.get("format") != hsnotify.FORMAT:
        last = {}
    if channel not in hslib.channels(cfg):
        result = {"at": hslib.iso(now), "channel": channel, "error": "not-set-up"}
    elif channel == "ntfy":
        steps = hsnotify.check_ntfy(cfg, token, lang, env.host, env.http)
        access = dict(hsnotify.access_of(steps), at=hslib.iso(now), target=target(cfg), full=True, steps=steps)
        result = {"at": hslib.iso(now), "channel": "ntfy", "target": target(cfg), "token": bool(token),
                  "steps": steps, "access": {k: access[k] for k in ("state", "anon_read", "anon_write")}}
        last["access"] = access
        if last.get("run"):
            last["format"] = hsnotify.FORMAT
            write_atomic(env.path("last.json"), last, 0o600)
            write_status(env, last, cfg)
    else:
        result = {"at": hslib.iso(now), "channel": "mail",
                  "result": hsnotify.send_mail(cfg, hsnotify.test_message(lang, env.host), env.domain(), env.smtp)}
    write_atomic(env.path(f"test-{channel}.json"), result, 0o600)
    json.dump(result, env.out, ensure_ascii=False)
    env.out.write("\n")
    return 0


def main(argv, env=None):
    env = env or Env()
    if argv == ["run"]:
        verb = do_run
    elif len(argv) == 2 and argv[0] == "test" and argv[1] in ("ntfy", "mail"):
        def verb(e):
            return do_test(e, argv[1])
    else:
        print(USAGE, file=sys.stderr)
        return 64
    held = lock(env)
    if held is None:
        print("another run holds the lock", file=sys.stderr)
        return 75
    try:
        return verb(env)
    except Exception as e:  # noqa: BLE001 - the previous state stays as it is
        print(f"failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        os.close(held)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
