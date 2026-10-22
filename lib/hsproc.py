# SPDX-License-Identifier: Apache-2.0
"""Commands with a time limit, for the helpers of Host Settings.

Lives in /usr/lib/cockpit-host-settings next to the scripts that import it
(root-owned). Standard library only, no side effects on import.

Every command runs in a process group of its own, with a fixed PATH that
leaves /usr/local out, a UTF-8 locale and TZ=UTC. The limit is a deadline for
the whole command; when it passes, the group gets SIGTERM, a moment later
SIGKILL, and the process is always waited for. At most `max_output` bytes of
stdout are kept; `cut` says whether there was more.
"""

import os
import signal
import subprocess

PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
GRACE = 3              # seconds between SIGTERM and SIGKILL
MAX_OUTPUT = 4 << 20   # bytes kept of stdout and of stderr each


class Result:
    """What a command did: exit code (None when it could not start or was
    stopped), its output as text, and whether it ran out of time."""

    def __init__(self, rc, out="", err="", timeout=False, missing=False, cut=False):
        self.rc, self.out, self.err = rc, out, err
        self.timeout, self.missing, self.cut = timeout, missing, cut

    @property
    def ok(self):
        return self.rc == 0 and not self.timeout

    def __repr__(self):
        return f"Result(rc={self.rc!r}, timeout={self.timeout}, missing={self.missing}, cut={self.cut})"


def env():
    return {"PATH": PATH, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "TZ": "UTC", "HOME": "/",
            "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0", "NO_COLOR": "1"}


def _stop(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=GRACE)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def _text(data, limit):
    return (data or b"")[:limit].decode("utf-8", "replace")


def run(argv, limit=60, max_output=MAX_OUTPUT):
    """Run argv (a list, never a shell string) with a deadline of `limit` seconds. Returns a
    Result; never raises for a command that is missing, fails or hangs."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env(), start_new_session=True, close_fds=True)
    except FileNotFoundError:
        return Result(None, missing=True)
    except (OSError, subprocess.SubprocessError) as e:
        return Result(None, err=str(e))
    try:
        out, err = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        _stop(proc)
        try:
            out, err = proc.communicate(timeout=GRACE)
        except (subprocess.TimeoutExpired, ValueError):
            out, err = b"", b""
        return Result(None, _text(out, max_output), _text(err, max_output), timeout=True)
    except BaseException:
        _stop(proc)
        raise
    return Result(proc.returncode, _text(out, max_output), _text(err, max_output), cut=len(out or b"") > max_output)
