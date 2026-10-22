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

Everything else is a wrong call (exit 64). Every argument is checked here.
The changes this helper makes are the settings in /etc/cockpit-host-settings
(notify.conf 0644, ntfy.token 0600, both root's) and the add-on's own timer;
the token never appears in an argument, an output or a log. Sending and
checking run in the add-on's services as their own user, never as root.

Exit codes: 0 ok, 1 failed, 2 unit missing, 64 wrong call, 75 busy, 77 needs
root. HOST_SETTINGS_LANG=de|en selects the language of the messages.
"""

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
import hslib  # noqa: E402
import hsproc  # noqa: E402
import hsstatus  # noqa: E402

CONF_DIR = "/etc/cockpit-host-settings"
CONF = CONF_DIR + "/notify.conf"
TOKEN = CONF_DIR + "/ntfy.token"
STATE_DIR = "/var/lib/cockpit-host-settings"
LOCK = "/run/cockpit-host-settings.lock"
SERVICE = "cockpit-host-settings-notify.service"
TIMER = "cockpit-host-settings-notify.timer"
TEST = "cockpit-host-settings-test@{}.service"
USER = "cockpit-host-settings"
LANG_RE = re.compile(r"[A-Za-z]{2,3}(?:[_-][A-Za-z0-9]{2,8})?")
USAGE = ("usage: host-settings.py status [--lang en|de] | show | set ntfy URL TOPIC | set mail ADDRESS | "
         "set language en|de | unset ntfy|mail | token set|remove | check ntfy | test mail | run | "
         "install timer | remove timer")
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


def do_status(rest, path=None, now=None):
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


def do_show(rest, conf=CONF, token=TOKEN, state_dir=STATE_DIR, run=hsproc.run):
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
        "last": {k: last.get(k) for k in ("baseline_at", "run", "delivery", "access", "seen")} if last else None,
        "check": read_json(os.path.join(state_dir, "test-ntfy.json")),
        "test_mail": read_json(os.path.join(state_dir, "test-mail.json")),
        "docs": hslib.NTFY_DOCS,
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
    sys.stdout.write(journal_tail(SERVICE, 3))
    if p.returncode:
        raise Fail(1, f"Der Lauf ist fehlgeschlagen: journalctl -u {SERVICE}", f"The run failed: journalctl -u {SERVICE}")
    say("Fertig.", "Done.")
    return 0


def do_timer(verb, rest, conf=CONF, state_dir=STATE_DIR):
    if rest != ["timer"]:
        raise WRONG
    need_root()
    if not unit_exists(TIMER):
        raise Fail(2, f"Der Timer {TIMER} fehlt (Paket cockpit-host-settings).",
                   f"The timer {TIMER} is missing (package cockpit-host-settings).")
    if verb == "install":
        try:
            with open(conf, encoding="utf-8") as f:
                cfg, _ = hslib.parse_config(f.read(1 << 16))
        except OSError:
            cfg, _ = hslib.parse_config("")
        if not hslib.channels(cfg):
            raise Fail(1, "Richten Sie zuerst ntfy oder E-Mail ein.", "Set up ntfy or e-mail first.")
        print(f"+ systemctl enable --now {TIMER}")
        systemctl("enable", "--now", TIMER, check=True)
        print(f"+ systemctl start {SERVICE}")
        systemctl("start", SERVICE, check=True)
        sys.stdout.write(journal_tail(SERVICE, 1))
        say("Regelmäßige Prüfung eingerichtet: alle 5 Minuten.", "Regular check set up: every 5 minutes.")
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
        raise WRONG
    except Fail as f:
        say(f.de, f.en, sys.stderr)
        return f.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
