# Internals: interfaces, formats, flows

## Paths

| What | Path | Owner, mode |
|---|---|---|
| The page | `/usr/share/cockpit/host-settings/` | root, 0644 |
| The helpers | `/usr/lib/cockpit-host-settings/` (`host-settings.py`, `host-settings-notify.py` 0755; `hslib.py`, `hsnotify.py`, `hsstatus.py`, `hsproc.py` 0644) | root |
| The units | `cockpit-host-settings-notify.service`, `.timer`, `cockpit-host-settings-test@.service` | root |
| The user | `cockpit-host-settings` (`/usr/lib/sysusers.d/cockpit-host-settings.conf`) | |
| The settings | `/etc/cockpit-host-settings/notify.conf` | root, 0644 |
| The ntfy token | `/etc/cockpit-host-settings/ntfy.token` (empty: no token) | root, 0600 |
| The state | `/var/lib/cockpit-host-settings/` (the units' `StateDirectory`) | cockpit-host-settings, 0755 |
| | `last.json`, `test-ntfy.json`, `test-mail.json` | 0600 |
| | `status.json` (status contract) | 0644 |
| The lock of the settings | `/run/cockpit-host-settings.lock` | root, 0600 |

The package's `configure` script creates the user (`systemd-sysusers`, else `useradd`), the
state folder with that owner, `/etc/cockpit-host-settings` and an empty token file: the units
hand the token over with `LoadCredential=`, which fails when the file is missing. Removing the
package removes the state and the units, and keeps the settings, the token and the user.

## Calls from the page

| Call | Superuser | What |
|---|---|---|
| `host-settings.py show` | try | settings (without the token), timer, last run, last checks (below) |
| `host-settings.py status --lang en\|de` | try | the status contract, when `show` says 77 |
| `host-settings.py set ntfy URL TOPIC` | require | the dialog "Set up ntfy" / "Change" |
| `host-settings.py unset ntfy` | require | |
| `host-settings.py set mail ADDRESS`, `unset mail` | require | |
| `host-settings.py set language en\|de` | require | |
| `host-settings.py token set` (token on standard input) | require | |
| `host-settings.py token remove` | require | empties the file |
| `host-settings.py check ntfy` | require | starts `cockpit-host-settings-test@ntfy.service`, waits, prints `RESULT {json}` |
| `host-settings.py test mail` | require | the same with `@mail` |
| `host-settings.py run` | require | starts `cockpit-host-settings-notify.service`, waits |
| `host-settings.py install timer`, `remove timer` | require | needs a channel; `remove` writes the "switched off" status |
| watch `/var/lib/cockpit-host-settings/status.json` | (user) | the page loads again when a run wrote it |

Every argument is checked by the helper (the page checks the same patterns first, for a quick
answer): the address `http(s)://host[:port][/path]` without user, query or fragment, at most 200
characters, a trailing slash dropped; the topic `[A-Za-z0-9_-]{1,64}`; one e-mail address; the
token `[A-Za-z0-9_.~+/=-]{1,256}`. `test/files.test.js` checks that the patterns of the page are
the helper's.

Exit codes of `host-settings.py`: 0 ok, 1 failed, 2 unit missing, 64 wrong call, 75 busy (the
lock), 77 needs root. `HOST_SETTINGS_LANG=de|en` selects the language of its messages.

### `host-settings.py show`

```json
{
  "root": true,
  "config": {"ntfy_url": "https://ntfy.example.com", "ntfy_topic": "cockpit-web1", "mail_to": "ops@example.com", "language": "en"},
  "bad_lines": [],
  "token": true,
  "timer": "active",
  "next": "2026-10-19T19:10:00Z",
  "running": false,
  "host": "web1",
  "smtp": {"listening": true, "sendmail": true},
  "last": {"baseline_at": "…", "run": {…}, "delivery": {…}, "access": {…}, "seen": {…}},
  "check": {…},
  "test_mail": {…},
  "docs": "https://docs.ntfy.sh/install/"
}
```

`smtp.listening` comes from `ss -Hltn`: something listens on port 25 of the loopback (or of every
address); `null` when `ss` failed. `last` is `last.json` without `sent` and without the steps of
the daily check; `check` and `test_mail` are the files of the test unit.

## The settings (`notify.conf`)

```
# Host Settings: where messages about the Azentrius add-ons of this host go.
ntfy_url = https://ntfy.example.com
ntfy_topic = cockpit-web1
mail_to = ops@example.com
language = en
```

`key = value`, `#` comments. A line that is not one of the four keys with a valid value is left
out and named on the page (`bad_lines`); saving from the page writes the file anew without it.
ntfy is set up when both `ntfy_url` and `ntfy_topic` are there, e-mail when `mail_to` is.

## The notifier

`host-settings-notify.py run`, as `cockpit-host-settings-notify.service` every 5 minutes:

1. **Find the add-ons** as Status Board does for other hosts: `/usr/share/cockpit/*/manifest.json`
   with the section `azentrius.status`, folders that are package names only, never this add-on's
   own. Only the first element of the command counts, and only when it matches the contract's
   pattern (`/usr/lib/cockpit-NAME/` and one to three plain segments). Not `/usr/local` or a
   user's `~/.local/share/cockpit`.
2. **Run the status verbs**, `COMMAND status --lang LANG` (the language of the messages), as the
   notifier's user, with 10 seconds each and at most 64 KiB of output. The command runs only when
   it is a regular file of root's that nobody else can change, in folders of root's that nobody
   else can change ("untrusted" otherwise).
3. **Judge the answers** as the board does (`lib/hslib.py`, after `src/lib/status.js` of
   Status Board): schema 1, the add-on is the package, states, items (at most 50, each id once),
   every text cleaned and cut; the state never better than the items; an item past
   `checked_at + stale_after` at least a warning. Exit 77 is "no access" (no level of its own
   here, see step 4; Status Board shows it as a warning, "No access"); every other failure (`rejected`, `untrusted`, `manifest`, `invalid` with its reason,
   `missing`, `failed` with the exit code, `timeout`, `unsupported` for exit 64) makes the add-on
   itself a warning.
4. **Compare** with what was told last (`sent` in `last.json`), per key: `PKG#` for the add-on
   (warning while it cannot be read), `PKG#ID` for each item. Only warning and danger count as
   levels; ok and info are OK. A key whose level differs from the one told is a change:
   `worse` (to warning or danger, or warning to danger), `better` (danger to warning),
   `resolved` (to OK, or the item is gone from a complete answer). What cannot be checked is no
   reason for OK, so these keys keep the level told before and give no message: the items of an
   add-on that cannot be read or that answers exit 77 (and the add-on itself on exit 77), an item
   in state `none` (the add-on cannot judge it, for example Security Monitor's `exposure` when the
   packet filter cannot be read), and an item missing from an answer that left items out (more
   than 50, or broken ones). An add-on that is no longer installed while it had told problems
   gives one change `unchecked` (a warning) and its keys are forgotten. The same change
   (`FROM>TO`) of the same key waits when it was told less than an hour ago; the key keeps its old
   level, so the change is told after the hour if it still holds. OK keys whose last change is
   older than an hour are forgotten.
5. **The first run with a channel** (no `baseline_at` yet) sends a summary of every problem instead
   of changes and takes them as told.
6. **Send** one message for all changes of the run, to every channel set up. Only when at least
   one channel took it, the new `sent` is kept (and `baseline_at` set); otherwise the same
   changes are sent again on the next run.
7. **Once a day**, with ntfy set up: read the topic without a login (below, steps reach, tls,
   anon-read; nothing is published). A new server or topic makes the check due at once.
8. Write `last.json` (0600) and `status.json` (0644).

Exit codes of `host-settings-notify.py`: 0 done (a message that could not be sent is no error),
1 failed (the previous files stay), 64 wrong call, 75 another run or test holds the lock
(`/var/lib/cockpit-host-settings/.lock`).

### Messages

`{title, body, priority, tags}`, from `lib/hsnotify.py` (`message()`), in English or German:

- Title: `HOST: LABEL, TITLE` for one change, `HOST: LABEL, no longer checked` for an add-on
  that is no longer installed, `HOST: N changes` for several, `HOST: messages set up` for the
  summary, `HOST: test message`.
- Body: one block per change, the most severe first:
  `STATE · LABEL · TITLE (VALUE)` and, on the next line, the detail, "out of date since …"
  for an item past its `stale_after`, or why it is OK again ("no longer reported"). For the
  add-on itself: "Status not readable" and the reason. An add-on that is no longer installed:
  `No longer checked · LABEL · the add-on is no longer installed` and how many of the findings
  it reported can no longer be checked. At most 20
  blocks and 3800 bytes (ntfy turns longer messages into attachments), then "and N more"; the
  footer `Host Settings on HOST`.
- Priority (ntfy): 4 when the worst change is a danger, 3 for a warning, 2 when everything is OK
  again; the summary 4 with a danger, else 3. Tags: `rotating_light`, `warning`,
  `white_check_mark`.

The texts of the add-ons come in the language asked for; the labels are the products' names.

### ntfy

`POST URL/` with JSON (publishing as JSON, so titles may hold any character):
`{"topic", "title", "message", "priority", "tags"}`, `Authorization: Bearer TOKEN` when there is
a token. A redirect is an answer and never followed: it could take the token to another host. The
result: `sent` (2xx), `rejected` (401, 403), `rate-limited` (429), `too-large` (413),
`http-NNN`, or without an answer `tls`, `timeout`, `network`.

### E-mail

SMTP to `localhost:25` (`smtplib`, no login, no TLS on the loopback): `From: Host Settings
<cockpit-host-settings@FQDN>`, `To:` the recipient, `Auto-Submitted: auto-generated`, UTF-8 text.
The result: `sent`, `no-mta` (connection refused), `refused` (recipient or 5xx), `smtp`,
`timeout`, `network`. Why not `sendmail`: postfix hands mail over through the setgid program
`postdrop`, and the notifier's sandbox (`NoNewPrivileges=`, which most of its hardening implies)
ignores setgid bits. A mail server with only a `sendmail` command (nullmailer, msmtp, dma) is
therefore not used yet; the page says so.

### "Check connection" (`test ntfy`)

`check_ntfy()` in `lib/hsnotify.py`, as `cockpit-host-settings-test@ntfy.service`:

| Step | Request | Result |
|---|---|---|
| `reach` | `GET URL/v1/health` | ok on any HTTP answer; failed (tls, timeout, network) skips the rest |
| `tls` | (the address) | ok for https, warning for http |
| `publish` | the test message with the token | ok, or failed (`no-token` when refused without one) |
| `anon-read` | `GET URL/TOPIC/json?poll=1&since=1m` without a login | warning on 2xx (readable), ok on 401/403, info otherwise |
| `anon-write` | a message without a login, which is itself the warning | warning on 2xx (writable), ok on 401/403, info otherwise |
| `token` | `GET URL/TOPIC/json?poll=1&since=1m` with the token | ok on 401/403 (write-only), info on 2xx (it may read too) or otherwise |

`access` from the steps: warning when the topic is readable or writable without a login, ok
when both are closed, none when not known. The daily check of the run keeps the write result of
the last full check. `test-ntfy.json`:

```json
{"at": "…", "channel": "ntfy", "target": "https://ntfy.example.com/cockpit-web1", "token": true,
 "steps": [{"id": "reach", "result": "ok", "code": 200, "reason": "answered"}, …],
 "access": {"state": "ok", "anon_read": "ok", "anon_write": "ok"}}
```

The page shows it only while `target` is the server and topic set now. The answers are those of
ntfy 2.11 and 2.32 in the job `e2e`: 403 for what a login does not allow, with or without a token.
`/v1/account` answers 200 but does not list what `ntfy access` grants, so the token's rights are
found out by reading with it.

## `last.json`

```json
{
  "format": 1,
  "baseline_at": "2026-10-19T18:00:00Z",
  "sent": {"cronjobs#failed": {"level": "danger", "at": 1760900000, "last": {"ok>danger": 1760900000}, "label": "Cron Jobs", "title": "Failed runs"}},
  "seen": {"cronjobs": {"label": "Cron Jobs", "kind": "ok", "reason": null, "code": null, "state": "danger", "version": "2.8.1", "summary": "…",
                        "items": [{"id": "failed", "title": "Failed runs", "state": "danger", "value": "2", "detail": null, "link": null, "stale": false}]}},
  "run": {"at": "…", "took_ms": 812, "addons": 4, "bad_lines": []},
  "delivery": {"at": "…", "summary": false, "changes": 1, "title": "…", "results": {"ntfy": {"ok": true, "code": 200, "reason": "sent"}}},
  "access": {"state": "ok", "anon_read": "ok", "anon_write": "ok", "at": "…", "target": "…", "full": true, "steps": […]}
}
```

A file of another `format` is started anew.

## `status.json` and `host-settings.py status` (status contract)

The notifier writes the English document with the German texts under `i18n` (`lib/hsstatus.py`);
the verb answers in the language asked for. Items, each with the link `#/notifications`:

| Item | State |
|---|---|
| `setup` | ok with the channels (value: their number), none without one |
| `delivery` | ok when the last message went out everywhere, warning with the channels that failed and why, none before the first message |
| `access` | only with ntfy: warning when the topic is readable or writable without a login, ok when closed, none when not checked |
| `run` | ok (value: the add-ons read), none without a channel (the notifier tells nobody); `stale_after` 900 seconds, so the verb and the menu icon make it a warning when three runs are missed |

Without `status.json` the verb answers "Not set up yet"; after `remove timer` the file says
"Switched off" (state none). Exit 77 when the file cannot be read. `test/run_test.py` checks every
answer against Status Board's rules (`hslib.validate_status`).

## Page internals

- `plugin/menu-status.js` runs first: Cockpit loads the page hidden after the login
  (`"preload": ["index"]`) for the icon in its menu; it reads only `status.json` and judges it as
  the board does. `settings.js` starts once the page is shown.
- One dialog for every change (`dlg` in `settings.js`): a description, the fields, then the live
  output of the helper and the result. The `RESULT` line of `check` and `test` is data for the
  outcome, not for the log. Password fields are cleared once sent.
- The commands for the ntfy server are built from the topic: user and topic are the same name;
  the reader's pattern replaces the host name in the topic with `*`.
- Tabs: `TABS` has one entry, so the tab bar stays hidden; the location `#/notifications` selects it.

## Tests

- `test/notify_test.py`: every answer in `test/fixtures/board/status` with the expectations of the
  board's own `test/status.test.js`, its manifests; reading an add-on (trust, exit codes); changes,
  flapping, unreadable add-ons, removed ones; the texts of messages; ntfy and SMTP with
  stand-ins; every step of "Check connection" against recorded answers of a closed and an open
  server.
- `test/run_test.py`: whole runs of `host-settings-notify.py` with stand-ins for the add-ons, the
  network, SMTP and the clock; the own status against the board's rules.
- `test/helper_test.py`: the verbs of `host-settings.py`.
- `test/page.test.js`, `test/files.test.js`, `test/i18n.test.js`: the page under jsdom, its files,
  its texts.
- `tools/check-board-fixtures.sh`: `test/fixtures/board` is unchanged from Status Board
  (`tools/board-fixtures.sha256`).
- `tools/preview/build.py` and `shoot.py` (job `test`): the page with `tools/preview/cockpit-stub.js`
  in place of Cockpit and the made-up scenarios `fresh`, `ready`, `open`, `nomta`, the status texts
  from `lib/hsstatus.py` itself. `shoot.py` opens every scenario in Chromium in both themes, both
  languages, with and without administrative access, fails on a JavaScript error or on sideways
  scrolling at a width of 390 pixels (the dialog as well), and writes the screenshots (artifact
  `screenshots`, the pictures in `docs/img`).
- `test/package-install.sh` (job `test` on Debian 13 and Fedora 43, job `install` on Rocky Linux
  and AlmaLinux 9 and 10, each in a throw-away container): the package installed with the
  distribution's own dependencies, its files and modes, the token file, the notifier's user and
  state folder, a run of the notifier as that user, the status verb as nobody, the removal.
- Job `e2e` (`test/e2e/run.sh`): the package with its real units on the runner, ntfy in Docker with
  `auth-default-access: deny-all`, postfix for local mail, a stand-in add-on. It runs the three
  commands of the page on the ntfy server, "Check connection" against a closed and an open topic,
  the first run with the summary through both channels, a change, a run without one, the status
  verb as nobody, switching off, and looks for the token in every file and the journal. It also
  checks that Cockpit lists the page (`cockpit-bridge --packages`).
- Job `e2e-el` (`tools/vm.sh` and `test/e2e/run.sh` with the `.rpm`): the same test in a Rocky
  Linux 9 or 10 VM under KVM on the runner, from a cloud image pinned by name and SHA-256, with
  SELinux enforcing and auditd running (both checked), ntfy in Podman with `podman-docker`, postfix
  from the distribution. `vm.sh avc-mark` marks the audit log after the set-up, `vm.sh avc-check`
  fails on any `AVC`, `USER_AVC`, `SELINUX_ERR` or `USER_SELINUX_ERR` record after the mark (and
  when it cannot read the log or finds no records at all). Before that, `vm.sh avc-selftest`
  causes a denial on purpose (root entering `httpd_t` with `/usr/bin/true`) and fails unless the
  check catches it. Report, journal, audit log and console
  are in the artifacts `e2e-rocky-9` and `e2e-rocky-10`.
