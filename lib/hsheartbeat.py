# SPDX-License-Identifier: Apache-2.0
"""The heartbeat of Host Settings: a signal to an external watchdog after every
run of the notifier, so that someone notices when the notifier stops or fails.

Two adapters, chosen explicitly (nothing is guessed from the address):

  healthchecks   success: the ping URL; failure: PING_URL/fail; test: PING_URL/log
                 (Healthchecks' pinging API, hc-ping.com or a server of its own)
  uptime-kuma    a push monitor: PUSH_URL?status=up (success, test) or
                 PUSH_URL?status=down (failure); never /fail

The whole ping or push URL is a secret: whoever knows it can report a run or
silence the alarm. It lives in /etc/cockpit-host-settings/heartbeat.conf (root,
0600), reaches the helper on standard input and the notifier as the systemd
credential heartbeat.conf (LoadCredential=), and never appears in an output, a
log, a state file, the status or an argument. Nothing here prints it; results
carry only reason words.

Plain http:// is allowed only to an IP address of a private network (a watchdog
reached through WireGuard, say), so that the URL never crosses the internet in
the clear; https:// is checked as usual (certificate and host name), and a
redirect is an answer, never followed. Each signal gets at most ATTEMPTS tries
of REQUEST_TIMEOUT seconds within BUDGET seconds. Formats and the rules for the
result of a run: docs/INTERNALS.md, "The watchdog".

Lives in /usr/lib/cockpit-host-settings (root-owned). Standard library only, no
side effects on import; only send() goes to the network.
"""

import ipaddress
import json
import re
import time
from urllib.parse import parse_qsl, quote, urlencode

ADAPTERS = ("healthchecks", "uptime-kuma")
LABELS = {"healthchecks": "Healthchecks", "uptime-kuma": "Uptime Kuma"}
SIGNALS = ("success", "failure", "test")
URL_MAX = 500
INPUT_MAX = 4096
REQUEST_TIMEOUT = 10        # seconds per try
ATTEMPTS = 3                # tries per signal at most
BACKOFF = (2, 5)            # seconds before the second and the third try
BUDGET = 40                 # seconds for one signal, every try and pause included
RETRY = ("timeout", "network")   # and HTTP 5xx: worth another try; nothing else is
# Where plain http:// may go: private networks only (RFC 1918, the shared address space that
# WireGuard and Tailscale set-ups use, unique local IPv6, loopback). A host name never: it could
# resolve to anything.
PRIVATE = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
                                                  "127.0.0.0/8", "::1/128", "fc00::/7"))
LOOPBACK = tuple(ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128"))
URL_RE = re.compile(r"(https?)://([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]{2,45}\])"
                    r"(?::([0-9]{1,5}))?((?:/[A-Za-z0-9._~%+-]+)+)/?(?:\?([A-Za-z0-9._~%&=+-]*))?")
KUMA_PATH_RE = re.compile(r"(?:/[A-Za-z0-9._~%+-]+)*/api/push/[A-Za-z0-9_-]{1,64}")
KUMA_QUERY = ("status", "msg", "ping")      # what Uptime Kuma's page shows after the push URL; dropped
HC_SIGNAL_RE = re.compile(r"/(?:fail|start|log|[0-9]{1,3})")
WHY = {
    None: "run done",
    "config": "the settings could not be read",
    "collect": "reading the add-ons failed",
    "delivery": "a message could not be sent",
    "channel": "a channel has not worked since it failed",
    "state": "the state could not be saved",
    "error": "the run failed",
}


class Invalid(Exception):
    """An address that is refused; reason is one of: form, http-public, query, kuma-path,
    hc-signal, adapter."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _ip(host):
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def private(host):
    """Whether host is an IP address of a private network (where plain http may go)."""
    ip = _ip(host)
    return ip is not None and any(ip in n for n in PRIVATE if n.version == ip.version)


def loopback(host):
    """Whether host is this machine itself: a watchdog there cannot see it fail."""
    if host.lower() == "localhost":
        return True
    ip = _ip(host)
    return ip is not None and any(ip in n for n in LOOPBACK if n.version == ip.version)


def split(url):
    """(scheme, host, port or None, path) of a valid address, else None."""
    m = URL_RE.fullmatch(url) if isinstance(url, str) and len(url) <= URL_MAX else None
    if not m or (m.group(3) and not 0 < int(m.group(3)) < 65536):
        return None
    return m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)


def normalize(adapter, url):
    """The address as it is stored: scheme://host[:port]/path, without a trailing slash or a
    query. Raises Invalid. Uptime Kuma's page shows the push URL with ?status=up&msg=OK&ping=,
    which is taken and dropped; Healthchecks' ping URL has no query."""
    if adapter not in ADAPTERS:
        raise Invalid("adapter")
    parts = split(url)
    if parts is None:
        raise Invalid("form")
    scheme, host, port, path, query = parts
    if scheme == "http" and not private(host):
        raise Invalid("http-public")
    if adapter == "uptime-kuma":
        if not KUMA_PATH_RE.fullmatch(path):
            raise Invalid("kuma-path")
        if query and any(k not in KUMA_QUERY for k, _ in parse_qsl(query, keep_blank_values=True)):
            raise Invalid("query")
    else:
        if query:
            raise Invalid("query")
        if HC_SIGNAL_RE.fullmatch(path[path.rfind("/"):]) and path.count("/") > 1:
            raise Invalid("hc-signal")
    return f"{scheme}://{host}" + (f":{port}" if port else "") + path


def describe(url):
    """What the page may know about a stored address: {encrypted, local}; never the address."""
    parts = split(url)
    if parts is None:
        return {"encrypted": None, "local": None}
    return {"encrypted": parts[0] == "https", "local": loopback(parts[1])}


# ---------------------------------------------------------------------------
# heartbeat.conf: two lines, root, 0600

def format_conf(adapter, url):
    return (f"# Host Settings: the watchdog that gets a signal after every run. Secret: root only (0600).\n"
            f"adapter = {adapter}\nurl = {url}\n")


def parse_conf(text):
    """{adapter, url} of heartbeat.conf; None when it sets up nothing (empty: the default);
    {"invalid": True} when it says something that does not hold (a broken hand edit)."""
    values = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key.strip() not in ("adapter", "url"):
            return {"invalid": True}
        values[key.strip()] = value.strip()
    if not values:
        return None
    try:
        url = normalize(values.get("adapter"), values.get("url"))
    except Invalid:
        return {"invalid": True}
    return {"adapter": values["adapter"], "url": url}


def read_conf(path):
    """parse_conf() of the file at path; None when it is missing, cannot be read or is empty."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(INPUT_MAX + 1)
    except (OSError, UnicodeDecodeError):
        return None
    if len(text) > INPUT_MAX:
        return {"invalid": True}
    return parse_conf(text)


# ---------------------------------------------------------------------------
# The signal

def why_text(why, detail=None):
    """The short text that goes with a signal (Healthchecks' log, Uptime Kuma's message): our
    own words and reason words, never what an add-on said, never an address."""
    text = "Host Settings: " + WHY.get(why, WHY["error"])
    if detail:
        text += " (" + ", ".join(detail) + ")"
    return text[:200]


def request_of(adapter, url, signal, why=None, detail=None):
    """(method, url, body, headers) of a signal."""
    if adapter == "uptime-kuma":
        # GET: Uptime Kuma 1.x takes only GET on the push URL, 2.x any method.
        status = "down" if signal == "failure" else "up"
        msg = "Host Settings: test signal" if signal == "test" else why_text(why if signal == "failure" else None, detail)
        return "GET", url + "?" + urlencode({"status": status, "msg": msg}, quote_via=quote), None, {}
    target = url + {"success": "", "failure": "/fail", "test": "/log"}[signal]
    body = "Host Settings: test signal" if signal == "test" else why_text(why if signal == "failure" else None, detail)
    return "POST", target, body.encode("utf-8"), {"Content-Type": "text/plain; charset=utf-8"}


def judge(adapter, code, body):
    """(ok, reason) of an answer. A 2xx alone is not enough: Healthchecks answers an unknown
    check or a rate limit with 200 and says so in the body, Uptime Kuma answers {"ok": false}."""
    if 300 <= code < 400:
        return False, "redirect"
    text = (body or b"")[:4096].decode("utf-8", "replace").strip()
    if adapter == "healthchecks":
        if code == 200:
            if text == "OK":
                return True, "accepted"
            if text == "OK (not found)":
                return False, "not-found"
            if text == "OK (rate limited)":
                return False, "rate-limited"
            return False, "unexpected"
        if code == 201:
            return True, "accepted"
        return False, {400: "invalid-url", 404: "not-found", 409: "ambiguous", 429: "rate-limited"}.get(code, f"http-{code}")
    try:
        doc = json.loads(text)
    except ValueError:
        doc = None
    ok = doc.get("ok") if isinstance(doc, dict) else None
    if 200 <= code < 300:
        if ok is True:
            return True, "accepted"
        return False, "refused" if ok is False else "unexpected"
    if code == 404 and ok is False:
        return False, "not-found"
    return False, f"http-{code}"


def send(adapter, url, signal, why=None, detail=None, request=None, sleep=time.sleep, clock=time.monotonic):
    """{ok, code, reason, attempts} of one signal: success, failure or test. A try without an
    answer (timeout, network) or with HTTP 5xx is tried again, at most ATTEMPTS times and never
    beyond BUDGET seconds; every other answer is final. request is hsnotify.http (TLS checked,
    redirects never followed) unless a test hands in a stand-in."""
    # Imported here: the root helper uses this module for the settings and never sends.
    import hsnotify
    NetError = hsnotify.NetError
    request = request or hsnotify.http
    method, target, body, headers = request_of(adapter, url, signal, why, detail)
    start = clock()
    attempts = 0
    timeout = REQUEST_TIMEOUT
    while True:
        attempts += 1
        try:
            code, data = request(method, target, body, headers, timeout=timeout, limit=4096)
        except NetError as e:
            ok, reason, code = False, e.reason, None
        except Exception:  # noqa: BLE001 - whatever else the request raised: no answer, and no text of it
            ok, reason, code = False, "network", None
        else:
            ok, reason = judge(adapter, code, data)
        result = {"ok": ok, "code": code, "reason": reason, "attempts": attempts}
        again = not ok and (reason in RETRY or (code is not None and 500 <= code < 600))
        if not again or attempts >= ATTEMPTS:
            return result
        pause = BACKOFF[attempts - 1]
        left = BUDGET - (clock() - start) - pause
        if left < 1:
            return result
        timeout = min(REQUEST_TIMEOUT, left)
        sleep(pause)
