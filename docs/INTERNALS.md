# Internals: interfaces, formats, flows

## Paths

| What | Path | Owner, mode |
|---|---|---|
| The page | `/usr/share/cockpit/host-settings/` | root, 0644 |
| The helpers | `/usr/lib/cockpit-host-settings/` (`host-settings.py`, `host-settings-notify.py` 0755; `hslib.py`, `hsnotify.py`, `hsstatus.py`, `hsproc.py`, `hsheartbeat.py`, `hschannels.py`, `hsmenu.py` 0644) | root |
| The units | `cockpit-host-settings-notify.service`, `.timer`, `cockpit-host-settings-test@.service` | root |
| The user | `cockpit-host-settings` (`/usr/lib/sysusers.d/cockpit-host-settings.conf`) | |
| The settings | `/etc/cockpit-host-settings/notify.conf` | root, 0644 |
| The target profile | `/etc/cockpit-host-settings/profile.conf`, the file before the last change in `profile.conf.bak` | root, 0644 |
| The ntfy token | `/etc/cockpit-host-settings/ntfy.token` (empty: no token) | root, 0600 |
| The watchdog | `/etc/cockpit-host-settings/heartbeat.conf` (empty: no watchdog; adapter and URL, a secret) | root, 0600 |
| The state | `/var/lib/cockpit-host-settings/` (the units' `StateDirectory`) | cockpit-host-settings, 0755 |
| | `last.json`, `heartbeat.json`, `test-ntfy.json`, `test-mail.json`, `test-heartbeat.json` | 0600 |
| | `status.json` (status contract) | 0644 |
| The lock of the settings | `/run/cockpit-host-settings.lock` | root, 0600 |
| Cockpit's menu for all users | `/etc/cockpit/<folder>.override.json` (Cockpit's own file; the helper changes only its part, below "Cockpit's menu") | root, 0644 |
| | the version before each of the last five changes: `/var/lib/cockpit-host-settings/backup/menu/<UTC time>/` | root, 0700 and 0600 |
| Cockpit's menu just for one user | `~/.config/cockpit/<folder>.override.json` of that user | the user, 0644 |

The package's `configure` script creates the user (`systemd-sysusers`, else `useradd`), the
state folder with that owner, `/etc/cockpit-host-settings`, an empty token file and an empty
watchdog file: the units hand both over with `LoadCredential=`, which fails when a file is
missing. Removing the package removes the state (with the menu's backups in `backup/`) and the
units, and keeps the settings, the token, the watchdog's file and the user. Cockpit's override
files in `/etc/cockpit` and `~/.config/cockpit` stay as they are: they are Cockpit's own, and the
menu set there is the administrator's choice (reset it in the Cockpit tab before uninstalling to
give Cockpit its own menu back). An update keeps the backups.

## Calls from the page

| Call | Superuser | What |
|---|---|---|
| `host-settings.py show` | try | settings (without the token), timer, last run, last checks, the target profile (below) |
| `host-settings.py status --lang en\|de` | try | the status contract, when `show` says 77 |
| `host-settings.py set ntfy URL TOPIC` | require | the dialog "Set up ntfy" / "Change" |
| `host-settings.py unset ntfy` | require | |
| `host-settings.py set mail ADDRESS`, `unset mail` | require | |
| `host-settings.py set language en\|de` | require | |
| `host-settings.py token set` (token on standard input) | require | |
| `host-settings.py token remove` | require | empties the file |
| `host-settings.py check ntfy` | require | starts `cockpit-host-settings-test@ntfy.service`, waits, prints `RESULT {json}`; the result counts for the channel's health (below, "Channel health") |
| `host-settings.py test mail` | require | the same with `@mail` |
| `host-settings.py run` | require | starts `cockpit-host-settings-notify.service`, waits |
| `host-settings.py install timer`, `remove timer` | require | needs a channel or a watchdog; `remove` writes the "switched off" status and, with a watchdog, says that it will raise the alarm |
| `host-settings.py heartbeat set healthchecks\|uptime-kuma` (URL on standard input) | require | the dialog "Set up a watchdog" / "Change" |
| `host-settings.py heartbeat remove` | require | empties `heartbeat.conf`; says to pause the check at the watchdog |
| `host-settings.py heartbeat test` | require | starts `cockpit-host-settings-test@heartbeat.service`, waits, prints `RESULT {json}` |
| `host-settings.py profile roles [ROLE ...]` | require | the dialog "Set up a profile" / "Change roles" |
| `host-settings.py profile expect add\|remove ITEM` | require | "Expect more", and "Remove" in a row of what is expected |
| `host-settings.py profile skip add ADDON/CHECK [YYYY-MM-DD]` (reason on standard input) | require | "Leave a check out" |
| `host-settings.py profile skip remove ADDON/CHECK` | require | "Remove" in a row of the checks left out |
| `host-settings.py profile remove` | require | "Remove the profile" |
| `host-settings.py menu show` | none | the Cockpit tab, once it is opened, and again after saving and with "Refresh"; as the user who is logged in |
| `host-settings.py menu apply system` (plan on standard input) | require | "Save changes" for all users, through the dialog |
| `host-settings.py menu apply user` (plan on standard input) | none | "Save changes" just for me, through the dialog, as the user |
| watch `/var/lib/cockpit-host-settings/status.json` | (user) | the page loads again when a run wrote it |

Every argument is checked by the helper (the page checks the same patterns first, for a quick
answer): the address `http(s)://host[:port][/path]` without user, query or fragment, at most 200
characters, a trailing slash dropped; the topic `[A-Za-z0-9_-]{1,64}`; one e-mail address; the
token `[A-Za-z0-9_.~+/=-]{1,256}`; a role `[a-z0-9-]{1,40}` that is in the catalog; an add-on or a
check `[a-z0-9][a-z0-9-]{0,40}(/[a-z0-9-]{1,40})?` (the contract's pattern for `expect`); a check
left out `[a-z0-9][a-z0-9-]{0,40}/[a-z0-9-]{1,40}` (never an add-on alone); a day `YYYY-MM-DD`
after today; the watchdog's URL as "The watchdog" below says (the page only looks for
`http(s)://host/path` first). `test/files.test.js` checks that the patterns of the page
are the helper's.

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
  "last": {"baseline_at": "…", "run": {…}, "delivery": {…}, "access": {…}, "seen": {…}, "coverage": […]},
  "channels": {"ntfy": {"state": "ok", "at": "…", "via": "test", "recovered": null},
               "mail": {"state": "failed", "since": "…", "at": "…", "via": "run", "reason": "no-mta"}},
  "check": {…},
  "test_mail": {…},
  "docs": "https://docs.ntfy.sh/install/",
  "heartbeat": {"configured": true, "invalid": false, "adapter": "uptime-kuma", "encrypted": false, "local": false,
                "last": {…}, "test": {…}},
  "profile": {"set": true, "unreadable": false, "roles": ["web"], "extra": ["security/exposure"],
              "expect": ["host-settings", "update-gaps", "security", "cronjobs", "image-updates", "security/exposure"],
              "skip": [{"check": "update-gaps/cve", "until": "2026-12-31", "reason": "network data off on purpose", "active": true}],
              "set_at": "2026-10-25T10:00:00Z", "bad_lines": [],
              "catalog": {"base": […], "roles": [["web", ["image-updates"]], …], "labels": {"security": "Security Monitor", …}}}
}
```

`smtp.listening` comes from `ss -Hltn`: something listens on port 25 of the loopback (or of every
address); `null` when `ss` failed. `last` is `last.json` without `sent`, `channels` and the steps
of the daily check; `channels` is the health of each channel set up, judged against the settings
set now (`hschannels.health()`, below, "Channel health"; never a target), with a delivery of 0.2.0
taken in as the next run takes it and nothing written; `check` and `test_mail` are the files of
the test unit. `heartbeat` says whether a watchdog is set up (`invalid`: the file says something that does not
hold), its adapter, whether its address is encrypted and whether it is this host itself, and
`heartbeat.json` and `test-heartbeat.json` (below); never the address. `profile` is what
`profile show` prints (below): `expect` resolved from the roles, each skip `active` or ended now,
`set_at` from the file (else its time), the lines that do not count, and the catalog of roles,
which the page shows instead of a copy of its own.

## The target profile (`profile.conf`)

What this host is expected to check, kept on the host itself: its roles, and with them the add-ons
it should have, extra add-ons or checks, and checks left out on purpose. The status verb sends it as `profile`, the page shows and changes it,
the notifier tells when an expected check is no longer possible.

```
# Host Settings: the target profile of this host (roles, expected add-ons and checks, checks left
# out on purpose). Written by the page (host-settings.py profile ...); comments and other lines stay.
roles = web
expect = security/exposure
skip = update-gaps/cve until 2026-12-31: network data off on purpose
skip = security/fail2ban: CrowdSec instead
set_at = 2026-10-25T10:00:00Z
```

- `key = value`, `#` comments, as `notify.conf`. A profile is set when there is a valid `roles`
  line; it may name no role (`roles =`), then the add-ons of every host are expected. Without
  one, `expect` and `skip` lines do not count.
- `roles`: role names, separated by commas or spaces; a line with a role that is not in the
  catalog does not count at all. Of `roles` and `set_at` the last valid line counts.
- `expect`: one add-on (`image-updates`) or one check of an add-on (`security/exposure`) per line,
  beyond the roles. As Status Board reads it, a check makes its add-on expected (and with it every
  check of the add-on), and the add-on must report exactly that check: one it does not report is
  a gap of its own (below, "Coverage").
- `skip`: `ADDON/CHECK[ until YYYY-MM-DD]: REASON`, one check of an add-on per line (a later line
  for the same check counts instead); the reason is one line of text, cleaned and cut to 200
  characters, and required. A skip ends on its day at 00:00 UTC, as an acceptance does. An add-on
  alone cannot be left out: Status Board lets only a skip that names a check take effect, so the
  helper refuses one that names an add-on alone, and such a line does not count.
- `set_at`: when the helper last changed the profile.
- Limits as the contract's: at most 10 roles, 100 add-ons and checks expected (resolved), 50
  skips; lines beyond them do not count. A line that does not count is named on the page
  (`bad_lines`).

**The catalog of roles** (`hslib.ROLES`, ids in English):

| Role | Expected | Why |
|---|---|---|
| (every host) | `host-settings`, `update-gaps`, `security`, `cronjobs` | messages, updates, what is reachable from where, scheduled jobs |
| `web` | + `image-updates` | services in containers, reachable from the internet |
| `database` | + `image-updates` | data that must be restorable |
| `build` | nothing more | |
| `bastion` | nothing more that reports a status | Status Board marks the bastion, but has no status entry of its own, so nothing can expect it |

`expect` in the status answer is resolved: the add-ons of every host, those of the roles, then the
extra ones, each once.

**The verbs** (root, each argument checked first; exit 64 for a wrong call, 1 when the change
cannot be made, 75 while the settings are being changed):

| Verb | What |
|---|---|
| `profile show` | the profile as `show` has it, as JSON; no root needed (the file is 0644) |
| `profile roles [ROLE ...]` | sets the roles (none: the add-ons of every host); sets up the profile |
| `profile expect add ITEM`, `profile expect remove ITEM` | an extra expectation; one the roles or every host bring cannot be removed (leave it out with a skip) |
| `profile skip add ADDON/CHECK [YYYY-MM-DD]` | leaves a check out on purpose, until that day if given; the reason on standard input; a second one for the same check replaces the first; an add-on alone is refused (exit 64) |
| `profile skip remove ADDON/CHECK` | |
| `profile remove` | removes the profile's lines: no profile any more |

`expect` and `skip` need a profile (exit 1 "set the roles first"). A change that changes nothing
says "Nothing changed" and writes nothing. A change writes the file line by line
(`hslib.format_profile`): comments, empty lines and every line that is not a valid line of the
profile (another key, a broken line) stay as they are, where they are; a valid line stays as it is
while it still holds, is changed in place, or goes; new lines go to the end. Before the file is
replaced (a temporary file in the same folder, `fsync`, `rename`, so it is never half written),
the file as it was goes to `profile.conf.bak`.

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

0. **The channels' records** from `last.json`, brought in line with the settings set now and with
   the last delivery (below, "Channel health").
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
   itself a warning. The optional parts of the contract are read as the board's
   `validateExtension()` reads them, and only those the add-on names in `features`: `checks` (at
   most 50), each item's `findings` (at most 100 over the answer, each `fid` once; a list is
   complete only when the add-on says so, nothing was left out of it, a check of state
   `checked` names the item and a whole-number `total` counts no more findings than it holds),
   an acceptance (`accepted_until`) only up to 90 days after the check, and `profile` only from
   `host-settings`. What breaks their rules is left out and counted, never makes the answer
   unreadable; an item that sends `findings` the rules leave out is told from one that sends
   none (below, "Findings"). An item is never better than its open findings: those not
   accepted, or whose acceptance has ended (the board's `itemState()`).
4. **Compare** with what was told last (`sent` in `last.json`), per key: `PKG#` for the add-on
   (warning while it cannot be read), `PKG#ID` for each item, and `PKG#ID/FID` for each finding
   of an item that lists them (below). Only warning and danger count as
   levels; ok and info are OK. A key whose level differs from the one told is a change:
   `worse` (to warning or danger, or warning to danger), `better` (danger to warning),
   `resolved` (to OK, or the item is gone from a complete answer). What cannot be checked is no
   reason for OK, so these keys keep the level told before and give no message: the items of an
   add-on that cannot be read or that answers exit 77 (and the add-on itself on exit 77), an item
   in state `none` (the add-on cannot judge it, for example Security Monitor's `exposure` when the
   packet filter cannot be read), and an item missing from an answer that left items out (more
   than 50, or broken ones). An add-on that is no longer installed while it had told problems
   gives one change `unchecked` (a warning) and its keys are forgotten, its findings with it. The same change
   (`FROM>TO`) of the same key waits when it was told less than an hour ago; the key keeps its old
   level, so the change is told after the hour if it still holds. OK keys whose last change is
   older than an hour are forgotten.
   With a target profile, the coverage of every add-on and check it expects is compared as well,
   with the keys `PKG@` and `PKG@CHECK` (below, "Coverage").
5. **The first run with a channel** (no `baseline_at` yet) sends a summary of every problem instead
   of changes and takes them as told. Where an item lists findings, the summary names its open
   findings instead of the item; accepted findings are taken as accepted and not named. With a
   target profile, the summary names the expected add-ons and checks that cannot be checked as
   well (an add-on whose status cannot be read only once, as "Status not readable"), and takes
   them as told.
6. **Send** one message for all changes of the run, to every channel set up. Only when at least
   one channel took it, the new `sent` is kept (and `baseline_at` set); otherwise the same
   changes are sent again on the next run. A channel that did not take it does not get it again
   (there is no queue per channel); each channel's result goes into its record.
7. **Once a day**, with ntfy set up: read the topic without a login (below, steps reach, tls,
   anon-read; nothing is published). A new server or topic makes the check due at once.
8. Write `last.json` (0600) and `status.json` (0644).
9. **With a watchdog**, its signal: success or failure as "The watchdog" below decides (a channel
   set up that is failed makes it a failure, whether this run sent anything or not), and its
   answer in `heartbeat.json` (0600), then `status.json` again with it.

Exit codes of `host-settings-notify.py`: 0 done (a message that could not be sent is no error;
the watchdog gets a failure), 1 failed (the previous files stay; the watchdog gets a failure), 64
wrong call, 75 another run or test holds the lock (`/var/lib/cockpit-host-settings/.lock`; no
signal at all, and a test changes nothing). A `notify.conf` that is there but cannot be read stops the run (1); a missing one
is the defaults, as before.

### Findings

An item that lists findings (its add-on names `findings` in `features` and the item has a valid
`findings` object) is compared finding by finding, with the key `PKG#ID/FID`, so a new finding
is told even when the item's state stays the same: a second port reachable from the internet in
Security Monitor's `exposure`, which is a danger already, gives a message of its own. The `fid`
comes from the add-on's rule and the resource, so the same answer in the other language or in
another order changes nothing. Each finding's entry has a status:

| In the new answer | Entry | Message |
|---|---|---|
| new, warning or danger | open | `worse` (the state) |
| known, higher state | open | `worse` |
| known, danger to warning | open | `better` |
| known, the same state | open | none |
| rated ok or info by the add-on | resolved | `resolved`; none for an accepted one |
| state `none` (cannot be judged now) | as it was | none |
| `accepted_until` in the future | accepted | none |
| acceptance ended, the finding still there | open | `expired` ("acceptance ended") |
| missing, the list is complete, the check that names the item and the item are in time, the answer left no items out | resolved | `resolved` ("no longer found by a complete check"); none for an accepted one |
| missing, anything else (a check `partial` or `failed`, a list the add-on calls incomplete, a finding left out of it, a `total` the list does not reach, out of date, items left out, status unreadable, exit 77) | as it was (unconfirmed) | none |
| the item's `findings` break the rules, or the item sends none any more (an older version of the add-on) | as it was | none |
| resolved before, here again | open | `again` |

Resolved findings are kept for 30 days, so that one coming back is told as `again`; after that it
is new. The hour against flapping holds for findings as for items (`ok>danger`, `danger>ok`,
`accepted>danger`). The memory holds at most 2000 keys; beyond that it keeps what was told as a
problem first, then accepted findings, then the rest, the newest first.

The item of such findings keeps its own key, but only for what its findings do not explain: its
level counts when no finding it lists, or remembers as open or accepted, is as severe (a danger
with an empty list that is not complete, for example); usually it is OK and not stored. Its
entry has `"findings": true`. When an item that was compared per item (by 0.1.0, or while its
add-on sent no findings) lists findings for the first time, findings no more severe than what
was told for the item are taken as told without a message, so an update of the add-on or of Host
Settings does not repeat what was told; a more severe one is told.

**Findings that cannot be read prove nothing.** The rules of `lib/hslib.py` say for every item
why it has the findings it has (`listing`, for the notifier only): it lists them (`listed`), it
sends `findings` the rules leave out (`broken`: the add-on does not declare the feature, or the
object, its `complete` or its `list` has the wrong shape), or it sends none (`none`: an add-on
that never lists findings, an older version of one, or an item without findings). `changes()`
decides per item:

- An item that never told findings is compared per item, as before: add-ons without findings
  work unchanged.
- An item whose findings were told and that lists none now (`broken` or `none`) keeps them as
  they are, open, accepted or resolved, without a message. While they are open or accepted they
  explain the item's level, so the item itself is told only for what they do not explain (a
  danger where they were warnings). They change again only with a list that can be read: a
  complete one resolves what it leaves out, and tells it; any other keeps it.
- A `broken` item is told nothing better, its own key neither: it may get worse (told), never
  better, until a list that can be read comes.
- A broken list in one item holds back nothing of the other items: their findings are compared,
  and resolved by a complete list, as usual.

Going back to a version of an add-on that sends no findings, or one that loses the extension, is
such a case. What it says of the item (OK, for example) resolves none of the findings told
before, an acceptance stays accepted (its end is told with the next answer that lists the finding
again), and a danger of the item that the open findings explain is not told again. The findings
stay in `last.json` until a version that lists findings sends a complete list, or until the
add-on is removed (one `unchecked` message, as above).

### Coverage

What Status Board knows about an add-on on a host, its coverage, judged as the board judges it
(`hslib.check_coverage`, `checks_coverage`, `tile_of`, `coverage_of`: the twins of the board's
`checkCoverage()`, `checksCoverage()`, `tileOf()` and `coverageOf()`; `test/notify_test.py` holds
them against the board's own cases): from the checks that apply (every state but
`notapplicable`), the worst of them (`noaccess`, `unconfigured`, `failed`, `unsupported`, `stale`,
`partial`, then `checked`); for an answer without checks from its items (`unchecked` when it rates
nothing, `stale`, else `checked`); `noaccess` for exit 77, `unreadable` when the status cannot be
read. Without a profile the notifier does nothing with it, as before.

With a profile (`hsnotify.profile_coverage()`), it is read as Status Board reads it: every entry
of `expect` makes its add-on expected (`security/exposure` as well as `security`), and every check
of an expected add-on is expected, unless the add-on reports it as not applicable or a skip that
runs leaves it out. A check named in `expect` that the add-on's valid answer does not report (it
sends no `checks`, or not that id, a misspelled one included) is a gap of its own:
`hslib.not_reported_checks()`, the twin of the board's `notReportedChecks()`, makes a check of
state `unsupported` for it, with the id as its title, and the add-on's coverage takes it into
account like any other check. While the add-on is not installed, installed without the status
entry, unreadable or without access, its own gap stands for its checks and none is made up; an
add-on expected alone is judged as before. An expected add-on that is not installed is `missing`
(`unsupported` when it is installed in a version without a status entry); Host Settings itself
counts as checked. The keys in `sent`:

| Key | What | Its coverage |
|---|---|---|
| `PKG@` | an expected add-on | `missing`, `unsupported`, `noaccess`, `unreadable`; for a valid answer `checked` while its own checks speak for themselves, else what its items say |
| `PKG@CHECK` | a check of an expected add-on that applies, or a check named in `expect` that the add-on does not report | the check's coverage; `unsupported` for one not reported |

Package names and check ids have no `@`, so these keys never meet `PKG#`, `PKG#ID`, `PKG#ID/FID`.
An entry is `{level, coverage, at, last, label, title}`, `level` `lost` or `ok`, kept for every
key the profile expects (so an add-on that is gone is still named by its product name). The
messages, only for what the profile expects:

| Coverage now | Told before | Message |
|---|---|---|
| `failed`, `noaccess`, `unconfigured`, `unreadable`, `missing`, `unsupported` | OK | `lost`: "Check no longer possible" |
| `checked` | lost | `regained`: "Check possible again" |
| `partial`, `stale`, `unchecked` | either | none: what was told stays |
| not applicable (a skip runs, the add-on says so), no longer expected | either | none: the key is forgotten |

`unsupported` is an add-on installed in a version without the status entry, a check the add-on
cannot run on this system, or a check named in `expect` that the add-on does not report. Up to
0.2.0 it kept what was told; after the update each such gap is told once, as one that the memory
holds as OK.

- The same change of the same key waits for the hour against flapping (`ok>lost`, `lost>ok`), and
  every change of a run goes into its one message, as for items and findings.
- While an expected add-on cannot be judged (not installed, exit 77, unreadable), what was told
  about its checks stays as it was, a check it did not report included.
- A check that comes back `checked` and in time is told once as possible again. Neither that nor
  its loss resolves a finding or makes one new: findings change only with their lists (above,
  "Findings").
- A change of `PKG@` is not told in a run that tells about the add-on itself (its status is not
  readable or readable again, it is no longer installed), though it is remembered.
- The first run after a profile is set up finds its keys missing from `sent`: the gaps of that
  moment are changes from OK, told once in that run's message (or in the summary on the very first
  run). Removing the profile forgets the keys without a message.

### Messages

`{title, body, priority, tags}`, from `lib/hsnotify.py` (`message()`), in English or German:

- Title: `HOST: LABEL, TITLE` for one change, `HOST: LABEL, no longer checked` for an add-on
  that is no longer installed, `HOST: N changes` for several, `HOST: messages set up` for the
  summary, `HOST: test message`.
- Body: one block per change, the most severe first:
  `STATE · LABEL · TITLE (VALUE)` and, on the next line, the detail, "out of date since …"
  for an item past its `stale_after`, or why it is OK again ("no longer reported"). For the
  add-on itself: "Status not readable" and the reason. A finding:
  `STATE · LABEL · FINDING TITLE` (`STATE, again`, `STATE, acceptance ended`, `STATE instead of
  BEFORE`, `OK again`) and, on the next line, the title of its item (with "out of date since …",
  or ": no longer found by a complete check" when it is resolved that way). An add-on that is no longer installed:
  `No longer checked · LABEL · the add-on is no longer installed` and how many of the findings
  it reported can no longer be checked. An expected add-on or check (coverage):
  `Check no longer possible · LABEL · CHECK TITLE` and why ("the check failed", "nothing is
  readable without root", "the check is not set up (its timer is off, or it never ran)", "the
  status cannot be read", "the add-on is not installed", "this version of the add-on reports no
  status", "the check is not supported on this system", "the expected check is not reported" with
  the check's id as its title; for a check with the add-on's reason after a colon), or
  `Check possible again · LABEL · CHECK TITLE`; for the add-on itself without the check. The title of one such change: `HOST: LABEL, check no longer possible` or
  `HOST: LABEL, check possible again`. At most 20
  blocks and 3800 bytes (ntfy turns longer messages into attachments), then "and N more"; the
  footer `Host Settings on HOST`.
- Priority (ntfy): 4 when the worst change is a danger, 3 for a warning (a check no longer
  possible is one), 2 when everything is OK again; the summary 4 with a danger, else 3. Tags:
  `rotating_light`, `warning`, `white_check_mark`.

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
 "access": {"state": "ok", "anon_read": "ok", "anon_write": "ok"},
 "health": "ok", "recovered": true}
```

The page shows it only while `target` is the server and topic set now. `health` is the channel's
state after the check (below), `recovered` is there when the check ended a failure. "Send a test
message" writes `test-mail.json` the same way: `{at, channel: "mail", target: ADDRESS, result:
{ok, code, reason}, health, recovered}`; the page shows it only while `target` is the address set
now (a file of 0.2.0 has no `target` and is not shown). The answers are those of
ntfy 2.11 and 2.32 in the job `e2e`: 403 for what a login does not allow, with or without a token.
`/v1/account` answers 200 but does not list what `ntfy access` grants, so the token's rights are
found out by reading with it.

### Channel health

`lib/hschannels.py`. Each channel set up keeps a record in `last.json` (`channels`); its health is
judged in every run, whether the run has something to send or not, and after every test of the
page:

| State | When | Its item in the status | The watchdog |
|---|---|---|---|
| `ok` | it accepted the last message or a test with the settings set now | ok | success |
| `failed` | from a message or a test it did not accept, until it accepts one again with the settings set now | warning | failure |
| `unknown` | it has accepted nothing with the settings set now: never, not since the update, or not since its target changed | none ("not confirmed"), never ok | success |

Accepted means that the ntfy server answered with 2xx (for "Check connection" the step `publish`)
or that the local mail server took the message. It does not mean that anyone read it: neither Host
Settings nor the watchdog can know that. A run, a channel accepting a message and a person taking
note are three different things.

- **A result belongs to its target**: the server and topic of ntfy (`URL/TOPIC`), the address of
  e-mail, kept as they are (`last.json` is 0600, and root sees them in the settings anyway). Never
  the token, nor a hash of it: a new token is the same target. A run's results belong to the
  settings it read. A test reads `notify.conf` again before it writes: a result whose target is no
  longer the one set up (the settings changed while it ran) is not counted at all, and the journal
  says so. A success for another target never confirms the one set now.
- **A failure stays** until the channel accepts a message of a run or a test for the target set
  now. A run with nothing to send changes nothing, also when the cause is gone (the mail server is
  back). A new target is no repair: the failure stays until the new one accepts something. A
  success that ends a failure is its one recovery: `recovered` in the record, one line in the
  journal (`channel mail: accepted again (test, failing since ...)`), the item of the channel
  names it, and the watchdog sees it as the next run's success after the failures.
- **A channel taken out of the settings** is no longer counted, its failure neither. The next run
  or test records `removed` (with `failed_since` when it was failing) and says in the journal that
  this is a change of the settings, not a repair; no recovery is recorded. Set up again, the
  channel starts anew, unknown. Taken out and set up again between two runs counts as a new target.
- **Nothing is sent again to one channel.** A message that one channel took is told: the channel
  that failed does not get it later (there is no queue per channel), and its record shows since
  when it failed. A message that no channel took is sent again on the next run, as before, and
  nothing of it counts as told.
- **Tests and the lock.** "Check connection" and "Send a test message" run in the test unit as
  the notifier's user and take the notifier's lock (`/var/lib/cockpit-host-settings/.lock`), as a
  run does: no run is in between, and while a run holds it the test ends with 75 and changes
  nothing. After its requests the test reads `last.json`, takes its result in and writes
  `last.json` (a temporary file in the same folder, `fsync`, `rename`, so it is never half
  written) and `status.json`. Before the first run it writes `last.json` all the same (`format`,
  `channels`, `access`), but no `status.json`. The helper's own lock
  (`/run/cockpit-host-settings.lock`) guards the settings, not the state; that a test reads the
  settings again is what keeps a change of them during a test apart. A test of the watchdog has
  nothing to do with the channels.

The record of a channel, times as ISO 8601, `via` `run` or `test`:

```json
"channels": {
  "ntfy": {"ok": {"at": "2026-11-01T10:00:00Z", "via": "run", "target": "https://ntfy.example.com/cockpit-web1"}},
  "mail": {"failed": {"since": "2026-11-01T10:00:00Z", "at": "2026-11-01T10:10:00Z", "via": "test", "reason": "no-mta",
                      "target": "ops@example.com"},
           "ok": {"at": "2026-10-31T08:00:00Z", "via": "run", "target": "ops@example.com"},
           "recovered": {"at": "2026-10-30T09:00:00Z", "via": "test", "since": "2026-10-30T08:00:00Z", "reason": "no-mta"}}
}
```

| Field | What |
|---|---|
| `failed` | the failure that has not ended: since when, the last failed try, how, the reason word of ntfy or e-mail (above), its target |
| `ok` | the last time it accepted something: when, how, its target |
| `recovered` | the last failure that ended: when, how, since when it had failed, its reason |
| `removed` | `{at, failed_since}`: taken out of the settings (`failed_since` null when it was not failing) |
| `reset_at` | set up again after a removal |

What does not hold (a hand edit) is left out when the records are read (`records_of()`).

**Format, update, going back.** `last.json` keeps format 1: `channels` is a new key, as the keys
of findings and coverage were, and a file of another format is still started anew. 0.2.0 and
0.1.0 kept no records, only the results of the last message (`delivery.results`). The first run or
test of this version takes them in and makes nothing up: a channel that did not take that message
is failed from its time on, without a target; one that took it has a success without a target,
which confirms nothing (unknown, not ok) and ends no failure. `show` judges the same way before
that first run and writes nothing. Restarts change nothing: every run is a new process that reads
the records. Going back to 0.2.0 works: it reads format 1 on, keeps `channels` as they are (it
updates the file it read) and writes its own `delivery`. When this version comes back, a
`delivery` newer than everything a channel's record knows is from that time and is taken in the
same way: a failure is kept (with its first `since` when the channel was failing already), and a
success without a target does not replace a confirmation with one. Going back to 0.1.0 is not
tested: it knows neither findings nor coverage keys.

## The watchdog

An external watchdog notices when the notifier stops or fails: after every run that holds
the lock the notifier sends it a signal (`lib/hsheartbeat.py`), and the watchdog raises the alarm
when a failure comes or the signals stay away. It judges whether the notifier works, not whether
the host is in danger: findings go out as messages. Off by default; one watchdog per host,
chosen explicitly, never guessed from the address.

### The settings

`/etc/cockpit-host-settings/heartbeat.conf` (root, 0600), written by the helper only:

```
# Host Settings: the watchdog that gets a signal after every run. Secret: root only (0600).
adapter = uptime-kuma
url = http://10.8.0.2:3001/api/push/TOKEN
```

The whole URL is a secret (whoever knows it can report runs and keep the alarm quiet): it reaches
the helper on standard input (the page: a password field, cleared once sent), the notifier as the
systemd credential `heartbeat.conf` (`LoadCredential=` in the notify and test units), and appears
in no other file, output, log, status, screenshot or process argument. `notify.conf` (0644) holds
nothing of it. An empty file (the package's default) is no watchdog; a file that says something
that does not hold (a broken hand edit) is `invalid`: no request, the answer `invalid`.

What the helper takes (`hsheartbeat.normalize`, exit 64 with the reason otherwise):

- `http(s)://host[:port]/path`, at most 500 characters, no user, no fragment; a trailing slash is
  dropped.
- `https://` for any host, the certificate and the host name checked as usual. Plain `http://`
  only to an IP address of a private network: 10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7 or
  the loopback (a watchdog reached through WireGuard, say), never to a host name, which could
  resolve to anything. This is the deliberate exception for a watchdog at home behind a VPN; the
  page marks such an address "Not encrypted: private network".
- `healthchecks`: the ping URL (`https://hc-ping.com/UUID`, `https://hc-ping.com/KEY/SLUG` or a
  server of one's own, `…/ping/UUID`), no query, and not ending in `/fail`, `/start`, `/log` or an
  exit status.
- `uptime-kuma`: the push URL, `…/api/push/TOKEN`; the query Uptime Kuma's page shows after it
  (`?status=up&msg=OK&ping=`) is taken and dropped, any other query refused.
- A watchdog on the loopback is taken (the end-to-end test needs it) and named on the page: it
  cannot notice when its own host fails.

### The signals

| Signal | Healthchecks | Uptime Kuma |
|---|---|---|
| success | `POST URL`, text body "Host Settings: run done" | `GET URL?status=up&msg=…` |
| failure | `POST URL/fail`, body "Host Settings: " and the reason | `GET URL?status=down&msg=…` (never `/fail`) |
| test (the page) | `POST URL/log`: logged, neither success nor failure | `GET URL?status=up&msg=…`: Uptime Kuma knows no other kind, so a test counts as a heartbeat |

GET for Uptime Kuma: 1.x takes only GET on the push URL, 2.x any method. The reason is one of our
own texts, never an add-on's: "the settings could not be read", "reading the add-ons failed", "a
message could not be sent (ntfy timeout, mail no-mta)", "a channel has not worked since it failed
(mail no-mta)", "the state could not be saved", "the run failed".

What an answer means (`hsheartbeat.judge`); a 2xx alone is not enough:

| Answer | Healthchecks | Uptime Kuma |
|---|---|---|
| taken (`accepted`) | 200 `OK`, 201 | 2xx with `{"ok": true}` |
| `not-found` | 200 `OK (not found)` (a UUID it does not know), 404 | 404 with `{"ok": false}` |
| `rate-limited` | 200 `OK (rate limited)`, 429 | |
| `refused` | | 2xx with `{"ok": false}` |
| `ambiguous`, `invalid-url` | 409, 400 | |
| `unexpected` | any other 200 body | a 2xx without `ok` |
| `redirect` | any 3xx: never followed, so the URL never goes to another target | the same |
| `http-NNN` | any other status | the same |
| `tls`, `timeout`, `network` | no answer | the same |

Each signal gets at most 3 tries of 10 seconds each, 2 and 5 seconds apart, never more than 40
seconds in all; only `timeout`, `network` and 5xx are tried again. The request is
`hsnotify.http`, as for ntfy: TLS checked with the system's certificates, redirects not followed.

### The result of a run

Decided explicitly in `do_run()` and `main()`, never from the exit code (0 also when a message
could not be sent) and never in a `finally`:

| The run | Signal |
|---|---|
| done and `last.json` and `status.json` written, every channel set up `ok` or `unknown` (above, "Channel health") | success |
| findings told correctly (a danger, an expected check no longer possible, an add-on whose status cannot be read) | success: they go out as messages; a gap in coverage is no failure of the notifier |
| a message did not reach at least one channel set up (also when another took it and it is not sent again; delivery and its retry stay as they are) | failure (`why` `delivery`), reason "a message could not be sent" with the channels that are failed |
| nothing failed in this run, but a channel set up is failed: it did not accept an earlier message or test and has accepted nothing since with the settings set now, also when the run had nothing to send | failure (`why` `channel`), reason "a channel has not worked since it failed" with the channels, in every run until that channel accepts a message or a test |
| a channel taken out of the settings | not counted: its failure ends without a recovery |
| `notify.conf` there but not readable | failure ("the settings could not be read"), exit 1 |
| collecting the add-ons ends with an exception | failure ("reading the add-ons failed"), exit 1 |
| `last.json` or `status.json` cannot be written | failure ("the state could not be saved"), exit 1 |
| another run or test holds the lock | nothing at all, exit 75: the run that holds it signals |
| hangs, is killed (`TimeoutStartSec=5min`), the timer is off, the host is down | nothing: the watchdog notices the silence |
| a run with nothing set up but the watchdog | success: the watchdog alone is enough for the timer |

The answer goes to `heartbeat.json`, a file of its own:

```json
{"at": "2026-10-19T19:00:01Z", "adapter": "uptime-kuma", "signal": "failure", "why": "delivery",
 "result": {"ok": true, "code": 200, "reason": "accepted", "attempts": 1}}
```

A signal that does not get through changes nothing in `last.json`: no finding is reset, no
channel's record changes, no message goes out again, the exit code stays the run's. The status shows it (item `heartbeat`, below), the
page under Watchdog, the journal as `watchdog: success, accepted` or `watchdog: failure, not
accepted: timeout`. The test signal writes `test-heartbeat.json` (`{at, channel: "heartbeat",
adapter, result}` or `{at, channel, error: "not-set-up"|"invalid"}`), never `heartbeat.json`.

### Period and grace

The timer: `OnCalendar=*:0/5`, `RandomizedDelaySec=30s`, `AccuracySec=10s`, so each run starts 0
to 40 seconds after its 5-minute mark, and two starts are 260 to 340 seconds apart. The signal
goes at the end of a run, which takes seconds; at worst the status verbs (10 s each), ntfy and
SMTP (20 s each), the daily check of the topic (3 × 20 s) and the signal itself (40 s), well
within the unit's 5 minutes, after which a run is killed and sends nothing. With runs under a
minute, two signals are at most about 400 seconds apart, and one missed or killed run makes about
700 seconds. Hence, so that one late or missed run raises no alarm and two in a row do (an alarm
at the latest 12 minutes after the last signal):

| Watchdog | Setting |
|---|---|
| Healthchecks | Period 5 minutes, Grace 7 minutes |
| Uptime Kuma, monitor of type Push | Heartbeat interval 720 seconds, Retries 0; a failure signal then raises the alarm at once (with Retries 1 a single failure, or the first missed window, waits one Heartbeat Retry Interval) |

The watchdog must not run on the watched host alone: it has to run outside the host and outside
what fails together with it (power, network, the same hypervisor), and something else should
watch the watchdog in turn. Which watchdog watches the servers, where it runs and who watches it
is decided outside this repository.

### Switching off

The signals simply stop, which the watchdog cannot tell from a failure. `remove timer`,
`heartbeat remove` and the page's dialogs say so before and after: pause or delete the check at
the watchdog first, or it raises the alarm once its grace time is over. Removing the package stops
the timer the same way and keeps `heartbeat.conf`.

### Tested against

- Uptime Kuma 2.5.7 (`louislam/uptime-kuma:2.5.7-slim`, pinned by digest) in job `e2e`: its first
  user and a push monitor made through its own Socket.IO API in the container (`test/e2e/kuma.py`,
  nothing leaves the runner), heartbeats while the timer runs, down when it stops, exactly one
  recovery, a failure with its reason. Uptime Kuma 1.x is untested; its push URL is the same, and
  the signal is a GET, which 1.x needs.
- Healthchecks: a stand-in for its pinging API (`test/e2e/watchdog.py`, the answers its
  documentation gives, also 200 `OK (not found)`), not Healthchecks itself.

## `last.json`

```json
{
  "format": 1,
  "baseline_at": "2026-10-19T18:00:00Z",
  "sent": {"cronjobs#failed": {"level": "danger", "at": 1760900000, "last": {"ok>danger": 1760900000}, "label": "Cron Jobs", "title": "Failed runs"},
           "security#exposure/port.6379-tcp": {"level": "danger", "status": "open", "at": 1760900000, "last": {"ok>danger": 1760900000},
                                               "label": "Security Monitor", "title": "6379/tcp redis", "item": "Reachable from the internet"}},
  "seen": {"cronjobs": {"label": "Cron Jobs", "kind": "ok", "reason": null, "code": null, "state": "danger", "version": "2.8.1", "summary": "…",
                        "items": [{"id": "failed", "title": "Failed runs", "state": "danger", "value": "2", "detail": null, "link": null, "stale": false}]}},
  "run": {"at": "…", "took_ms": 812, "addons": 4, "bad_lines": []},
  "delivery": {"at": "…", "summary": false, "changes": 1, "title": "…", "results": {"ntfy": {"ok": true, "code": 200, "reason": "sent"}}},
  "access": {"state": "ok", "anon_read": "ok", "anon_write": "ok", "at": "…", "target": "…", "full": true, "steps": […]},
  "channels": {"ntfy": {"ok": {"at": "…", "via": "run", "target": "https://ntfy.example.com/cockpit-web1"}}}
}
```

A file of another `format` is started anew (and the next run sends a summary again). `channels`
holds the record of each channel (above, "Channel health"); it came without a new `format`, and
how a file of 0.2.0 is taken in and how going back to 0.2.0 works is described there. The keys
of findings came without a new `format`: a file of 0.1.0 holds only keys of add-ons and items,
which are read as before (see "Findings" above). So did the coverage keys `PKG@` and `PKG@CHECK`
in `sent` and `coverage`, what the last run found for each add-on and check the profile expects
(`null` without a profile), for the page:

```json
"coverage": [{"id": "security", "label": "Security Monitor", "coverage": "unsupported",
              "gaps": [{"id": "containers", "title": "Container hardening", "coverage": "partial"},
                       {"id": "exposure", "title": "exposure", "coverage": "unsupported", "not_reported": true}]},
             {"id": "image-updates", "label": "Image Update Monitor", "coverage": "missing", "gaps": []},
             {"id": "security/exposure", "label": "Security Monitor", "coverage": "unsupported", "gaps": [],
              "not_reported": true},
             {"id": "security/fail2ban", "label": "Security Monitor", "coverage": "notapplicable", "gaps": [],
              "reason": "fail2ban is not installed"}]
```

`not_reported` marks a check named in `expect` that the add-on does not report, `reason` is the
add-on's reason for a check it reports; the page shows either below the label. Both came without
a new `format`: a page or a file without them reads as before.

## `status.json` and `host-settings.py status` (status contract)

The notifier writes the English document with the German texts under `i18n` (`lib/hsstatus.py`);
the verb answers in the language asked for. Items, each with the link `#/notifications`:

| Item | State |
|---|---|
| `setup` | ok with the channels (value: their number), none without one |
| `ntfy`, `mail` | one for each channel set up (they replace `delivery`, the last message, of 0.2.0): ok when it accepted the last message or a test with the settings set now (detail: when, and the failure it ended), warning from a failure until then (since when, why, and that runs with nothing to send do not end it), none while that is not known ("not confirmed", never shown as tested); never the target |
| `access` | only with ntfy: warning when the topic is readable or writable without a login, ok when closed, none when not checked |
| `heartbeat` | only with a watchdog: ok when it took the last signal (detail: when, and whether it was told of a failed run or of a failing channel), warning when the signal did not get through (with the reason) or the settings do not hold, none before the first signal; never the address |
| `run` | ok (value: the add-ons read), none without a channel (the notifier tells nobody); `stale_after` 900 seconds, so the verb and the menu icon make it a warning when three runs are missed |

Without `status.json` the verb answers "Not set up yet"; after `remove timer` the file says
"Switched off" (state none). Exit 77 when the file cannot be read. `test/run_test.py` checks every
answer against Status Board's rules (`hslib.validate_status`).

**The profile.** With a target profile, the verb reads `profile.conf` itself (0644, so as any
user, and a change shows at once, before the notifier runs) and adds the optional part of the
contract to every answer:

```json
"features": ["profile"],
"profile": {"roles": ["web"],
            "expect": ["host-settings", "update-gaps", "security", "cronjobs", "image-updates", "security/exposure"],
            "skip": [{"check": "update-gaps/cve", "reason": "network data off on purpose", "until": "2026-12-31T00:00:00Z"},
                     {"check": "security/fail2ban", "reason": "CrowdSec instead"}],
            "set_at": "2026-10-25T10:00:00Z"}
```

`expect` is resolved from the roles; `until` (only when the skip has an end) is its day at 00:00
UTC; `set_at` is the file's `set_at`, else the file's time. Without a profile, or when the file
cannot be read, neither field is sent, and Status Board says the host has no target profile.
`test/helper_test.py` checks that the board's rules (`hslib.validate_profile`, the twin of the
board's `validateProfile()`) keep every part of it, also at the limits (`ext_dropped` 0).

## Cockpit's menu (`hsmenu.py`)

The verbs for the menu editor of the Cockpit tab (below, "Page internals").

| Call | Superuser | What |
|---|---|---|
| `host-settings.py menu show` | none | every entry of Cockpit's menu and what the override files change in it, as JSON; run as the user whose menu it is |
| `host-settings.py menu apply system` (plan on standard input) | require | the override files for all users |
| `host-settings.py menu apply user` (plan on standard input) | none | the calling user's own override files; refused as root (64), else it would change root's menu |

**How Cockpit reads it** (its bridge, `src/cockpit/packages.py`, read on 2026-11-01): the pages are
`cockpit/*/manifest.json` below `$XDG_DATA_HOME` (`~/.local/share`), then below `$XDG_DATA_DIRS`
(`/usr/local/share`, `/usr/share`). Before it reads a manifest's name, priority and conditions it
merges three override files into it (JSON merge patch, RFC 7396), each later one winning: the
package's own `override.json`; `<folder>.override.json` in the first of `$XDG_CONFIG_DIRS` (`/etc`)
that has one below `cockpit/` (else the first); `<folder>.override.json` in
`$XDG_CONFIG_HOME/cockpit` (`~/.config/cockpit`). `<folder>` is the package's folder, not its
name. Of several packages with one name it serves the one with the highest priority whose
conditions (`path-exists`, `path-not-exists`, `any`; others hold) are met. A file that is not a
JSON object is ignored. The bridge loads all this when the session starts and again whenever the
Shell starts anew, so reloading Cockpit in the browser is enough. `hsmenu.load()` does the same,
except that it does not check a manifest's `requires`. What a real bridge serves after the
helper's changes, and what the shell's navigation then shows, the end-to-end tests check (below,
"Tests": job `e2e`, job `e2e-el`, E2E-1).

**`menu show`** prints one object:

- `entries`: one per entry of `dashboard`, `menu` and `tools` of every package served, in the
  order of the package's manifest: `section`, `key`, `package` (its name), `dir` (its folder),
  `editable`, `base` (`label` and `order` with the package's own `override.json`), `system` and
  `user` (what that scope's file does to it: `null` nothing, `{"hidden": true}`, or the `order` and
  `label` it sets, `"other": true` when it holds more), and `effective` (`label`, `order`, `hidden`).
- `orphans`: a key in an override file that the package does not have (`scope`, `dir`,
  `section`, `key`, and `value` as for an entry's `system` and `user`, so that a plan can keep it).
  With `phantom` the file holds an object for it, and Cockpit shows an entry without a page; a
  null for it does nothing.
- `files`: every override file of both scopes: `scope`, `dir`, `path`, `sha256` of its bytes,
  `valid`, `error` (`unreadable`, `too-large`, `invalid`, `not-an-object`), `foreign` (how many
  things in it the editor does not manage) and `served` (whether a package with that folder is
  served).
- `user`, `uid`, `paths` (the folders of both scopes), `cut` (more than 200 manifests).

**`menu apply system|user`** takes a plan of at most 64 KiB on standard input:

```json
{"base": {"systemd": "<sha256 of the file the plan was made from, or empty>"},
 "set": {"systemd": {"menu": {"logs": null, "services": {"order": 1, "label": "Units"}}}}}
```

- `set` is the editor's whole part of this scope for each folder named in `base`: a folder in
  `base` without `set` loses it. The editor's part is every `null` and every `order` and `label`
  in `dashboard`, `menu` and `tools`; everything else in a file stays as it is, in its order, also
  `bridges` or `content-security-policy` that someone else wrote there.
- Refused whole (64, nothing written): anything but `base` and `set`; more than 200 folders or
  100 entries in one section; a folder not `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` or in `set` but not in
  `base`; a checksum that is not 64 hex digits; a section other than the three; a key not
  `[A-Za-z0-9_-]{1,64}`; a value other than `null` or an object with only `order` and `label`; an
  `order` that is not a number from -1000 to 10000 with at most three decimals; a `label` that is
  not 1 to 60 characters, NFC, without control, zero-width or bidirectional characters, line breaks
  or spaces at either end.
- Refused whole for a file: 75 when its `sha256` is no longer the one in `base` (someone changed
  it); 1 when it cannot be read or is not a JSON object, and when hiding an entry would delete what
  someone else wrote for it there (or the entry there is not an object).
- A file left with nothing is removed. Files are written atomically next to the old one (0644),
  under an exclusive lock on their folder (75 when held). For `system` the version before is kept
  in `/var/lib/cockpit-host-settings/backup/menu/<UTC time>/` (0700, the files 0600), the newest
  five of these folders stay.
- The output names each file changed or removed, then that it takes effect once Cockpit is
  reloaded; "Nothing changed." when nothing was.

## Page internals

- `plugin/menu-status.js` runs first: Cockpit loads the page hidden after the login
  (`"preload": ["index"]`) for the icon in its menu; it reads only `status.json` and judges it as
  the board does. `settings.js` starts once the page is shown.
- One dialog for every change (`dlg` in `settings.js`): a description, the fields, then the live
  output of the helper and the result. The `RESULT` line of `check` and `test` is data for the
  outcome, not for the log. Password fields are cleared once sent.
- The commands for the ntfy server are built from the topic: user and topic are the same name;
  the reader's pattern replaces the host name in the topic with `*`.
- Tabs: `TABS` is Notifications and Cockpit; the location (`#/notifications`, `#/cockpit`) selects one.
  Both show also without administrative access: Notifications then holds the summary of the status
  contract, Cockpit the menu editor just for the user. The page messages and the first loading card
  belong to Notifications.
- The Cockpit tab holds the card "Menu" (`renderMenu()`). It runs `menu show` the first time the tab
  opens (never while Cockpit keeps the page hidden), never with administrative access, and adds the
  one script the page loads itself, `../manifests-i18n.js` (the bridge's translations of every
  package's manifest); once that has loaded, a label from a manifest is shown as
  `cockpit.gettext(label)`, as the navigation shows it, and without it (an error, an older Cockpit)
  as the manifest has it. A name set in an override is fixed text, never translated.
  - Scope: "For all users" (the default with administrative access) or "Just for me" (the only one
    without). A list shows what that scope's users see: for all users the package's entry with the
    override for all users, just for me with the user's own on top. The scope is fixed while
    changes wait.
  - Three lists, Apps (`dashboard`), System (`menu`) and Tools (`tools`), in Cockpit's order: by
    `order` (1000 without one), then by the label shown. Each row: the label, the original under a
    renamed one, package and key; labels for not saved, the start page (the first entry of System
    that is shown; "Becomes the start page after login" when the draft changes it), hidden (hidden
    for all users, just for me), renamed, changed for all users, changed just for you, and "“Just
    for me” overrides this" (for all users, when the user's own file sets something for the entry).
    Hidden entries stay in their place, greyed.
  - Buttons per row: "Move up" and "Move down" (buttons with the entry's name as `aria-label`; the
    focus stays on the entry), "Rename" (the dialog, which then runs nothing), "Hide" or "Show". None
    for an entry the helper does not write (`editable` false, or a folder or key outside its
    patterns), for one whose override file in that scope is not valid, and, just for me, for one
    hidden for all users. "Hide" is off when the file holds more for that entry (`other`): the
    helper would refuse to delete it.
  - The draft (`draft`, in memory): the mark each changed entry should have in the scope. Moving
    gives only the moved entry a new `order`, midway between its new neighbours (three decimals at
    most), one less or more at either end; when that leaves no room (equal orders, too close), the
    entries from its new place on get the next whole numbers, as far as needed. "Show" brings back
    the mark as saved (or none). A name equal to the original, or empty, removes the name.
  - Below the lists: a warning for each override file that is not valid, a note for each file of
    the scope that holds settings the editor does not manage, and the settings for keys a package
    does not have (`orphans`; for an object: "Makes an entry without a page"), with "Remove" in the
    scope being changed.
  - "Save changes" and "Discard" only with a draft. Saving opens the dialog with the changes in
    words ("Hide Logs", "Services before Overview: becomes the start page after login", how many
    entries got a new number to make room, the settings removed, the files whose other settings
    stay), then runs `menu apply system` (administrative access) or `menu apply user` (as the user;
    the only call of the dialog without administrative access) with the plan on standard input:
    for each package folder touched, `base` is the `sha256` of the scope's file from `menu show`
    (empty when there is none) and `set` is the editor's whole part of that file after the draft
    (every hidden entry, order and name, and those of the keys without an entry, from their `value`,
    unless removed here), so what the draft did not touch stays. Only a key outside the helper's
    pattern cannot be kept: the dialog names it as removed. Exit 75: the
    dialog says the menu changed meanwhile, the page reads it again and keeps the draft. After
    saving: `menu show` again, the draft cleared, and "Saved" with the hint to reload Cockpit (F5)
    and the button "Reload Cockpit" (`window.top.location.reload()`; when that throws, the hint
    alone).
- The watchdog is a section of the Notifications tab (`renderHeartbeat()`): set up or not, the
  service, "Stored (readable by root only)" instead of the address, "Not encrypted: private
  network" for plain http, the last signal and the last test, the settings that fit the timer.
  "Set up a watchdog" and "Change" take the URL in a password field and hand it to the helper on
  standard input; "Remove" and "Switch off" say first that the watchdog will raise the alarm. A
  watchdog alone enables "Set up the regular check".
- Each channel's card has a row "Channel state" from `show`'s `channels` (`healthCell()`):
  working (it accepted the last message or a test, and the failure that ended), failing (since
  when, why, and that runs with nothing to send do not end it), or not confirmed. The overview is
  a warning while a channel is failing and names it; the row "Last message" says which channel
  accepted the last message. A note under the overview says that a run, a channel accepting a
  message and someone reading it are three things. After "Check connection" and "Send a test
  message" the dialog says when the test ended a failure, or that its result does not count
  because the settings changed while it ran.
- The target profile is a section of the Notifications tab (`renderProfile()`), with
  administrative access only: the roles, what is expected and why (every host, a role, added
  here) with the coverage the notifier found last (and below it the add-on's reason, or "Expected
  check is not reported"), the checks left out with reason and end. Each
  change opens the dialog: the roles as switches (from the catalog `show` sends), an add-on or
  check, a skip (the reason goes to the helper on standard input), a remove. Without
  administrative access the summary names the profile from the status answer, with no button.

## Tests

- `test/notify_test.py`: every answer in `test/fixtures/board/status` with the expectations of the
  board's own `test/status.test.js` and the board's tests of the optional parts, of a complete
  list (the shared answers `ext-complete-*.json`) and of checks a profile names (the shared
  answers `ext-expect-*.json`: not reported, misspelled, gone and back, not applicable, left out
  until a day, an add-on that cannot be judged, an add-on expected alone, two of one add-on, an
  update and a restart), its manifests;
  reading an add-on (trust, exit codes); changes, flapping, unreadable add-ons, removed ones;
  findings as sequences of answers (a second finding while the item is a danger, the same answer
  in the other language or order, lists that cannot say a finding is gone, resolved, again,
  accepted and ended, thirty at once, the memory of 0.1.0, findings that break the rules or are
  no longer sent, an older version of the add-on); the coverage twins against the board's
  own cases, and the coverage of a target profile run after run (an expected add-on gone and
  back, an optional one gone, a skip that runs and ends, no access, not set up, the hour against
  flapping, a run without a change, the memory of 0.1.0); the texts of messages; ntfy and SMTP
  with stand-ins; every step of "Check connection" against recorded answers of a closed and an
  open server.
- `test/run_test.py`: whole runs of `host-settings-notify.py` with stand-ins for the add-ons, the
  network, SMTP and the clock (also with a `last.json` of 0.1.0, with a target profile set up
  first or later, and with a list of findings that breaks the rules); the own status against the
  board's rules.
- `test/helper_test.py`: the verbs of `host-settings.py`, the profile's among them (refusals,
  unknown roles, the file line by line, atomic write and backup, the limits, the status field),
  and the watchdog's (the URL from standard input only, 0600, never printed, not by `show`
  either; the refusals; the timer with a watchdog alone; what switching off says), and the
  menu's (`Menu`, in a throw-away tree: priority, conditions and the order of the overrides as the
  bridge has them, orphans and broken files; a plan that keeps everything else in a file, hides,
  shows again and removes an empty file; just for me never as root; every refusal leaves every
  file as it was; a file changed meanwhile, the backups, the lock).
- `test/heartbeat_test.py`: the addresses each adapter takes, the requests of both protocols,
  every answer (also 200 bodies that say no and `{"ok": false}`), the tries and the budget;
  `hsnotify.http` against real sockets (a redirect not followed, a timeout, a TLS error, nobody
  listening); whole runs: success only once the state is saved, failure when a message did not go
  out although the run exits 0 (and in the runs after it, while that channel has accepted
  nothing), when the settings cannot be read, collecting aborts or the state cannot be written,
  nothing when another run holds the lock, no request at all without a watchdog, a signal that
  does not get through kept apart from the findings, the URL in no file, output, status or
  argument; the status item by the board's rules.
- `test/channels_test.py`: the health of each channel, as whole runs and tests of the page: one
  channel of two failing (the status and the watchdog's signal), the runs after it with nothing to
  send (no message twice, no recovery, also with the mail server back), a test or a later message
  that heals that channel only and once (also "Check connection", which can fail ntfy as well), a
  test whose settings changed while it ran, a new target that starts unknown, a channel taken out
  and set up again, the `last.json` of 0.2.0, restarts, going back to 0.2.0 and forth, another
  format, both channels failing (the summary and a change come again), the watchdog not reached,
  a test while a run holds the lock, the file a test writes; `hschannels` on its own.
- `test/page.test.js`, `test/files.test.js`, `test/i18n.test.js`: the page under jsdom, its files,
  its texts. The menu editor from a `menu show` fixture: read once its tab opens and never while
  hidden, the lists in Cockpit's order, moving (midway, at either end, without room), renaming,
  hiding and showing in the draft, the dialog's words and the plan on standard input (`base`, and
  a setting of the file the draft did not touch kept in `set`), for all users with administrative
  access and just for me without, the start page, orphans, a file that is not valid, a file changed
  meanwhile (75), hostile labels as text, the translated labels and German.
- `tools/check-board-fixtures.sh`: `test/fixtures/board` is unchanged from Status Board
  (`tools/board-fixtures.sha256`).
- `tools/preview/build.py` and `shoot.py` (job `test`): the page with `tools/preview/cockpit-stub.js`
  in place of Cockpit and the made-up scenarios `fresh`, `ready`, `open`, `nomta`, `menu` and
  `menu-noadmin` (the Cockpit tab, with and without administrative access, German labels from a
  stand-in `manifests-i18n.js`), the status texts from `lib/hsstatus.py` itself. `shoot.py` opens every scenario in Chromium in both themes, both
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
  the first run with the summary through both channels, a change, a run without one, a second
  finding of an item that is a danger already (one message), a target profile that expects the
  stand-in, which then goes away (one message "Check no longer possible"), the status verb as
  nobody with the profile in it, the watchdog against `test/e2e/watchdog.py` (a stand-in for both
  protocols: a success after a run, a failure for a message that reached e-mail only while the run
  ends with 0 and for settings that cannot be read, nothing from a run that loses the lock, an
  unknown push token, a 200 `OK (not found)`, a redirect; then, with the timer every 5 seconds, the
  timer stopped, the signals missed, the timer started, exactly one recovery, for both protocols),
  ntfy failing until "Check connection" after its address was set back (a run with nothing to
  send in between stays a failure), e-mail failing while postfix is stopped and ntfy takes the
  finding once, kept through runs with nothing to send (also once postfix is back) in `last.json`,
  the status as nobody and the watchdog's signal, then "Send a test message" and exactly one
  recovery (in the stand-in watchdog and in the journal), with `KUMA_IMAGE` the same against a real
  Uptime Kuma, switching off, and looks for the token and the watchdog's address in every file,
  the journal and every process's arguments. It also checks that
  Cockpit lists the page (`cockpit-bridge --packages`). Then Cockpit's menu, with the
  distribution's `cockpit-system` (on the runner the Cockpit of Ubuntu 24.04's archive, 314, the
  oldest of the tested systems) and what its bridge serves (`cockpit-bridge --packages`: the
  labels of the menu and the tools of the package `system`, after the overrides) to root and to a
  user of the test, `hsmenu`: `menu show` names what the bridge serves, as root and as `hsmenu`;
  `menu apply system` from a plan (Logs hidden, Services renamed and given an order) writes
  `/etc/cockpit/systemd.override.json` with exactly that (root, 0644), and the bridge serves it to
  both; refused and nothing written: that plan once more after the change (75), `apply system` as
  `hsmenu` (77), `apply user` as root (64); `menu apply user` run as `hsmenu` writes its own file
  (0644, `hsmenu`'s), whose name wins over the one for all users for `hsmenu` and does not reach
  root; an empty plan for all users removes the file, the backup folder holds the version before
  (0700, the file 0600), and the bridge serves root Cockpit's own menu again, `hsmenu` still its
  own name; an empty plan of `hsmenu` gives it Cockpit's own menu as well. The order of an entry
  is not in that listing: E2E-1 shows it in the navigation. Removing the package at the end
  takes the backups with the state folder, and an override file for all users set just before
  stays as it was.
- Job `upgrade` (`test/e2e/upgrade.sh`, once from each release named in the workflow): the
  update from a release a server may run, with ntfy and the stand-in add-on as in job `e2e`. The
  `.deb` of that release (`PREVIOUS_*`: 0.1.0, `LATEST_*`: 0.2.0, downloaded by
  `test/e2e/release-asset.sh`, which checks it against the SHA-256 pinned there and the release's
  `SHA256SUMS`) is set up as an admin does (ntfy, e-mail with nothing on `localhost:25`, the token
  on standard input, the timer on) and tells the stand-in's warning through ntfy only; then the
  package of this build goes over it with apt. `notify.conf`, the token and the timer stay,
  `heartbeat.conf` is there and empty (root, 0600), systemd runs the new units; the next run reads
  the old `last.json` on (no message, no "OK again", no new summary) and keeps the failure of
  e-mail from the old delivery without making ntfy confirmed; findings of the item told are taken
  as told when no more severe, a danger among them is told once and confirms ntfy; a target
  profile set afterwards states its gaps in one message and appears in the status as nobody; from
  0.2.0 the test goes back to it (it reads `last.json` on without a message and keeps the
  channels' records, and tells a change) and forth again (what 0.2.0 sent is taken in, the failure
  of e-mail keeps its beginning, ntfy stays confirmed); e-mail taken out of the settings is
  recorded as removed, not repaired; removing the package keeps `/etc/cockpit-host-settings` and
  removes the state; the token is in no other file and no log. Until the release that raises the
  version, this build carries the version of 0.2.0 itself: apt would see nothing to do, so
  `dpkg -i` installs one package over the other, and the test checks that the notifier installed
  is the one of the package meant.
- Job `e2e-el` (`tools/vm.sh` and `test/e2e/run.sh` with the `.rpm`, suite `e2e`; suite `upgrade`
  runs `test/e2e/upgrade.sh` with the `.rpm` of the previous release first): the same tests in a Rocky
  Linux 9 or 10 VM under KVM on the runner, from a cloud image pinned by name and SHA-256, with
  SELinux enforcing and auditd running (both checked), ntfy in Podman with `podman-docker`, postfix
  from the distribution. `vm.sh avc-mark` marks the audit log after the set-up, `vm.sh avc-check`
  fails on any `AVC`, `USER_AVC`, `SELINUX_ERR` or `USER_SELINUX_ERR` record after the mark (and
  when it cannot read the log or finds no records at all). Before that, `vm.sh avc-selftest`
  causes a denial on purpose (root entering `httpd_t` with `/usr/bin/true`) and fails unless the
  check catches it. The override file for all users of the menu step has the context SELinux gives
  its path (`restorecon -n -v` would change nothing), and the bridge reads it without a denial.
  Report, journal, audit log and console
  are in the artifacts `e2e-rocky-9`, `e2e-rocky-10`, `upgrade-rocky-9` and `upgrade-rocky-10`.
- E2E-1, the shared suite (`test/e2e-suite/`, below): Security Monitor, Host Settings and Status
  Board with their real packages on the runner, for every candidate of each of them and before
  every release.

### The shared suite (E2E-1)

**Who runs it.** The reusable workflow `.github/workflows/suite.yml` is the suite. Each of the
three product repositories calls it from a workflow of its own, `e2e-suite.yml`, on every pull
request, on every push to `main` and by hand:

| Repository | Caller | Which `suite.yml` |
|---|---|---|
| Host Settings | `.github/workflows/e2e-suite.yml` | `uses: ./.github/workflows/suite.yml`, the commit of the run |
| Status Board, Security Monitor | `.github/workflows/e2e-suite.yml` | `uses: Azentrius/cockpit-host-settings/.github/workflows/suite.yml@COMMIT` and `suite: COMMIT`, the same full commit |

The commit passed as `suite` gives the test (`test/e2e-suite/`) and the released combination
(`test/e2e-suite/suite-manifest.json`). For a caller other than Host Settings, job `plan` reads the
caller's workflow file at the run's commit and fails unless it pins `suite.yml` at exactly that
commit. On a pull request of Host Settings, `suite` is the pull request's head: GitHub loads
`suite.yml` itself from its test merge commit, the test, the manifest and the product from the
head.

**The released combination** (`suite-manifest.json`): for each product its repository, tag, full
commit and, for now informational, the package of the release (name, version, `.deb` and its
SHA-256), and `required_scenarios` (below). At present Status Board 0.4.0, Host Settings 0.3.0 and
Security Monitor 0.4.0, the commits of their tags. After a release: change the manifest in a pull
request of Host Settings, then move the pin in the callers of Status Board and Security Monitor
to the merged commit, each in a pull request of its own.

**The plan** (job `plan`, `plan.py`) is made once, before anything is built. Each product's
commit is, in this order: the commit given as input (`workflow_dispatch` of Host Settings'
`e2e-suite.yml`, or `with:` of a caller), the calling repository's own candidate, or the commit of
the released combination. The candidate of a pull request is its **head commit**, not GitHub's
test merge commit (which nobody can build again later); whether it still fits `main` after other
merges is what the push run on `main` checks after the squash merge. On a push or a manual run the
candidate is the commit of the run. No job resolves a branch: job `suite` checks out exactly the
three commits of the plan, and `manifest.py` fails when a checkout is not the commit the plan
named.

A pull request whose changed files are all text (`*.md` except `docs/STATUS-CONTRACT.md`,
`docs/img/`, `LICENSE`, `NOTICE`) does not need the suite: `plan` says so and job `suite` is
skipped. Everything else runs it: producer, parser, profile, notifier, contract, units, packaging
and the workflows. A push to `main` and a manual run always run it.

**An explicit combination.** Candidates that belong together, such as pull requests in two
repositories, are tested together by hand: Actions › e2e-suite › Run workflow in Host Settings,
on the branch whose test should run, with a full commit for each product that is not to come from
the manifest; an empty Host Settings input means the commit of the run.

**Job `gate`** always runs and is the check to require (`e2e-suite / gate`): it passes when job
`suite` passed, or when the plan skipped it for a pull request that changes only text; it fails
when the plan failed, or the suite failed, was cancelled or was skipped for any other reason.

**The steps.** `setup.sh` installs the three packages with apt and adds Cockpit with an
administrator and a user without administrative access, ntfy, UFW with 5432/tcp, 6379/tcp and
3306/tcp allowed, a public address on a dummy interface and a second host for the board (a
container with SSH and Host Settings, whose profile expects Security Monitor). `core.py` (steps 1
to 9) opens real listeners the scan rates a danger from the internet and checks the messages,
`last.json` and the coverage through two findings, a repeat in German and in another order, a
resolved finding, a packet filter listing that fails inside the scan's unit (partial, nothing
resolved), the hour against flapping (by moving a time in `last.json`, test only) and two broken
answers of a stand-in for Security Monitor's status verb. Then step 11: the stand-in ends with
exit 77 (no access), one "Check no longer possible", nothing resolved, and one "Check possible
again" with the real answer. Step 12 (H1): the profile expects `security/suite-unreported`, which
Security Monitor never reports: one message "the expected check is not reported", the gap with its
check id in `last.json`, nothing more on an unchanged run. Step 13 (H5): e-mail set up next to
ntfy with no mail server; a new finding (3306/tcp) reaches ntfy once and fails on e-mail; the
failure stays in `last.json`, in the status item `mail` and in the run's outcome through a run
with nothing to send; a mail server (`smtp_sink.py`) and "Send a test message" end it once; the
next run sends nothing to either channel. `ui.py` (step 10, last) checks the board's tasks per
host, the jump to the port in Security Monitor's open frame, the expected gap with "Only what
needs attention", that a user without administrative access sees no details and, with H1, the
gap of `suite-unreported` as a task and in the coverage line.

Then, with a browser of its own and also when the board's part could not run, `ui.py` tests the
menu editor in that Cockpit (scenario `menu`), against Cockpit's own pages from `cockpit-system`
(the backports' Cockpit). The administrator, in English, with administrative access: the
navigation has Logs and Services; the Cockpit tab (`/host-settings#/cockpit`) is on "For all
users" and lists each entry of the navigation under the name the navigation shows; Logs hidden
(greyed in its place), Services renamed and moved past the entry next to it, saved through the
dialog (`menu apply system`): `/etc/cockpit/systemd.override.json` holds exactly that. "Reload
Cockpit" reloads the whole shell from the page's frame (`window.top`), and the navigation has no
Logs and Services under its new name in its new place. The user without administrative access,
in German: the navigation shows the change for all users; the tab has no choice of scope ("Nur für
mich"), shows Cockpit's own labels translated exactly as the navigation does, with
`../manifests-i18n.js` answering the page's frame with 200, and Logs without a button; Services
renamed just for this user and saved (`menu apply user`, as the user): the user's own file
(0644, the user's) holds only that, and after "Reload Cockpit" the user's navigation shows that
name. The administrator again: still the name for all users. Empty plans of the helper remove
both files (at the end also after a failure), and after a reload the navigation is the one from
before. No JavaScript error and no failed request of Host Settings' files.

Steps 12 and 13 and the board's part of H1 need products with those rules (`hslib.not_reported_checks`,
`hschannels.py`, `notReportedChecks` in Status Board's `lib/status.js`). Without them each notes
that it did not check the rule. Status Board 0.4.0 and Host Settings 0.3.0 have them, so the
manifest's `required_scenarios` lists `h1` and `h5`, and a candidate without them fails. The same
holds for the menu editor (`hsmenu.py` and the Cockpit tab): Host Settings 0.3.0 does not have it,
so the scenario `menu` runs for a candidate of Host Settings that has it and is noted for the
released combination; once a release with the editor is in the manifest, `required_scenarios`
can list `menu`.

**The artifact** `e2e-suite` (no credential: the job looks for the test's tokens and passwords in
it and fails on a find): `plan.json`; `manifest.json` and `manifest.md` with the commit of the
test and its combination, the run, and for each product its source (`candidate`, `input` or
`manifest` with the tag), the commit asked for and the one checked out, the package built
(name, version, file, SHA-256) and, for a released commit, whether the `.deb` has the bytes of the
release; `result.json` and `result.md` (the combination, each part's checks and failures, the
scenarios); `core/` and `ui/` with the assertions, the status answers, the messages, `last.json`
at each step and the screenshots; `logs/`.

**Releases.** The release workflow of each of the three repositories requires the push run of
`e2e-suite` on the commit it releases: completed with success, its jobs `e2e-suite / E2E-1 (real
components)` and `e2e-suite / gate` successful, and the version manifest in its artifact naming
this repository at exactly that commit as the candidate, without a problem. A test before the
version bump is no proof for the commit with the bump.
