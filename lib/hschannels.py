# SPDX-License-Identifier: Apache-2.0
"""The health of each channel of Host Settings (ntfy, e-mail), kept in last.json under
"channels" and judged for every channel set up, whether a run has something to send or not.

A channel is "ok" once it accepted something with the settings set now: the message of a run, or
a test from the page ("Check connection" for ntfy, "Send a test message" for e-mail). Accepted
means that the ntfy server or the local mail server took it, not that anyone read it. From a
failure on, a channel is "failed" until it accepts again with the settings set now; a run with
nothing to send changes nothing. A channel that has accepted nothing with the settings set now is
"unknown", never "ok".

Each result belongs to the target it was made with: the server and topic of ntfy (URL/TOPIC), the
address of e-mail. Never the token, nor anything made from it: a new token is the same target. A
test whose target is no longer the one set up when it ends proves nothing either way. A channel
taken out of the settings is no longer counted; its removal is a change of the settings, never a
repair, and it starts anew, unknown, when it is set up again.

The records, per channel (times as ISO 8601, via "run" or "test"):

  failed     {since, at, via, reason, target}   the failure that has not ended yet
  ok         {at, via, target}                  the last time it accepted something
  recovered  {at, via, since, reason}           the last failure that ended, and how
  removed    {at, failed_since}                 taken out of the settings (failed_since: or null)
  reset_at   when it was set up again after a removal

Lives in /usr/lib/cockpit-host-settings (root-owned). Standard library only, no side effects on
import, nothing here goes to the network or writes a file. Formats and rules: docs/INTERNALS.md,
"Channel health".
"""

import re

import hslib

CHANNELS = ("ntfy", "mail")
VIA = ("run", "test")
STATES = ("ok", "failed", "unknown")
REASON_RE = re.compile(r"[a-z0-9-]{1,40}")
TARGET_MAX = 300


def target_of(cfg, channel):
    """The target of a channel, to tell which settings a result belongs to: URL/TOPIC for ntfy,
    the address for e-mail; None when the channel is not set up."""
    if channel == "ntfy":
        return f"{cfg['ntfy_url']}/{cfg['ntfy_topic']}" if cfg.get("ntfy_url") and cfg.get("ntfy_topic") else None
    if channel == "mail":
        return cfg.get("mail_to") or None
    return None


def targets(cfg):
    """{channel: target} for every channel set up, in the order of hslib.channels()."""
    return {c: target_of(cfg, c) for c in hslib.channels(cfg)}


# ---------------------------------------------------------------------------
# The records in last.json

def _time(v):
    return v if isinstance(v, str) and hslib.parse_time(v) is not None else None


def _reason(v):
    return v if isinstance(v, str) and REASON_RE.fullmatch(v) else "network"


def _target(v):
    return v if isinstance(v, str) and 0 < len(v) <= TARGET_MAX else None


def _via(v):
    return v if v in VIA else "run"


def records_of(doc):
    """The records of last.json's "channels", cleaned: what does not hold is left out. A file of
    0.2.0 or older has none."""
    out = {}
    if not isinstance(doc, dict):
        return out
    for ch in CHANNELS:
        rec = doc.get(ch)
        if not isinstance(rec, dict):
            continue
        clean = {}
        f = rec.get("failed")
        if isinstance(f, dict) and _time(f.get("at")) and _time(f.get("since")):
            clean["failed"] = {"since": f["since"], "at": f["at"], "via": _via(f.get("via")),
                               "reason": _reason(f.get("reason")), "target": _target(f.get("target"))}
        o = rec.get("ok")
        if isinstance(o, dict) and _time(o.get("at")):
            clean["ok"] = {"at": o["at"], "via": _via(o.get("via")), "target": _target(o.get("target"))}
        r = rec.get("recovered")
        if isinstance(r, dict) and _time(r.get("at")) and _time(r.get("since")):
            clean["recovered"] = {"at": r["at"], "via": _via(r.get("via")), "since": r["since"],
                                  "reason": _reason(r.get("reason"))}
        m = rec.get("removed")
        if isinstance(m, dict) and _time(m.get("at")):
            clean["removed"] = {"at": m["at"], "failed_since": _time(m.get("failed_since"))}
        if _time(rec.get("reset_at")):
            clean["reset_at"] = rec["reset_at"]
        out[ch] = clean
    return out


def _latest(rec):
    """The newest time a record knows anything of, as seconds; None for an empty one."""
    times = [(rec.get(k) or {}).get("at") for k in ("failed", "ok", "recovered", "removed")] + [rec.get("reset_at")]
    times = [hslib.parse_time(t) for t in times if t]
    return max(times) if times else None


def _put(rec, ok, reason, target, via, at):
    """One result into a record (changed in place). Returns "failed" when a failure begins,
    "recovered" when it ends, else None. A success without a target (from a version that kept
    none) ends no failure and does not replace a success with a target."""
    if ok:
        if target is not None or not (rec.get("ok") or {}).get("target"):
            rec["ok"] = {"at": at, "via": via, "target": target}
        f = rec.get("failed")
        if f and target is not None:
            del rec["failed"]
            rec["recovered"] = {"at": at, "via": via, "since": f["since"], "reason": f["reason"]}
            return "recovered"
        return None
    f = rec.get("failed")
    rec["failed"] = {"since": f["since"] if f else at, "at": at, "via": via, "reason": _reason(reason),
                     "target": target}
    return None if f else "failed"


def record(records, channel, ok, reason, target, via, at, current):
    """Takes one result of a channel into records (changed in place): whether it accepted, the
    reason word, the target it was made with, how ("run" or "test"), when (ISO 8601), and the
    target set up now. Returns "failed" when a failure begins, "recovered" when a success ends
    one (once: the failure is gone then), "stale" when the result belongs to a target that is no
    longer set up (nothing changes), else None."""
    if current is None or target != current:
        return "stale"
    return _put(records.setdefault(channel, {}), ok, reason, target, via, at)


def reconcile(records, cfg, delivery, now):
    """(records, notes) at the start of a run or a test, under the lock: the records brought in
    line with the settings and with the last delivery. notes: [(channel, what, detail)] for the
    journal.

    - A channel no longer set up is removed: no longer counted, its failure neither. A change of
      the settings, never a repair: no recovery is recorded. ("removed", failed_since or None)
    - A channel set up again after its removal starts anew, unknown. ("reset", None)
    - The last delivery, when it is newer than everything the record of a channel set up knows,
      was written by 0.2.0 or older (the first run after the update, or runs after going back to
      such a version for a while). Its result is taken in as it was, without a target, since that
      version kept none: a failure stays a failure, a success proves nothing for the target set
      now. ("taken", reason)"""
    records = {c: dict(r) for c, r in (records or {}).items()}
    chans = hslib.channels(cfg)
    at = hslib.iso(now)
    notes = []
    for ch in CHANNELS:
        rec = records.get(ch)
        if rec is None:
            continue
        if ch not in chans:
            if "removed" not in rec:
                since = (rec.get("failed") or {}).get("since")
                records[ch] = {"removed": {"at": at, "failed_since": since}}
                notes.append((ch, "removed", since))
        elif "removed" in rec:
            records[ch] = {"reset_at": at}
            notes.append((ch, "reset", None))
    if isinstance(delivery, dict) and isinstance(delivery.get("results"), dict):
        when = hslib.parse_time(delivery.get("at"))
        for ch in chans:
            r = delivery["results"].get(ch)
            if when is None or not isinstance(r, dict):
                continue
            latest = _latest(records.get(ch) or {})
            if latest is not None and latest >= when:
                continue
            _put(records.setdefault(ch, {}), r.get("ok") is True, r.get("reason"), None, "run", delivery["at"])
            notes.append((ch, "taken", _reason(r.get("reason")) if r.get("ok") is not True else "sent"))
    return records, notes


# ---------------------------------------------------------------------------
# What the records say

def state_of(rec, current):
    """"failed", "ok" or "unknown" of a channel set up with the target current."""
    rec = rec or {}
    if rec.get("failed"):
        return "failed"
    ok = rec.get("ok") or {}
    if current is not None and ok.get("target") == current:
        return "ok"
    return "unknown"


def health(records, cfg):
    """{channel: view} for every channel set up, in the order of hslib.channels(): its state and
    what the status and the page show of it; never the target.

      failed   {state, since, at, via, reason}
      ok       {state, at, via, recovered: {since, reason} or None}   recovered: this success
               ended a failure
      unknown  {state}"""
    out = {}
    for ch, current in targets(cfg).items():
        rec = records.get(ch) or {}
        state = state_of(rec, current)
        view = {"state": state}
        if state == "failed":
            f = rec["failed"]
            view.update(since=f["since"], at=f["at"], via=f["via"], reason=f["reason"])
        elif state == "ok":
            o, r = rec["ok"], rec.get("recovered")
            view.update(at=o["at"], via=o["via"],
                        recovered={"since": r["since"], "reason": r["reason"]} if r and r["at"] == o["at"] else None)
        out[ch] = view
    return out


def failing(records, cfg):
    """[(channel, reason)] of the channels set up that are "failed", sorted: what makes the
    watchdog's signal a failure."""
    return [(ch, v["reason"]) for ch, v in sorted(health(records, cfg).items()) if v["state"] == "failed"]


def check_outcome(steps):
    """(ok, reason) of "Check connection" for the channel ntfy: whether the server accepted the
    test message (step publish); when it could not be reached, why (step reach). The steps about
    the topic's protection say nothing about the channel."""
    by = {s.get("id"): s for s in steps or () if isinstance(s, dict)}
    publish = by.get("publish") or {}
    if publish.get("result") == "ok":
        return True, "sent"
    if publish.get("result") == "skipped":
        return False, _reason((by.get("reach") or {}).get("reason"))
    return False, _reason(publish.get("reason"))


def note_text(channel, what, detail):
    """One line for the journal about a change of a channel's record."""
    return {
        "failed": f"channel {channel}: failed ({detail})",
        "recovered": f"channel {channel}: accepted again ({detail})",
        "removed": f"channel {channel}: removed from the settings" +
                   (f" while failing since {detail}: a change of the settings, not a repair" if detail else ""),
        "reset": f"channel {channel}: set up again, not confirmed yet",
        "taken": f"channel {channel}: the last message of an earlier version taken in ({detail}), its target unknown",
        "stale": f"channel {channel}: the result belongs to settings changed since, not counted",
    }.get(what, f"channel {channel}: {what}")
