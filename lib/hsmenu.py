# SPDX-License-Identifier: Apache-2.0
"""Host Settings, the Cockpit add-on: Cockpit's menu, read and changed through
manifest overrides.

Lives in /usr/lib/cockpit-host-settings (root-owned). Standard library only,
no side effects on import; nothing here runs a command or uses the network.

Cockpit's bridge (src/cockpit/packages.py, PackagesLoader) finds the pages as
cockpit/*/manifest.json below $XDG_DATA_HOME (~/.local/share), then below
$XDG_DATA_DIRS (/usr/local/share, /usr/share). Before it reads a manifest's
name, priority and conditions, it merges three override files into it (JSON
merge patch, RFC 7396), each later one winning:

  <package folder>/override.json                       shipped with the package
  <$XDG_CONFIG_DIRS or /etc>/cockpit/<folder>.override.json     scope "system"
  <$XDG_CONFIG_HOME or ~/.config>/cockpit/<folder>.override.json  scope "user"

Of several packages with the same name it serves the one with the highest
priority whose conditions hold. view() reads all of this the same way.
apply_plan() changes the editor's part of one scope's override files: for each
entry of dashboard, menu and tools only "order", "label" and null (hidden).
Everything else in a file stays as it is. Unlike the bridge, a manifest's
"requires" is not checked (the bridge leaves out a package that needs a newer
Cockpit than the one installed).
"""

import contextlib
import fcntl
import hashlib
import json
import math
import os
import pwd
import re
import unicodedata
from datetime import datetime, timezone

import hslib

SECTIONS = ("dashboard", "menu", "tools")
MANAGED = ("order", "label")
SUFFIX = ".override.json"
# A package folder the editor writes an override for, and a key of a section.
DIR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
KEY_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
LABEL_MAX = 60
ORDER_MIN, ORDER_MAX = -1000, 10000
MAX_PACKAGES = 200          # manifests read, and packages in one plan
MAX_ENTRIES = 100           # entries of one section in one plan
MAX_FILE = 64 * 1024        # a manifest or an override file
MAX_PLAN = 64 * 1024        # the plan on standard input
KEEP_BACKUPS = 5


class Refused(Exception):
    """A change that is not made; nothing was written."""

    def __init__(self, code, de, en):
        super().__init__(en)
        self.code, self.de, self.en = code, de, en


def _bad_plan(de, en):
    return Refused(64, "Der Plan ist ungültig: " + de, "The plan is not valid: " + en)


# ---------------------------------------------------------------------------
# Where the bridge looks

def _home(env):
    return env.get("HOME") or pwd.getpwuid(os.geteuid()).pw_dir


def data_dirs(env):
    dirs = [env.get("XDG_DATA_HOME") or os.path.join(_home(env), ".local", "share")]
    if env.get("XDG_DATA_DIRS"):
        dirs += [d for d in env["XDG_DATA_DIRS"].split(":") if d]
    else:
        dirs += ["/usr/local/share", "/usr/share"]
    return dirs


def config_dirs(env):
    return [d for d in (env.get("XDG_CONFIG_DIRS") or "/etc").split(":") if d] or ["/etc"]


def user_dir(env):
    return os.path.join(env.get("XDG_CONFIG_HOME") or os.path.join(_home(env), ".config"), "cockpit")


def override_path(scope, folder, env):
    """The file the bridge reads for this scope and package folder (lookup_config: the first
    config dir that has it, else the first config dir)."""
    name = folder + SUFFIX
    if scope == "user":
        return os.path.join(user_dir(env), name)
    dirs = config_dirs(env)
    for d in dirs:
        path = os.path.join(d, "cockpit", name)
        if os.path.exists(path):
            return path
    return os.path.join(dirs[0], "cockpit", name)


# ---------------------------------------------------------------------------
# Reading

def read_file(path):
    """An override or manifest file: {"exists", "sha256", "doc", "error"}. doc is the JSON
    object, or None when the file is missing, unreadable, too large or not an object (the
    bridge then ignores it, with a warning)."""
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_FILE + 1)
    except FileNotFoundError:
        return {"exists": False, "sha256": "", "doc": None, "error": None}
    except OSError:
        return {"exists": True, "sha256": "", "doc": None, "error": "unreadable"}
    out = {"exists": True, "sha256": hashlib.sha256(data).hexdigest(), "doc": None, "error": None}
    if len(data) > MAX_FILE:
        out["error"] = "too-large"
        return out
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        out["error"] = "invalid"
        return out
    if not isinstance(doc, dict):
        out["error"] = "not-an-object"
        return out
    out["doc"] = doc
    return out


def merge_patch(current, patch):
    """JSON merge patch (RFC 7396) as the bridge's json_merge_patch: a new object."""
    result = dict(current)
    for key, value in patch.items():
        if isinstance(value, dict):
            old = current.get(key)
            result[key] = merge_patch(old if isinstance(old, dict) else {}, value)
        elif value is not None:
            result[key] = value
        else:
            result.pop(key, None)
    return result


def _condition(value, exists):
    """Whether one condition of a manifest holds; ValueError for a broken one (the bridge then
    leaves the package out). Unknown predicates hold, as in the bridge."""
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError("condition")
    (name, arg), = value.items()
    if name in ("path-exists", "path-not-exists"):
        if not isinstance(arg, str):
            raise ValueError("condition")
        return exists(arg) == (name == "path-exists")
    if name == "any":
        if not isinstance(arg, list):
            raise ValueError("condition")
        return any([_condition(c, exists) for c in arg])
    return True


def _identity(manifest, folder):
    """name and priority as the bridge reads them, or ValueError."""
    name = manifest.get("name", folder)
    priority = manifest.get("priority", 1)
    conditions = manifest.get("conditions", [])
    if not isinstance(name, str) or not isinstance(priority, int) or isinstance(priority, bool) \
            or not isinstance(conditions, list):
        raise ValueError("manifest")
    return name, priority, conditions


def load(env, exists=os.path.exists):
    """The packages the bridge would serve, in its order: for each one its folder, the
    manifest with the package's own override.json ("base"), the override files of both scopes
    and the result of all of them ("effective"). Also whether more manifests were left out."""
    candidates, cut = [], False
    for datadir in data_dirs(env):
        root = os.path.join(datadir, "cockpit")
        try:
            folders = sorted(os.listdir(root))
        except OSError:
            continue
        for folder in folders:
            path = os.path.join(root, folder)
            raw = read_file(os.path.join(path, "manifest.json"))["doc"]
            if raw is None:
                continue
            if len(candidates) >= MAX_PACKAGES:
                cut = True
                break
            base = merge_patch(raw, read_file(os.path.join(path, "override.json"))["doc"] or {})
            system = read_file(override_path("system", folder, env))
            user = read_file(override_path("user", folder, env))
            effective = merge_patch(merge_patch(base, system["doc"] or {}), user["doc"] or {})
            try:
                name, priority, conditions = _identity(effective, folder)
            except ValueError:
                continue
            candidates.append({"folder": folder, "path": path, "name": name, "priority": priority,
                               "conditions": conditions, "base": base, "system": system, "user": user,
                               "effective": effective})
    served, seen = [], set()
    for c in sorted(candidates, key=lambda c: -c["priority"]):
        if c["name"] in seen:
            continue
        try:
            holds = all([_condition(x, exists) for x in c["conditions"]])
        except ValueError:
            continue
        if holds:
            seen.add(c["name"])
            served.append(c)
    return served, cut


def _section(doc, name):
    v = doc.get(name) if isinstance(doc, dict) else None
    return v if isinstance(v, dict) else {}


def _number(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _text(v):
    return v if isinstance(v, str) else None


def _mark(section, key):
    """What one scope's override does to one entry: None (nothing), {"hidden": true}, or the
    order and label it sets; "other" when it holds more than the editor manages."""
    if key not in section:
        return None
    v = section[key]
    if v is None:
        return {"hidden": True}
    if not isinstance(v, dict):
        return {"other": True}
    out = {k: v[k] for k in MANAGED if k in v}
    if set(v) - set(MANAGED):
        out["other"] = True
    return out


def _foreign(doc):
    """How many things in an override file the editor does not manage."""
    n = 0
    for k, v in doc.items():
        if k not in SECTIONS or not isinstance(v, dict):
            n += 1
            continue
        for entry in v.values():
            if isinstance(entry, dict):
                n += len(set(entry) - set(MANAGED))
            elif entry is not None:
                n += 1
    return n


def _override_files(env):
    """Every override file of both scopes, as (scope, folder, path); for scope system the one
    lookup_config would find."""
    found, taken = [], set()
    for d in config_dirs(env):
        root = os.path.join(d, "cockpit")
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for n in names:
            folder = n[:-len(SUFFIX)]
            if n.endswith(SUFFIX) and folder and folder not in taken:
                taken.add(folder)
                found.append(("system", folder, os.path.join(root, n)))
    root = user_dir(env)
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for n in names:
        if n.endswith(SUFFIX) and n[:-len(SUFFIX)]:
            found.append(("user", n[:-len(SUFFIX)], os.path.join(root, n)))
    return found


def view(env, exists=os.path.exists):
    """The JSON of "host-settings.py menu show"."""
    served, cut = load(env, exists)
    entries, orphans = [], []
    for p in served:
        for section in SECTIONS:
            base = _section(p["base"], section)
            scopes = {s: _section(p[s]["doc"], section) for s in ("system", "user")}
            effective = _section(p["effective"], section)
            for key, info in base.items():
                info = info if isinstance(info, dict) else {}
                now = effective.get(key)
                now = now if isinstance(now, dict) else None
                entries.append({
                    "section": section, "key": key, "package": p["name"], "dir": p["folder"],
                    "editable": bool(DIR_RE.fullmatch(p["folder"])) and bool(KEY_RE.fullmatch(key)),
                    "base": {"label": _text(info.get("label")), "order": _number(info.get("order"))},
                    "system": _mark(scopes["system"], key),
                    "user": _mark(scopes["user"], key),
                    "effective": {"label": _text((now or info).get("label")),
                                  "order": _number((now or info).get("order")), "hidden": now is None},
                })
            for scope, sec in scopes.items():
                for key, v in sec.items():
                    if key not in base:
                        # An object for a key the package does not have makes the bridge serve an
                        # entry without a page; a null for it does nothing.
                        orphans.append({"scope": scope, "dir": p["folder"], "section": section, "key": key,
                                        "phantom": isinstance(v, dict), "value": _mark(sec, key)})
    folders = {p["folder"] for p in served}
    files = []
    for scope, folder, path in _override_files(env):
        f = read_file(path)
        files.append({"scope": scope, "dir": folder, "path": path, "sha256": f["sha256"],
                      "valid": f["doc"] is not None, "error": f["error"],
                      "foreign": _foreign(f["doc"]) if f["doc"] is not None else 0,
                      "served": folder in folders})
    try:
        user = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        user = str(os.geteuid())
    return {"user": user, "uid": os.geteuid(),
            "paths": {"system": os.path.join(config_dirs(env)[0], "cockpit"), "user": user_dir(env)},
            "entries": entries, "orphans": orphans, "files": files, "cut": cut}


# ---------------------------------------------------------------------------
# Changing

def _order(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    if not ORDER_MIN <= v <= ORDER_MAX or abs(v * 1000 - round(v * 1000)) > 1e-6:
        return None
    return int(v) if float(v).is_integer() else round(v, 3)


def _label(v):
    if not isinstance(v, str) or not 1 <= len(v) <= LABEL_MAX or v != v.strip():
        return None
    if unicodedata.normalize("NFC", v) != v or hslib.INVISIBLE_RE.search(v) or re.search("[\t\n\r]", v):
        return None
    return v


def check_plan(doc):
    """The plan of "menu apply": {"base": {folder: sha256 or ""}, "set": {folder: {section:
    {key: null | {"order": n, "label": text}}}}}. "set" is the editor's whole part of this scope
    for the folders named in "base"; a folder in "base" without "set" loses it. Returns
    (base, set) checked and normalized, or raises Refused (64)."""
    if not isinstance(doc, dict) or set(doc) - {"base", "set"} or not isinstance(doc.get("base"), dict) \
            or not isinstance(doc.get("set", {}), dict):
        raise _bad_plan("erwartet wird ein Objekt mit base und set.", "expected an object with base and set.")
    base, wanted = doc["base"], doc.get("set", {})
    if len(base) > MAX_PACKAGES:
        raise _bad_plan(f"höchstens {MAX_PACKAGES} Pakete.", f"at most {MAX_PACKAGES} packages.")
    for folder, sha in base.items():
        if not DIR_RE.fullmatch(folder) or not isinstance(sha, str) or (sha and not SHA_RE.fullmatch(sha)):
            raise _bad_plan("ein Paketordner oder eine Prüfsumme in base.", "a package folder or a checksum in base.")
    out = {}
    for folder, sections in wanted.items():
        if folder not in base or not isinstance(sections, dict) or set(sections) - set(SECTIONS):
            raise _bad_plan("ein Paket in set fehlt in base, oder ein Abschnitt ist unbekannt.",
                            "a package in set is missing from base, or a section is unknown.")
        out[folder] = {}
        for section, entries in sections.items():
            if not isinstance(entries, dict) or len(entries) > MAX_ENTRIES:
                raise _bad_plan(f"{folder}/{section}.", f"{folder}/{section}.")
            checked = {}
            for key, value in entries.items():
                where = f"{folder}/{section}/{key}"
                if not KEY_RE.fullmatch(key):
                    raise _bad_plan(f"der Schlüssel {where}.", f"the key {where}.")
                if value is None:
                    checked[key] = None
                    continue
                if not isinstance(value, dict) or not value or set(value) - set(MANAGED):
                    raise _bad_plan(f"{where}: erlaubt sind nur order und label.",
                                    f"{where}: only order and label are allowed.")
                entry = {}
                if "order" in value:
                    entry["order"] = _order(value["order"])
                    if entry["order"] is None:
                        raise _bad_plan(f"{where}: order ist eine Zahl von {ORDER_MIN} bis {ORDER_MAX} mit "
                                        "höchstens drei Nachkommastellen.",
                                        f"{where}: order is a number from {ORDER_MIN} to {ORDER_MAX} with at most "
                                        "three decimals.")
                if "label" in value:
                    entry["label"] = _label(value["label"])
                    if entry["label"] is None:
                        raise _bad_plan(f"{where}: label ist ein Text von 1 bis {LABEL_MAX} Zeichen ohne "
                                        "Steuerzeichen und ohne Leerzeichen am Rand.",
                                        f"{where}: label is a text of 1 to {LABEL_MAX} characters without control "
                                        "characters and without spaces at either end.")
                checked[key] = entry
            if checked:
                out[folder][section] = checked
    return dict(base), out


def without_editor_part(doc):
    """An override file without what the editor manages: every null and every order and label
    in dashboard, menu and tools. Everything else stays, in its order."""
    out = {}
    for k, v in doc.items():
        if k not in SECTIONS or not isinstance(v, dict):
            out[k] = v
            continue
        sec = {}
        for key, entry in v.items():
            if entry is None:
                continue
            if isinstance(entry, dict):
                rest = {a: b for a, b in entry.items() if a not in MANAGED}
                if rest:
                    sec[key] = rest
            else:
                sec[key] = entry
        if sec:
            out[k] = sec
    return out


def with_editor_part(current, wanted, name):
    """current with the editor's part replaced by wanted (one package's part of a checked plan)."""
    doc = without_editor_part(current)
    for section in SECTIONS:
        entries = wanted.get(section)
        if not entries:
            continue
        sec = doc.get(section)
        if sec is not None and not isinstance(sec, dict):
            raise Refused(1, f"{name}: {section} ist dort kein Objekt; das lässt der Editor stehen.",
                          f"{name}: {section} is not an object there; the editor leaves that alone.")
        sec = dict(sec or {})
        for key, value in entries.items():
            old = sec.get(key)
            if old is not None and (value is None or not isinstance(old, dict)):
                raise Refused(1, f"{name}: {section}/{key} hat dort weitere Angaben; Ausblenden würde sie löschen.",
                              f"{name}: {section}/{key} holds more there; hiding it would delete that.")
            sec[key] = None if value is None else dict(old or {}, **value)
        doc[section] = sec
    return doc


def _dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=4) + "\n"


@contextlib.contextmanager
def _locked(directory):
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused(75, "Das Menü wird gerade geändert.", "The menu is being changed right now.")
        yield
    finally:
        os.close(fd)


def keep_backup(root, contents, now, keep=KEEP_BACKUPS):
    """The previous version of each changed file, {folder: bytes}, in root/<UTC time>/; only the
    newest `keep` of these folders stay."""
    if not contents:
        return None
    os.makedirs(root, mode=0o700, exist_ok=True)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target, n = os.path.join(root, stamp), 1
    while os.path.exists(target):
        target, n = os.path.join(root, f"{stamp}-{n}"), n + 1
    os.mkdir(target, 0o700)
    for folder, data in sorted(contents.items()):
        fd = os.open(os.path.join(target, folder + SUFFIX), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    for old in sorted(d for d in os.listdir(root) if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(-[0-9]+)?", d))[:-keep]:
        for name in os.listdir(os.path.join(root, old)):
            os.unlink(os.path.join(root, old, name))
        os.rmdir(os.path.join(root, old))
    return target


def apply_plan(scope, doc, env, write, backup_root=None, now=None):
    """Writes one scope's override files from a plan (check_plan). Every file must still be the
    one the plan was made from (its sha256 in base), else nothing is written (75). Returns
    [(folder, path, "written" | "removed" | "unchanged")]. write(path, text, mode) writes atomically;
    with backup_root the previous versions are kept there first."""
    base, wanted = check_plan(doc)
    paths = {folder: override_path(scope, folder, env) for folder in base}
    directories = sorted({os.path.dirname(p) for p in paths.values()})
    for d in directories:
        os.makedirs(d, mode=0o755 if scope == "system" else 0o700, exist_ok=True)
    with contextlib.ExitStack() as stack:
        for d in directories:
            stack.enter_context(_locked(d))
        plan, previous = [], {}
        for folder in sorted(base):
            path = paths[folder]
            cur = read_file(path)
            if cur["error"] == "unreadable":
                raise Refused(1, f"{path} ist nicht lesbar.", f"{path} is not readable.")
            if cur["sha256"] != base[folder]:
                raise Refused(75, f"{path} wurde inzwischen geändert. Laden Sie das Menü neu.",
                              f"{path} has changed meanwhile. Reload the menu.")
            if cur["exists"] and cur["doc"] is None:
                raise Refused(1, f"{path} ist kein gültiges JSON-Objekt ({cur['error']}); der Editor ändert es "
                                 "nicht.",
                              f"{path} is not a valid JSON object ({cur['error']}); the editor does not change it.")
            new = with_editor_part(cur["doc"] or {}, wanted.get(folder, {}), folder)
            if cur["exists"] and new == cur["doc"] or not cur["exists"] and not new:
                plan.append((folder, path, None, "unchanged"))
                continue
            if cur["exists"]:
                with open(path, "rb") as f:
                    previous[folder] = f.read(MAX_FILE + 1)
            plan.append((folder, path, new, "written" if new else "removed"))
        if backup_root and previous:
            keep_backup(backup_root, previous, now if now is not None else datetime.now(timezone.utc).timestamp())
        for folder, path, new, what in plan:
            if what == "written":
                write(path, _dump(new), 0o644)
            elif what == "removed":
                os.unlink(path)
    return [(folder, path, what) for folder, path, _, what in plan]
