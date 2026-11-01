#!/usr/bin/python3 -IB
# SPDX-License-Identifier: Apache-2.0
"""Host Settings, the Cockpit add-on: the fixed verbs of the page and of the
Status Board.

  host-settings.py status [--lang en|de]     the status contract (schema 1), no root
  host-settings.py show                      settings, timer, last run and checks, for the page (root)
  host-settings.py set ntfy URL TOPIC        the ntfy server and topic (root)
  host-settings.py set mail ADDRESS          the recipient of e-mail (root)
  host-settings.py set language en|de        the language of the messages (root)
  host-settings.py unset ntfy | unset mail   (root)
  host-settings.py token set                 the ntfy token, read from standard input (root)
  host-settings.py token remove              (root)
  host-settings.py check ntfy                "Check connection" (cockpit-host-settings-test@ntfy, root)
  host-settings.py test mail                 a test message (cockpit-host-settings-test@mail, root)
  host-settings.py run                       one run of the notifier now (root)
  host-settings.py install timer             the regular run: every 5 minutes (root)
  host-settings.py remove timer              switch it off (root)
  host-settings.py heartbeat set healthchecks|uptime-kuma   the watchdog; its ping or push URL, a
                                             secret, read from standard input (root)
  host-settings.py heartbeat remove          no watchdog any more (root)
  host-settings.py heartbeat test            a test signal (cockpit-host-settings-test@heartbeat, root)
  host-settings.py profile show              the host's target profile (no root: the file is 0644)
  host-settings.py profile roles [ROLE ...]  set the roles; none: the add-ons of every host (root)
  host-settings.py profile expect add|remove ADDON[/CHECK]   an extra expectation (root)
  host-settings.py profile skip add ADDON/CHECK [YYYY-MM-DD] leave a check out on purpose, until
                                             that day if given; the reason on standard input (root)
  host-settings.py profile skip remove ADDON/CHECK (root)
  host-settings.py profile remove            no profile any more (root)
  host-settings.py menu show                 Cockpit's menu: every entry and what the override files
                                             change in it (no root; run as the user whose menu it is)
  host-settings.py menu apply system|user    order, labels and hidden entries of Cockpit's menu, as a
                                             plan in JSON on standard input: system for all users
                                             (root), user just for the calling user (never as root)

Everything else is a wrong call (exit 64). Every argument is checked here.
The changes this helper makes are the settings in /etc/cockpit-host-settings
(notify.conf and profile.conf 0644, ntfy.token and heartbeat.conf 0600, all
root's; the profile keeps its previous version in profile.conf.bak), the
add-on's own timer, and the editor's part of Cockpit's manifest overrides
(order, label and null of the menu entries; hsmenu.py): for all users in
/etc/cockpit/<folder>.override.json (0644, root's; the previous versions in
/var/lib/cockpit-host-settings/backup/menu), just for the calling user in
~/.config/cockpit. The token and the watchdog's URL never appear in an
argument, an output or a log. Sending and checking run in the add-on's
services as their own user, never as root.

Exit codes: 0 ok, 1 failed, 2 unit missing, 64 wrong call, 75 busy, 77 needs
root. HOST_SETTINGS_LANG=de|en selects the language of the messages.
"""

import copy
import fcntl
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import hschannels  # noqa: E402
import hsheartbeat  # noqa: E402
import hslib  # noqa: E402
import hsmenu  # noqa: E402
import hsproc  # noqa: E402
import hsstatus  # noqa: E402

CONF_DIR = "/etc/cockpit-host-settings"
CONF = CONF_DIR + "/notify.conf"
PROFILE = CONF_DIR + "/profile.conf"
TOKEN = CONF_DIR + "/ntfy.token"
HEARTBEAT = CONF_DIR + "/heartbeat.conf"
STATE_DIR = "/var/lib/cockpit-host-settings"
LOCK = "/run/cockpit-host-settings.lock"
SERVICE = "cockpit-host-settings-notify.service"
TIMER = "cockpit-host-settings-notify.timer"
TEST = "cockpit-host-settings-test@{}.service"
USER = "cockpit-host-settings"
LANG_RE = re.compile(r"[A-Za-z]{2,3}(?:[_-][A-Za-z0-9]{2,8})?")
USAGE = ("usage: host-settings.py status [--lang en|de] | show | set ntfy URL TOPIC | set mail ADDRESS | "
         "set language en|de | unset ntfy|mail | token set|remove | check ntfy | test mail | run | "
         "install timer | remove timer | heartbeat set healthchecks|uptime-kuma | heartbeat remove | "
         "heartbeat test | profile show | profile roles [ROLE ...] | "
         "profile expect add|remove ADDON[/CHECK] | profile skip add ADDON/CHECK [YYYY-MM-DD] | "
         "profile skip remove ADDON/CHECK | profile remove | menu show | menu apply system|user")
MENU_BACKUP = STATE_DIR + "/backup/menu"
ENV = {"PATH": hsproc.PATH, "LC_ALL": "C.UTF-8", "TZ": "UTC"}


class Fail(Exception):
    def __init__(self, code, de, en):
        super().__init__(en)
        self.code, self.de, self.en = code, de, en


def say(de, en, stream=None):
    print(de if os.environ.get("HOST_SETTINGS_LANG") == "de" else en, file=stream or sys.stdout)


WRONG = Fail(64, USAGE, USAGE)


def need_root():
    if os.geteuid() != 0:
        raise Fail(77, "Dafür wird administrativer Zugriff benötigt.", "This needs administrative access.")


def systemctl(*args, check=False):
    p = subprocess.run(["systemctl", *args], capture_output=True, text=True, env=ENV)
    if check and p.returncode:
        raise Fail(1, f"systemctl {' '.join(args)} ist fehlgeschlagen: {p.stderr.strip()}",
                   f"systemctl {' '.join(args)} failed: {p.stderr.strip()}")
    return p


def unit_exists(unit):
    return systemctl("cat", unit).returncode == 0


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def write_text_atomic(path, text, mode):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + "-", suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# status (no root, no command, no lock)

def language(rest):
    if rest and len(rest) == 1 and rest[0].startswith("--lang="):
        rest = ["--lang", rest[0][len("--lang="):]]
    if not rest:
        return "en"
    if len(rest) != 2 or rest[0] != "--lang" or not LANG_RE.fullmatch(rest[1]):
        return None
    return "de" if rest[1].lower()[:2] == "de" else "en"


def read_profile(path):
    """(profile, bad line numbers, the file's time) of profile.conf: no profile when the file is
    missing, None for the profile when it cannot be read."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(1 << 16)
            mtime = os.fstat(f.fileno()).st_mtime
    except FileNotFoundError:
        prof, bad = hslib.parse_profile("")
        return prof, bad, None
    except (OSError, UnicodeDecodeError):
        return None, [], None
    prof, bad = hslib.parse_profile(text)
    return prof, bad, mtime


def do_status(rest, path=None, now=None, profile=None):
    lang = language(rest)
    if lang is None:
        print(USAGE, file=sys.stderr)
        return 64
    path = path or os.path.join(STATE_DIR, "status.json")
    ver = hsstatus.version()
    try:
        with open(path, "rb") as f:
            data = f.read(1 << 20)
    except FileNotFoundError:
        doc = hsstatus.none_document(lang, ver, "missing")
    except OSError as e:
        print(f"cannot read {path}: {e.strerror}", file=sys.stderr)
        return 77
    else:
        try:
            status = json.loads(data.decode("utf-8"))
        except ValueError:
            status = None
        if not isinstance(status, dict) or hsstatus.problems({k: v for k, v in status.items() if k != "i18n"}):
            doc = hsstatus.none_document(lang, ver, "unreadable")
        else:
            doc = hsstatus.localize(status, lang, now)
            doc["version"] = ver
    # The host's target profile, from the file itself (0644): the board sees a change at once, also
    # before the notifier ran. Without a profile (or a file that cannot be read) no field at all.
    prof, _, mtime = read_profile(profile or PROFILE)
    answer = hslib.profile_answer(prof, mtime) if prof else None
    if answer:
        doc["features"] = list(dict.fromkeys(list(doc.get("features") or []) + ["profile"]))
        doc["profile"] = answer
    json.dump(doc, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


# ---------------------------------------------------------------------------
# show (root)

def timer_state():
    if not os.path.isdir("/run/systemd/system") or not unit_exists(TIMER):
        return "missing", None
    active = systemctl("is-active", "--quiet", TIMER).returncode == 0
    nxt = None
    if active:
        v = systemctl("show", TIMER, "-p", "NextElapseUSecRealtime", "--value").stdout.strip()
        m = re.match(r"^\w{3} (\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d) UTC$", v)
        nxt = f"{m.group(1)}T{m.group(2)}Z" if m else None
    return ("active" if active else "inactive"), nxt


def smtp_listening(run=hsproc.run):
    """Whether something accepts connections on port 25 of the loopback: True, False, or
    None when ss could not tell."""
    res = run(["ss", "-Hltn"], 10)
    if not res.ok:
        return None
    for line in res.out.splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        addr, _, port = cols[3].rpartition(":")
        if port != "25":
            continue
        addr = addr.split("%")[0].strip("[]")
        if addr in ("127.0.0.1", "::1", "0.0.0.0", "::", "*") or addr.startswith("127."):
            return True
    return False


def hostname():
    try:
        with open("/etc/hostname", encoding="utf-8") as f:
            name = f.read(256).split()
        if name:
            return name[0]
    except OSError:
        pass
    return socket.gethostname()


def profile_view(path=PROFILE, now=None):
    """The profile as `show` and `profile show` print it: whether one is set, the roles, the extra
    expectations, what is expected (resolved), the skips (each active or ended at the time now),
    set_at, the lines that do not count, and the catalog of roles; unreadable when the file
    cannot be read."""
    now = time.time() if now is None else now
    prof, bad, mtime = read_profile(path)
    catalog = {"base": list(hslib.BASE_EXPECT), "roles": [[r, list(adds)] for r, adds in hslib.ROLES],
               "labels": dict(hslib.ADDON_LABELS)}
    if prof is None:
        return {"set": False, "unreadable": True, "roles": [], "extra": [], "expect": [], "skip": [], "set_at": None,
                "bad_lines": [], "catalog": catalog}
    on = hslib.profile_set(prof)
    return {"set": on, "unreadable": False, "roles": prof["roles"] or [], "extra": prof["extra"],
            "expect": hslib.resolve_expect(prof["roles"], prof["extra"]) if on else [],
            "skip": [dict(s, active=hslib.skip_active(s, now)) for s in prof["skip"]],
            "set_at": prof["set_at"] or (hslib.iso(mtime) if on and mtime is not None else None),
            "bad_lines": bad, "catalog": catalog}


def heartbeat_view(path, state_dir):
    """What the page may know about the watchdog: set up or not, the adapter, whether the address
    is encrypted and whether it is this host itself, the last signal and the last test (their
    answers). Never the address: it is a secret."""
    conf = hsheartbeat.read_conf(path)
    view = {"configured": bool(conf) and not conf.get("invalid"), "invalid": bool(conf and conf.get("invalid")),
            "adapter": None, "encrypted": None, "local": None,
            "last": read_json(os.path.join(state_dir, "heartbeat.json")),
            "test": read_json(os.path.join(state_dir, "test-heartbeat.json"))}
    if view["configured"]:
        view.update(adapter=conf["adapter"], **hsheartbeat.describe(conf["url"]))
    return view


def heartbeat_set_up(path=None):
    conf = hsheartbeat.read_conf(path or HEARTBEAT)
    return bool(conf) and not conf.get("invalid")


def do_show(rest, conf=CONF, token=TOKEN, state_dir=STATE_DIR, run=hsproc.run, profile=None, heartbeat=None):
    if rest:
        raise WRONG
    need_root()
    try:
        with open(conf, encoding="utf-8") as f:
            cfg, bad = hslib.parse_config(f.read(1 << 16))
    except FileNotFoundError:
        cfg, bad = hslib.parse_config("")
    except OSError as e:
        raise Fail(1, f"{conf} ist nicht lesbar: {e.strerror}", f"{conf} is not readable: {e.strerror}")
    try:
        token_set = os.path.getsize(token) > 0
    except OSError:
        token_set = False
    timer, nxt = timer_state()
    last = read_json(os.path.join(state_dir, "last.json")) or {}
    out = {
        "root": True, "config": cfg, "bad_lines": bad, "token": token_set, "timer": timer, "next": nxt,
        "running": systemctl("is-active", SERVICE).stdout.strip() == "activating",
        "host": hostname(), "smtp": {"listening": smtp_listening(run), "sendmail": os.path.exists("/usr/sbin/sendmail")},
        "last": {k: last.get(k) for k in ("baseline_at", "run", "delivery", "access", "seen", "coverage")}
        if last else None,
        # Each channel set up as the notifier knows it, judged against the settings set now (a
        # result for another target does not count, a delivery of 0.2.0 is taken in as the next
        # run takes it): ok, failed or unknown; never the target. Nothing is written here.
        "channels": hschannels.health(hschannels.reconcile(hschannels.records_of(last.get("channels")), cfg,
                                                           last.get("delivery"), time.time())[0], cfg),
        "check": read_json(os.path.join(state_dir, "test-ntfy.json")),
        "test_mail": read_json(os.path.join(state_dir, "test-mail.json")),
        "docs": hslib.NTFY_DOCS,
        "profile": profile_view(profile or PROFILE),
        "heartbeat": heartbeat_view(heartbeat or HEARTBEAT, state_dir),
    }
    if out["last"] and isinstance(out["last"].get("access"), dict):
        out["last"]["access"] = {k: v for k, v in out["last"]["access"].items() if k != "steps"}
    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


# ---------------------------------------------------------------------------
# The settings (root)

class Locked:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise Fail(75, "Die Einstellungen werden gerade geändert.", "The settings are being changed right now.")
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        return False


def change_config(update, conf=CONF, lock=LOCK):
    with Locked(lock):
        os.makedirs(os.path.dirname(conf), mode=0o755, exist_ok=True)
        try:
            with open(conf, encoding="utf-8") as f:
                cfg, _ = hslib.parse_config(f.read(1 << 16))
        except FileNotFoundError:
            cfg, _ = hslib.parse_config("")
        update(cfg)
        write_text_atomic(conf, hslib.format_config(cfg), 0o644)
    return cfg


def do_set(rest, conf=CONF, lock=LOCK):
    if len(rest) == 3 and rest[0] == "ntfy":
        url = hslib.valid_url(rest[1])
        if not url:
            raise Fail(64, "Die Adresse des ntfy-Servers ist ungültig: http:// oder https://, ein Host, auf Wunsch "
                           "Port und Pfad, keine Anmeldedaten, keine Abfrage.",
                       "The address of the ntfy server is not valid: http:// or https://, a host, optionally a "
                       "port and a path, no credentials, no query.")
        if not hslib.valid_topic(rest[2]):
            raise Fail(64, "Das Thema ist ungültig: 1 bis 64 Zeichen aus Buchstaben, Ziffern, - und _.",
                       "The topic is not valid: 1 to 64 letters, digits, - and _.")
        need_root()
        change_config(lambda c: c.update(ntfy_url=url, ntfy_topic=rest[2]), conf, lock)
        say(f"ntfy eingerichtet: {url}, Thema {rest[2]}.", f"ntfy set up: {url}, topic {rest[2]}.")
        return 0
    if len(rest) == 2 and rest[0] == "mail":
        if not hslib.valid_mail(rest[1]):
            raise Fail(64, "Die E-Mail-Adresse ist ungültig.", "The e-mail address is not valid.")
        need_root()
        change_config(lambda c: c.update(mail_to=rest[1]), conf, lock)
        say(f"E-Mail eingerichtet: {rest[1]}.", f"E-mail set up: {rest[1]}.")
        return 0
    if len(rest) == 2 and rest[0] == "language":
        if rest[1] not in hslib.LANGUAGES:
            raise WRONG
        need_root()
        change_config(lambda c: c.update(language=rest[1]), conf, lock)
        say(f"Sprache der Nachrichten: {rest[1]}.", f"Language of the messages: {rest[1]}.")
        return 0
    raise WRONG


def do_unset(rest, conf=CONF, lock=LOCK):
    if rest == ["ntfy"]:
        need_root()
        change_config(lambda c: c.update(ntfy_url=None, ntfy_topic=None), conf, lock)
        say("ntfy entfernt.", "ntfy removed.")
        return 0
    if rest == ["mail"]:
        need_root()
        change_config(lambda c: c.update(mail_to=None), conf, lock)
        say("E-Mail entfernt.", "E-mail removed.")
        return 0
    raise WRONG


def do_token(rest, token=TOKEN, lock=LOCK, stdin=None):
    if rest == ["set"]:
        need_root()
        data = (stdin or sys.stdin.buffer).read(4097)
        try:
            text = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            text = ""
        if len(data) > 4096 or not hslib.valid_token(text):
            raise Fail(64, "Das Token ist ungültig: eine Zeile aus Buchstaben, Ziffern und _ . ~ + / = -, "
                           "höchstens 256 Zeichen.",
                       "The token is not valid: one line of letters, digits and _ . ~ + / = -, at most 256 "
                       "characters.")
        with Locked(lock):
            os.makedirs(os.path.dirname(token), mode=0o755, exist_ok=True)
            write_text_atomic(token, text + "\n", 0o600)
        say("Token gespeichert (nur für root lesbar).", "Token saved (readable by root only).")
        return 0
    if rest == ["remove"]:
        need_root()
        with Locked(lock):
            os.makedirs(os.path.dirname(token), mode=0o755, exist_ok=True)
            write_text_atomic(token, "", 0o600)
        say("Token entfernt.", "Token removed.")
        return 0
    raise WRONG


# ---------------------------------------------------------------------------
# The watchdog (root): heartbeat.conf, a secret like the token

HEARTBEAT_INVALID = {
    "input": ("Die Adresse fehlt oder ist ungültig: eine Zeile auf der Standardeingabe.",
              "The address is missing or not valid: one line on standard input."),
    "form": ("Die Adresse ist ungültig: http:// oder https://, ein Host, auf Wunsch ein Port, ein Pfad; keine "
             "Anmeldedaten, kein Fragment, höchstens 500 Zeichen.",
             "The address is not valid: http:// or https://, a host, optionally a port, a path; no credentials, no "
             "fragment, at most 500 characters."),
    "http-public": ("Unverschlüsseltes http:// ist nur zu einer IP-Adresse eines privaten Netzes erlaubt (10/8, "
                    "172.16/12, 192.168/16, 100.64/10, fc00::/7, Loopback), etwa über WireGuard. Verwenden Sie sonst "
                    "https://.",
                    "Plain http:// is allowed only to an IP address of a private network (10/8, 172.16/12, 192.168/16, "
                    "100.64/10, fc00::/7, loopback), such as one reached through WireGuard. Use https:// otherwise."),
    "kuma-path": ("Eine Push-Adresse von Uptime Kuma endet auf /api/push/TOKEN; die Abfrage ?status=… darf "
                  "folgen, sie wird entfernt.",
                  "An Uptime Kuma push address ends in /api/push/TOKEN; a query ?status=… may follow, it is dropped."),
    "hc-signal": ("Geben Sie die Ping-Adresse selbst an, ohne /fail, /start, /log oder einen Exitcode am Ende.",
                  "Enter the ping address itself, without /fail, /start, /log or an exit status at the end."),
    "query": ("Die Abfrage (?…) ist hier nicht erlaubt: bei Healthchecks keine, bei Uptime Kuma nur status, msg und "
              "ping.",
              "This query (?…) is not allowed: none for Healthchecks, only status, msg and ping for Uptime Kuma."),
}


def do_heartbeat(rest, path=None, lock=LOCK, stdin=None):
    path = path or HEARTBEAT
    if len(rest) == 2 and rest[0] == "set":
        adapter = rest[1]
        if adapter not in hsheartbeat.ADAPTERS:
            raise WRONG
        need_root()
        # The URL is a secret: from standard input only, never an argument, never printed.
        data = (stdin or sys.stdin.buffer).read(hsheartbeat.INPUT_MAX + 1)
        try:
            text = data.decode("utf-8").rstrip("\r\n").strip()
        except UnicodeDecodeError:
            text = ""
        if len(data) > hsheartbeat.INPUT_MAX or not text or "\n" in text or "\r" in text:
            raise Fail(64, *HEARTBEAT_INVALID["input"])
        try:
            url = hsheartbeat.normalize(adapter, text)
        except hsheartbeat.Invalid as e:
            raise Fail(64, *HEARTBEAT_INVALID.get(e.reason, HEARTBEAT_INVALID["form"])) from None
        with Locked(lock):
            os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
            write_text_atomic(path, hsheartbeat.format_conf(adapter, url), 0o600)
        label = hsheartbeat.LABELS[adapter]
        about = hsheartbeat.describe(url)
        say(f"Wächter eingerichtet: {label}. Die Adresse ist nur für root lesbar gespeichert und wird nicht mehr "
            f"angezeigt. Nach jedem Lauf der regelmäßigen Prüfung bekommt er ein Signal.",
            f"Watchdog set up: {label}. The address is stored readable by root only and is not shown again. It gets "
            f"a signal after every run of the regular check.")
        if not about["encrypted"]:
            say("Die Adresse ist unverschlüsselt (http): erlaubt, weil sie in einem privaten Netz liegt.",
                "The address is not encrypted (http): allowed because it is in a private network.")
        if about["local"]:
            say("Der Wächter ist dieser Server selbst: Er kann nicht bemerken, wenn dieser Server ausfällt.",
                "The watchdog is this server itself: it cannot notice when this server fails.")
        return 0
    if rest == ["remove"]:
        need_root()
        if hsheartbeat.read_conf(path) is None:
            say(*UNCHANGED)
            return 0
        with Locked(lock):
            os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
            write_text_atomic(path, "", 0o600)
        say("Wächter entfernt: Er bekommt keine Signale mehr von diesem Server. Pausieren oder löschen Sie die "
            "Prüfung beim Wächter, sonst schlägt er nach seiner Kulanzzeit Alarm.",
            "Watchdog removed: it gets no more signals from this server. Pause or delete the check at the watchdog, "
            "or it raises an alarm once its grace time is over.")
        return 0
    if rest == ["test"]:
        need_root()
        say("Schicke ein Testsignal an den Wächter …", "Sending a test signal to the watchdog …")
        return start_test("heartbeat")
    raise WRONG


# ---------------------------------------------------------------------------
# The target profile (root; `profile show` without)

NO_PROFILE = Fail(1, "Dieser Host hat noch kein Profil: Setzen Sie zuerst die Rollen (profile roles …).",
                  "This host has no profile yet: set the roles first (profile roles …).")
BAD_ITEM = Fail(64, "Erwartet wird ein Add-on (security) oder eine seiner Prüfungen (security/exposure).",
                "Expected: an add-on (security) or one of its checks (security/exposure).")
BAD_SKIP = Fail(64, "Auslassen lässt sich nur eine Prüfung eines Add-ons, als ADD-ON/PRÜFUNG (update-gaps/cve); "
                    "ein Add-on allein lässt sich nicht auslassen.",
                "Only a check of an add-on can be left out, as ADDON/CHECK (update-gaps/cve); an add-on alone cannot "
                "be left out.")
UNCHANGED = ("Nichts geändert.", "Nothing changed.")


def change_profile(update, path=PROFILE, lock=LOCK, now=None):
    """Changes the profile under the lock: update(profile) gets a copy and returns the new profile
    (None: no profile), or raises Fail. Writes profile.conf atomically (0644, line by line, see
    hslib.format_profile) after keeping the file as it was in profile.conf.bak, and only when
    something changes. Returns whether it did."""
    with Locked(lock):
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
        try:
            with open(path, encoding="utf-8") as f:
                old = f.read(1 << 16)
        except FileNotFoundError:
            old = ""
        except (OSError, UnicodeDecodeError) as e:
            why = getattr(e, "strerror", None) or "UTF-8"
            raise Fail(1, f"{path} ist nicht lesbar: {why}", f"{path} is not readable: {why}")
        prof, _ = hslib.parse_profile(old)
        new = update(copy.deepcopy(prof))
        if new is None:
            text = hslib.format_profile(old, None)
            if not old or text == old:
                return False
        else:
            if new == prof:
                return False
            new["set_at"] = hslib.iso(time.time() if now is None else now)
            text = hslib.format_profile(old, new)
        if old:
            write_text_atomic(path + ".bak", old, 0o644)
        write_text_atomic(path, text, 0o644)
    return True


def read_reason(stdin=None):
    """The reason of a skip, from standard input: one line of UTF-8 text, cleaned and cut to 200."""
    data = (stdin or sys.stdin.buffer).read(4097)
    try:
        text = data.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError:
        text = ""
    reason = hslib.clean_reason(text) if "\n" not in text and "\r" not in text else None
    if len(data) > 4096 or not reason:
        raise Fail(64, "Der Grund fehlt oder ist ungültig: eine Zeile Text auf der Standardeingabe; davon werden "
                       "höchstens 200 Zeichen behalten.",
                   "The reason is missing or not valid: one line of text on standard input, of which at most 200 "
                   "characters are kept.")
    return reason


def _known_roles():
    return ", ".join(r for r, _ in hslib.ROLES)


def do_profile(rest, path=PROFILE, lock=LOCK, stdin=None, now=None):
    if rest == ["show"]:
        view = profile_view(path, now)
        if view["unreadable"]:
            raise Fail(1, f"{path} ist nicht lesbar.", f"{path} is not readable.")
        json.dump(view, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    if rest and rest[0] == "roles":
        roles = list(dict.fromkeys(rest[1:]))
        unknown = [r for r in roles if not hslib.ROLE_RE.fullmatch(r) or r not in hslib.ROLE_EXPECT]
        if unknown or len(roles) > hslib.MAX_ROLES:
            named = ", ".join(r for r in unknown if hslib.ROLE_RE.fullmatch(r)) or "?"
            raise Fail(64, f"Unbekannte Rolle: {named}. Bekannt sind: {_known_roles()}.",
                       f"Unknown role: {named}. Known roles: {_known_roles()}.")
        need_root()
        if not change_profile(lambda p: dict(p, roles=roles), path, lock, now):
            say(*UNCHANGED)
            return 0
        if roles:
            say(f"Profil gespeichert: Rollen {', '.join(roles)}.", f"Profile saved: roles {', '.join(roles)}.")
        else:
            say("Profil gespeichert: keine Rolle, erwartet werden die Add-ons jedes Hosts.",
                "Profile saved: no role, the add-ons of every host are expected.")
        return 0

    if len(rest) == 3 and rest[0] == "expect" and rest[1] in ("add", "remove"):
        item = rest[2]
        if not hslib.EXPECT_RE.fullmatch(item):
            raise BAD_ITEM
        need_root()

        def expect(p):
            if not hslib.profile_set(p):
                raise NO_PROFILE
            have = hslib.resolve_expect(p["roles"], p["extra"])
            if rest[1] == "add":
                if item in have:
                    return p
                if len(have) >= hslib.MAX_EXPECT:
                    raise Fail(1, f"Höchstens {hslib.MAX_EXPECT} Add-ons und Prüfungen werden erwartet.",
                               f"At most {hslib.MAX_EXPECT} add-ons and checks are expected.")
                p["extra"].append(item)
            elif item in p["extra"]:
                p["extra"].remove(item)
            elif item in have:
                raise Fail(1, f"{item} erwarten die Rollen oder jeder Host. Eine Prüfung lassen Sie mit "
                              f"„profile skip add“ aus.",
                           f"{item} is expected by the roles or by every host. Leave a check out with "
                           f"\"profile skip add\".")
            return p
        if not change_profile(expect, path, lock, now):
            say(*UNCHANGED)
        elif rest[1] == "add":
            say(f"Zusätzlich erwartet: {item}.", f"Also expected now: {item}.")
        else:
            say(f"Nicht mehr zusätzlich erwartet: {item}.", f"No longer expected in addition: {item}.")
        return 0

    if len(rest) in (3, 4) and rest[:2] == ["skip", "add"]:
        check, until = rest[2], (rest[3] if len(rest) == 4 else None)
        if not hslib.SKIP_RE.fullmatch(check):
            raise BAD_SKIP
        if until is not None:
            day = hslib.parse_date(until)
            today = int((time.time() if now is None else now) // 86400) * 86400
            if day is None or day <= today:
                raise Fail(64, "Das Ende ist ein Tag in der Zukunft, JJJJ-MM-TT. Die Ausnahme endet an diesem Tag.",
                           "The end is a day in the future, YYYY-MM-DD. The skip ends on that day.")
        need_root()
        reason = read_reason(stdin)

        def skip_add(p):
            if not hslib.profile_set(p):
                raise NO_PROFILE
            entry = {"check": check, "until": until, "reason": reason}
            if any(s["check"] == check for s in p["skip"]):
                p["skip"] = [entry if s["check"] == check else s for s in p["skip"]]
            elif len(p["skip"]) >= hslib.MAX_SKIP:
                raise Fail(1, f"Höchstens {hslib.MAX_SKIP} Prüfungen lassen sich auslassen.",
                           f"At most {hslib.MAX_SKIP} checks can be left out.")
            else:
                p["skip"].append(entry)
            return p
        if not change_profile(skip_add, path, lock, now):
            say(*UNCHANGED)
        elif until:
            say(f"Bewusst ausgelassen bis {until}: {check}.", f"Left out on purpose until {until}: {check}.")
        else:
            say(f"Bewusst ausgelassen: {check}.", f"Left out on purpose: {check}.")
        return 0

    if len(rest) == 3 and rest[:2] == ["skip", "remove"]:
        check = rest[2]
        if not hslib.SKIP_RE.fullmatch(check):
            raise BAD_SKIP
        need_root()

        def skip_remove(p):
            if not hslib.profile_set(p):
                raise NO_PROFILE
            p["skip"] = [s for s in p["skip"] if s["check"] != check]
            return p
        if not change_profile(skip_remove, path, lock, now):
            say(*UNCHANGED)
        else:
            say(f"Zählt wieder: {check}.", f"Counts again: {check}.")
        return 0

    if rest == ["remove"]:
        need_root()
        if not change_profile(lambda p: None, path, lock, now):
            say(*UNCHANGED)
        else:
            say("Profil entfernt: Dieser Host hat kein Sollprofil mehr.", "Profile removed: this host has no target "
                "profile any more.")
        return 0
    raise WRONG


# ---------------------------------------------------------------------------
# Cockpit's menu (hsmenu.py): show without root, apply system as root, apply user never as root

MENU_DONE = {"written": ("geändert", "changed"), "removed": ("entfernt, nichts mehr darin", "removed, nothing left in it")}


def do_menu(rest, env=None, stdin=None, backup=None, now=None, euid=None):
    env = os.environ if env is None else env
    if rest == ["show"]:
        json.dump(hsmenu.view(env), sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    if len(rest) != 2 or rest[0] != "apply" or rest[1] not in ("system", "user"):
        raise WRONG
    scope = rest[1]
    if scope == "system":
        need_root()
    elif (os.geteuid() if euid is None else euid) == 0:
        # Else it would change root's own menu, not the one of the user who asked.
        raise Fail(64, "„Nur für mich“ läuft ohne administrativen Zugriff, als der angemeldete Benutzer.",
                   "\"Just for me\" runs without administrative access, as the user who is logged in.")
    data = (stdin or sys.stdin.buffer).read(hsmenu.MAX_PLAN + 1)
    try:
        if len(data) > hsmenu.MAX_PLAN:
            raise ValueError("size")
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Fail(64, f"Der Plan fehlt oder ist kein JSON (höchstens {hsmenu.MAX_PLAN // 1024} KiB auf der "
                       "Standardeingabe).",
                   f"The plan is missing or not JSON (at most {hsmenu.MAX_PLAN // 1024} KiB on standard input).")
    try:
        done = hsmenu.apply_plan(scope, doc, env, write_text_atomic,
                                 (backup or MENU_BACKUP) if scope == "system" else None, now)
    except hsmenu.Refused as r:
        raise Fail(r.code, r.de, r.en)
    changed = [(path, what) for _, path, what in done if what != "unchanged"]
    if not changed:
        say(*UNCHANGED)
    for path, what in changed:
        de, en = MENU_DONE[what]
        say(f"{path}: {de}.", f"{path}: {en}.")
    if changed:
        say("Wirksam, sobald Cockpit neu geladen wird.", "In effect once Cockpit is reloaded.")
    return 0


# ---------------------------------------------------------------------------
# The services (root)

def journal_tail(unit, n=10):
    j = subprocess.run(["journalctl", "-u", unit, "-n", str(n), "--no-pager", "-o", "cat"], capture_output=True,
                       text=True, env=ENV)
    return j.stdout


def start_test(channel, state_dir=None):
    state_dir = state_dir or STATE_DIR
    unit = TEST.format(channel)
    if not unit_exists(unit):
        raise Fail(2, f"Der Dienst {unit} fehlt (Paket cockpit-host-settings).",
                   f"The service {unit} is missing (package cockpit-host-settings).")
    started = time.time()
    p = systemctl("start", unit)
    if p.returncode:
        sys.stdout.write(journal_tail(unit))
        raise Fail(1, f"Die Prüfung ist fehlgeschlagen: journalctl -u {unit}",
                   f"The check failed: journalctl -u {unit}")
    path = os.path.join(state_dir, f"test-{channel}.json")
    doc = read_json(path)
    at = hslib.parse_time((doc or {}).get("at"))
    if not doc or at is None or at < started - 2:
        raise Fail(1, f"Kein Ergebnis in {path}.", f"No result in {path}.")
    print("RESULT " + json.dumps(doc, ensure_ascii=False))
    return 0


def do_check(rest):
    if rest != ["ntfy"]:
        raise WRONG
    need_root()
    say("Prüfe die Verbindung zum ntfy-Server …", "Checking the connection to the ntfy server …")
    return start_test("ntfy")


def do_test(rest):
    if rest != ["mail"]:
        raise WRONG
    need_root()
    say("Schicke eine Testnachricht an das lokale MTA …", "Handing a test message to the local MTA …")
    return start_test("mail")


def do_run(rest):
    if rest:
        raise WRONG
    need_root()
    if not unit_exists(SERVICE):
        raise Fail(2, f"Der Dienst {SERVICE} fehlt (Paket cockpit-host-settings).",
                   f"The service {SERVICE} is missing (package cockpit-host-settings).")
    print(f"+ systemctl start {SERVICE}")
    p = systemctl("start", SERVICE)
    sys.stdout.write(journal_tail(SERVICE, 5))
    if p.returncode:
        raise Fail(1, f"Der Lauf ist fehlgeschlagen: journalctl -u {SERVICE}", f"The run failed: journalctl -u {SERVICE}")
    say("Fertig.", "Done.")
    return 0


def do_timer(verb, rest, conf=CONF, state_dir=STATE_DIR, heartbeat=None):
    if rest != ["timer"]:
        raise WRONG
    need_root()
    if not unit_exists(TIMER):
        raise Fail(2, f"Der Timer {TIMER} fehlt (Paket cockpit-host-settings).",
                   f"The timer {TIMER} is missing (package cockpit-host-settings).")
    watchdog = heartbeat_set_up(heartbeat)
    if verb == "install":
        try:
            with open(conf, encoding="utf-8") as f:
                cfg, _ = hslib.parse_config(f.read(1 << 16))
        except OSError:
            cfg, _ = hslib.parse_config("")
        # A channel or a watchdog: the notifier runs for the watchdog alone as well.
        if not hslib.channels(cfg) and not watchdog:
            raise Fail(1, "Richten Sie zuerst ntfy, E-Mail oder einen Wächter ein.",
                       "Set up ntfy, e-mail or a watchdog first.")
        print(f"+ systemctl enable --now {TIMER}")
        systemctl("enable", "--now", TIMER, check=True)
        print(f"+ systemctl start {SERVICE}")
        systemctl("start", SERVICE, check=True)
        sys.stdout.write(journal_tail(SERVICE, 2))
        say("Regelmäßige Prüfung eingerichtet: alle 5 Minuten.", "Regular check set up: every 5 minutes.")
        if watchdog:
            say("Der Wächter bekommt nach jedem Lauf ein Signal.", "The watchdog gets a signal after every run.")
    else:
        print(f"+ systemctl disable --now {TIMER}")
        systemctl("disable", "--now", TIMER, check=True)
        if os.path.isdir(state_dir):
            at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            write_text_atomic(os.path.join(state_dir, "status.json"),
                              json.dumps(hsstatus.off_file(hsstatus.version(), at), ensure_ascii=False, indent=1) + "\n",
                              0o644)
        say("Regelmäßige Prüfung ausgeschaltet: Von diesem Host kommen keine Nachrichten mehr.",
            "Regular check switched off: this host sends no more messages.")
        if watchdog:
            # Switching off is no failure the notifier could tell: the signals just stop.
            say("Der Wächter bekommt keine Signale mehr und schlägt nach seiner Kulanzzeit Alarm: Pausieren Sie die "
                "Prüfung dort, wenn das Ausschalten geplant ist.",
                "The watchdog gets no more signals and raises an alarm once its grace time is over: pause the check "
                "there if switching off is planned.")
    return 0


def main(argv):
    verb, rest = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if verb == "status":
            return do_status(rest)
        if verb == "show":
            return do_show(rest)
        if verb == "set":
            return do_set(rest)
        if verb == "unset":
            return do_unset(rest)
        if verb == "token":
            return do_token(rest)
        if verb == "check":
            return do_check(rest)
        if verb == "test":
            return do_test(rest)
        if verb == "run":
            return do_run(rest)
        if verb in ("install", "remove"):
            return do_timer(verb, rest)
        if verb == "heartbeat":
            return do_heartbeat(rest)
        if verb == "profile":
            return do_profile(rest)
        if verb == "menu":
            return do_menu(rest)
        raise WRONG
    except Fail as f:
        say(f.de, f.en, sys.stderr)
        return f.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
