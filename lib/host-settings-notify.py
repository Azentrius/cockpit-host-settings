#!/usr/bin/python3 -IB
# SPDX-License-Identifier: Apache-2.0
"""Host Settings, the Cockpit add-on: the notifier.

  host-settings-notify.py run             one run (cockpit-host-settings-notify.service, every 5 minutes)
  host-settings-notify.py test ntfy       "Check connection" of the page (cockpit-host-settings-test@ntfy)
  host-settings-notify.py test mail       "Send a test message" of the page (cockpit-host-settings-test@mail)
  host-settings-notify.py test heartbeat  "Send a test signal" of the page (cockpit-host-settings-test@heartbeat)

Runs as the user cockpit-host-settings, never as root: it reads the status
verbs of the Azentrius add-ons on this host (they work without root, as the
status contract asks), tells what changed through ntfy and/or the local MTA,
and writes its state to /var/lib/cockpit-host-settings. The ntfy token comes
from systemd as the credential ntfy.token (LoadCredential=); nothing here
writes it anywhere. Each channel keeps its own health (lib/hschannels.py): a
channel that did not accept a message or a test stays failed until it accepts
one again with the settings set now, also through runs with nothing to send.
With a watchdog set up (credential heartbeat.conf), every run that holds the
lock ends with a signal to it: success once the run is done and its state
saved, failure when it could not run or a channel set up is failed
(lib/hsheartbeat.py). Formats and steps: docs/INTERNALS.md.

Exit codes: 0 done (a message that could not be sent is no error: it is in
the state and the status, and the watchdog is told), 1 failed, 64 wrong call,
75 another run or test holds the lock (no signal: the run that holds it gives
one).
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
import hschannels  # noqa: E402
import hsheartbeat  # noqa: E402
import hslib  # noqa: E402
import hsnotify  # noqa: E402
import hsproc  # noqa: E402
import hsstatus  # noqa: E402

CONF = "/etc/cockpit-host-settings/notify.conf"
PROFILE = "/etc/cockpit-host-settings/profile.conf"
USAGE = "usage: host-settings-notify.py run | test ntfy | test mail | test heartbeat"


class ConfigError(Exception):
    """notify.conf is there but cannot be read: the run cannot know where messages go."""


class Env:
    """Everything a run touches; the tests replace what they need."""

    def __init__(self, **kw):
        creds = os.environ.get("CREDENTIALS_DIRECTORY", "/nonexistent")
        self.conf = CONF
        self.profile = PROFILE
        self.token_file = os.path.join(creds, "ntfy.token")
        self.heartbeat_file = os.path.join(creds, "heartbeat.conf")
        self.state_dir = os.environ.get("STATE_DIRECTORY", "/var/lib/cockpit-host-settings").split(":")[0]
        self.root = "/usr/share/cockpit"
        self.now = None
        self.http = hsnotify.http
        self.smtp = None
        self.runner = hsproc.run
        self.lstat = os.lstat
        self.sleep = time.sleep
        self.host = socket.gethostname().split(".")[0] or "localhost"
        self.fqdn = None
        self.out = sys.stdout
        # The journal of a test, whose standard output is its result.
        self.err = sys.stderr
        # The result of a run for the watchdog: where it got to, and what do_run decided.
        self.stage = "config"
        self.outcome = None
        self.saved = None
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
    """(config, bad line numbers) of notify.conf; the defaults when there is none. A file that is
    there but cannot be read stops the run (ConfigError): it says where messages go."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(1 << 16)
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path} cannot be read: {getattr(e, 'strerror', None) or 'UTF-8'}") from None
    return hslib.parse_config(text)


def read_heartbeat(env):
    """The watchdog the credential sets up ({adapter, url}, or {"invalid": True}), or None."""
    return hsheartbeat.read_conf(env.heartbeat_file)


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


def heartbeat_info(env):
    """What the status says about the watchdog: None without one; else whether its settings hold
    and the last signal's answer (heartbeat.json), never the address."""
    conf = read_heartbeat(env)
    if conf is None:
        return None
    last = load(env.path("heartbeat.json"))
    return {"invalid": bool(conf.get("invalid")), "at": last.get("at"), "signal": last.get("signal"),
            "why": last.get("why") if isinstance(last.get("why"), str) else None,
            "result": last.get("result") if isinstance(last.get("result"), dict) else None}


def write_status(env, last, cfg):
    if not last.get("run"):
        return
    info = {"channels": hslib.channels(cfg), "delivery": last.get("delivery"), "access": last.get("access"),
            "run": last["run"], "heartbeat": heartbeat_info(env),
            "channel_health": hschannels.health(hschannels.records_of(last.get("channels")), cfg)}
    write_atomic(env.path("status.json"), hsstatus.status_file(info, hsstatus.version()), 0o644)


def load_records(last, cfg, now, log):
    """The records of the channels (lib/hschannels.py) from last.json, brought in line with the
    settings and the last delivery; what changed goes to the journal (log)."""
    records, notes = hschannels.reconcile(hschannels.records_of(last.get("channels")), cfg, last.get("delivery"), now)
    for ch, what, detail in notes:
        print(hschannels.note_text(ch, what, detail), file=log)
    return records


def take(records, channel, ok, reason, target, via, at, current, log):
    """One result of a channel into its record; a failure that begins or ends goes to the journal
    (log), the end once: the failure is gone then."""
    before = (records.get(channel) or {}).get("failed")
    what = hschannels.record(records, channel, ok, reason, target, via, at, current)
    if what == "failed":
        print(hschannels.note_text(channel, "failed", f"{via}, {reason}"), file=log)
    elif what == "recovered":
        print(hschannels.note_text(channel, "recovered", f"{via}, failing since {before['since']}"), file=log)
    elif what == "stale":
        print(hschannels.note_text(channel, "stale", None), file=log)
    return what


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
    # env.stage says how far the run got, for the failure signal should it end with an exception.
    env.stage = "config"
    cfg, bad = read_config(env.conf)
    token = read_token(env.token_file)
    last = load(env.path("last.json"))
    if last.get("format") != hsnotify.FORMAT:
        last = {}
    # Each channel's health, judged whether this run sends anything or not: what is known of it
    # from earlier runs and tests, in line with the settings now.
    records = load_records(last, cfg, now, env.out)
    lang = cfg["language"]
    env.stage = "collect"
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
            # takes them as told; an add-on whose status cannot be read is named once.
            problems = hsnotify.problems_of(snapshot)
            unreadable = {e["pkg"] for e in problems if not e["id"]}
            gaps, told_gaps = hsnotify.coverage_changes({}, coverage, now)
            gaps = [g for g in gaps if g["check"] or g["pkg"] not in unreadable]
            msg = hsnotify.message(lang, env.host, snapshot=snapshot, summary=True, gaps=gaps)
            new_sent = dict(hsnotify.baseline(snapshot, now), **told_gaps)
            count = len(problems) + len(gaps)
        else:
            # A profile set up since the last run: its gaps are changes from OK, told once.
            events, new_sent = hsnotify.changes(sent, snapshot, now, coverage)
            msg = hsnotify.message(lang, env.host, events=events) if events else None
            count = len(events)
        if msg:
            env.stage = "delivery"
            results = deliver(env, cfg, token, msg)
            last["delivery"] = {"at": hslib.iso(now), "summary": summary, "changes": count, "title": msg["title"],
                                "results": results}
            told = results
            # Each channel's result into its record: a failure begins, or a success for the target
            # set now ends one.
            for ch, r in sorted(results.items()):
                tg = hschannels.target_of(cfg, ch)
                take(records, ch, r["ok"], r["reason"], tg, "run", hslib.iso(now), tg, env.out)
            # Nothing went out: the same changes are told on the next run. Taken by one channel,
            # they are told: the channel that failed does not get them again (no queue per
            # channel), and stays failed until it accepts a message or a test.
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
            env.stage = "delivery"
            steps = hsnotify.check_ntfy(cfg, token, lang, env.host, env.http, ("reach", "tls", "anon-read"))
            access = dict(hsnotify.access_of(steps, access), at=hslib.iso(now), target=target(cfg), full=False,
                          steps=steps)
    last.update(format=hsnotify.FORMAT, sent=sent, seen=seen_of(snapshot), access=access,
                coverage=coverage[0] if coverage else None, channels=records,
                run={"at": hslib.iso(now), "took_ms": int((time.monotonic() - started) * 1000),
                     "addons": len(snapshot), "bad_lines": bad})
    env.stage = "state"
    write_atomic(env.path("last.json"), last, 0o600)
    write_status(env, last, cfg)
    # The run is done and its state saved: now its result for the watchdog, decided here and not
    # from the exit code (0 also when a message could not be sent). A channel set up that is
    # failed makes it a failure: one that did not accept this run's message, even when another
    # took it and it is not sent again, and one that failed before and has not accepted a
    # message or a test since, also when this run had nothing to send. An add-on that cannot be
    # read or a gap in what the profile expects is a finding, told through the messages, and no
    # failure of the notifier.
    env.stage = "done"
    failing = hschannels.failing(records, cfg)
    failed_now = any(not r.get("ok") for r in (told or {}).values())
    env.outcome = (("failure", "delivery" if failed_now else "channel", [f"{c} {why}" for c, why in failing])
                   if failing else ("success", None, None))
    env.saved = (last, cfg)
    bad_addons = sorted(p for p, a in snapshot.items() if a["kind"] in hsnotify.ERROR_KINDS)
    print(f"{len(snapshot)} add-ons" + (f", not readable: {', '.join(bad_addons)}" if bad_addons else "") +
          ("" if told is None else ", message: " + ", ".join(f"{c} {r['reason']}" for c, r in sorted(told.items()))) +
          (", failing: " + ", ".join(f"{c} {why}" for c, why in failing) if failing else ""),
          file=env.out)
    return 0


# Where a run that ended with an exception got to, as the reason of its failure signal.
STAGE_WHY = {"config": "config", "collect": "collect", "delivery": "delivery", "state": "state"}


def heartbeat(env):
    """The signal to the watchdog at the end of a run that held the lock: nothing at all without
    one; else success or failure as the run decided (env.outcome), and the answer in
    heartbeat.json. The answer never changes last.json: a signal that did not get through resets
    no finding and makes no message go out again; the status shows it (item heartbeat)."""
    conf = read_heartbeat(env)
    if conf is None:
        return
    signal, why, detail = env.outcome or ("failure", STAGE_WHY.get(env.stage, "error"), None)
    if conf.get("invalid"):
        result = {"ok": False, "code": None, "reason": "invalid", "attempts": 0}
    else:
        result = hsheartbeat.send(conf["adapter"], conf["url"], signal, why, detail, env.http, env.sleep)
    doc = {"at": hslib.iso(env.clock()), "adapter": conf.get("adapter"), "signal": signal, "why": why,
           "result": result}
    print(f"watchdog: {signal}, " + ("accepted" if result["ok"] else "not accepted: " + result["reason"]), file=env.out)
    try:
        write_atomic(env.path("heartbeat.json"), doc, 0o600)
        if env.saved:
            write_status(env, *env.saved)
    except OSError as e:
        print(f"the watchdog's answer could not be saved: {e.strerror}", file=sys.stderr)


def do_test(env, channel):
    now = env.clock()
    if channel == "heartbeat":
        # A test signal: Healthchecks' /log (counts as neither success nor failure), Uptime Kuma's
        # status=up (Uptime Kuma has nothing else). The answer, never the address.
        conf = read_heartbeat(env)
        if conf is None or conf.get("invalid"):
            result = {"at": hslib.iso(now), "channel": "heartbeat", "error": "invalid" if conf else "not-set-up"}
        else:
            result = {"at": hslib.iso(now), "channel": "heartbeat", "adapter": conf["adapter"],
                      "result": hsheartbeat.send(conf["adapter"], conf["url"], "test", request=env.http, sleep=env.sleep)}
        write_atomic(env.path("test-heartbeat.json"), result, 0o600)
        json.dump(result, env.out, ensure_ascii=False)
        env.out.write("\n")
        return 0
    cfg, _ = read_config(env.conf)
    token = read_token(env.token_file)
    lang = cfg["language"]
    if channel not in hslib.channels(cfg):
        result = {"at": hslib.iso(now), "channel": channel, "error": "not-set-up"}
        write_atomic(env.path(f"test-{channel}.json"), result, 0o600)
        json.dump(result, env.out, ensure_ascii=False)
        env.out.write("\n")
        return 0
    tg = hschannels.target_of(cfg, channel)
    access = None
    if channel == "ntfy":
        steps = hsnotify.check_ntfy(cfg, token, lang, env.host, env.http)
        access = dict(hsnotify.access_of(steps), at=hslib.iso(now), target=target(cfg), full=True, steps=steps)
        result = {"at": hslib.iso(now), "channel": "ntfy", "target": target(cfg), "token": bool(token),
                  "steps": steps, "access": {k: access[k] for k in ("state", "anon_read", "anon_write")}}
        ok, reason = hschannels.check_outcome(steps)
    else:
        r = hsnotify.send_mail(cfg, hsnotify.test_message(lang, env.host), env.domain(), env.smtp)
        result = {"at": hslib.iso(now), "channel": "mail", "target": tg, "result": r}
        ok, reason = r["ok"], r["reason"]
    # The channel's health, under the lock (no run in between), with last.json and the settings
    # read now: the result counts only for the target it was made with, and only while that is
    # the one set up. A success for it ends a known failure, also without a new finding.
    last = load(env.path("last.json"))
    if last.get("format") != hsnotify.FORMAT:
        last = {}
    try:
        now_cfg, _ = read_config(env.conf)
    except ConfigError:
        now_cfg = None
    if now_cfg is not None:
        records = load_records(last, now_cfg, now, env.err)
        what = take(records, channel, ok, reason, tg, "test", hslib.iso(now), hschannels.target_of(now_cfg, channel),
                    env.err)
        if access is not None and target(now_cfg) == access["target"]:
            last["access"] = access
        last.update(format=hsnotify.FORMAT, channels=records)
        write_atomic(env.path("last.json"), last, 0o600)
        write_status(env, last, now_cfg)
        result["health"] = hschannels.state_of(records.get(channel), hschannels.target_of(now_cfg, channel))
        if what == "recovered":
            result["recovered"] = True
    write_atomic(env.path(f"test-{channel}.json"), result, 0o600)
    json.dump(result, env.out, ensure_ascii=False)
    env.out.write("\n")
    return 0


def main(argv, env=None):
    env = env or Env()
    if argv == ["run"]:
        verb = do_run
    elif len(argv) == 2 and argv[0] == "test" and argv[1] in ("ntfy", "mail", "heartbeat"):
        def verb(e):
            return do_test(e, argv[1])
    else:
        print(USAGE, file=sys.stderr)
        return 64
    held = lock(env)
    if held is None:
        # No signal at all: the run or test that holds the lock is the one that tells the watchdog.
        print("another run holds the lock", file=sys.stderr)
        return 75
    try:
        try:
            code = verb(env)
        except Exception as e:  # noqa: BLE001 - the previous state stays as it is
            print(f"failed: {type(e).__name__}: {e}", file=sys.stderr)
            code = 1
            env.outcome = ("failure", STAGE_WHY.get(env.stage, "error"), None)
        if verb is do_run:
            try:
                heartbeat(env)
            except Exception as e:  # noqa: BLE001 - the run's own result stands; the type only, never a text
                print(f"watchdog: failed: {type(e).__name__}", file=sys.stderr)
        return code
    finally:
        os.close(held)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
