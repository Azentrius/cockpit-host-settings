# Security Policy

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

Report them privately through GitHub instead:
[Report a vulnerability](https://github.com/Azentrius/cockpit-host-settings/security/advisories/new)
(tab **Security → Report a vulnerability** in this repository).
If you cannot use GitHub, write to **support@azentrius.de**.

Please include:

- what is affected (page, helper script, systemd unit or package; version or commit),
- steps to reproduce,
- the impact you expect.

Do not send real tokens, passwords, topics or other private data from your servers. Redact logs
and command output before you attach them (host names, IP addresses, e-mail addresses, ntfy
tokens and topics); a made-up example is just as good.

We aim to acknowledge reports within 7 days. This is a small project, so a fix may take longer; we
will keep you informed and credit you in the advisory unless you prefer to stay anonymous. Please keep
the details private until a fixed release is out.

## Supported versions

| Version | Security fixes |
|---|---|
| Newest release | yes |
| Older releases | no |

The add-on is in its 0.x line: only the newest release is supported. Security fixes go into a new
patch or minor release of the latest release line; older releases get no backports. The `main` branch
is a development state, not a supported installation.

## How fixes are announced

A fixed vulnerability is announced with:

- a GitHub security advisory in this repository (tab **Security**), with a CVE ID where one is assigned,
- an entry in `CHANGELOG.md` marked as a security fix,
- the release notes of the fixed release, which come from `CHANGELOG.md`.

## Especially relevant

The page's helper in `lib/` runs as root from Cockpit and writes the settings and the ntfy token;
the notifier runs as its own user with network access and holds the token. Reports are
particularly welcome for:

- ways to make the page run commands or arguments other than the fixed verbs of `host-settings.py`,
- anything that makes the helpers run or import code from a directory others can write,
- the notifier running a status command that is not root's, or one outside `/usr/lib/cockpit-NAME/`,
- data from the add-ons (labels, titles, details) that reaches the page as markup or script (the
  Cockpit session can act as root), or that lets a message say more than the add-on reported,
- the ntfy token reaching anything but `/etc/cockpit-host-settings/ntfy.token`, systemd's
  credential and the request header (an argument, the page, the state files, the journal),
- the token or a message going to a host other than the one set up (redirects, proxies),
- a change of a finding that the notifier stays silent about, or "Check connection" calling an
  open topic closed,
- the package `cockpit-host-settings` (its install and removal scripts, its user) or `make install`
  overwriting or exposing files it should not touch.
