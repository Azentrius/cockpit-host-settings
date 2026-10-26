# Changelog

## 0.2.0 – 2026-10-26

Each finding is told on its own, a target profile says what a server is expected to check, and an
external watchdog notices when the notifier itself stops.

- **Each finding on its own:** where an add-on lists single findings (Security Monitor 0.4.0 does
  for every port the internet reaches), the notifier compares them one by one, judged as Status
  Board does. A second dangerous port in an item that is a danger already gets its own message
  ("Danger · Security Monitor · 6379/tcp"). A finding is "OK again" only when a complete list,
  from a check done in full and in time, no longer has it; a check done in part, failed, without
  access or out of date resolves nothing. An accepted finding (`accepted_until`) stays quiet, the
  end of its acceptance is told once, and a finding back within 30 days is told as "again".
  Add-ons that list no findings are compared per item as before.
- **Target profile:** `/etc/cockpit-host-settings/profile.conf` names the server's roles (`web`,
  `database`, `build`, `bastion`), the add-ons and checks it is expected to have besides, and
  checks left out on purpose, each with a reason and perhaps an end date. It is set up in a new
  section of the Notifications tab (administrative access) or with the helper's `profile` verbs,
  written line by line with a backup. Host Settings sends it in its status, where Status Board
  0.3.0 reads it for this server.
- **Coverage messages:** with a profile, one message "Check no longer possible" when an expected
  add-on or one of its checks failed, has no access, is not set up, cannot be read or is not
  installed, and one "Check possible again" once it is checked again. The first run after a
  profile is set up names its gaps once. Without a profile nothing changes.
- **Watchdog:** optional and off by default. After every run, a signal to Healthchecks or to a
  push monitor of Uptime Kuma: success only once the run is done and its state saved, failure
  when a message did not reach a channel set up (the run still exits 0) or the run could not
  finish. A notifier that hangs, is killed or whose timer is off sends nothing, so the watchdog
  raises the alarm. A failed signal never resets a finding or sends a message again. The address
  is a secret: it goes to the helper on standard input, lives in
  `/etc/cockpit-host-settings/heartbeat.conf` (root, 0600), reaches the notifier as a systemd
  credential and is never shown. `https://` with the usual checks, plain `http://` only to a
  private address; no redirects; at most 3 tries within 40 seconds. A watchdog alone is enough to
  switch on the regular check, and the status has an item `heartbeat`. Tested against Uptime Kuma
  2.5.7; Healthchecks only against a stand-in that gives its documented answers.
- **Fix, OK again without a check:** an item in state `none`, exit 77 and an answer that left
  items out were told as "OK again". They now keep what was told. An add-on removed while it had
  told problems gives one "No longer checked" instead of an "OK again" per finding.
- **Fix, broken findings data:** a findings list that breaks the rules was left out and the item
  compared as a whole, so an open finding was closed and told as "OK again". Now broken or missing
  findings data, also the loss of the whole part or going back to an add-on version without
  findings, keeps the known findings as they are (open or accepted) until a readable list comes.
  An item with broken data can get worse, never better, and the other items of the answer are
  compared as usual.
- **Fix, complete lists:** a list of findings is complete only when its own `total` agrees, as on
  Status Board 0.3.0: `"complete": true, "total": 1, "list": []` is never complete. The shared
  answers of the board's tests are copied byte for byte.
- **`last.json`** keeps format 1. A file of 0.1.0 is read as before: no new summary and no message
  again after the update, and findings no more severe than what was told for their item are taken
  as told.
- **Menu:** the entry moves to the end of System, in one block with the other Azentrius add-ons.
  Searching the menu for "azentrius" finds them all, and Cockpit's Help menu links the README.
- **Systems:** the `.rpm` is installed and checked on Rocky Linux and AlmaLinux 9 and 10, and the
  end-to-end test runs in Rocky Linux 9 and 10 VMs with SELinux enforcing as well.
- **Install:** the README and the release notes name the package repository first again.
- **Tested together:** Status Board 0.3.0, Host Settings 0.2.0 and Security Monitor 0.4.0. Single
  findings of exposed ports need Security Monitor 0.4.0 or newer, the target profile on the board
  Status Board 0.3.0 or newer.
- **Older add-ons:** the status contract stays at schema 1 and the new parts are optional. Add-ons
  in older versions still work and are compared per item as before; Status Board shows their items
  as "summary only".

## 0.1.0 – 2026-10-23

First version: messages through ntfy, e-mail or both when an Azentrius add-on on a server needs
attention.

- **The notifier** reads the status verbs of the Azentrius add-ons on its server every 5 minutes
  (`cockpit-host-settings-notify.timer`, off until set up from the page), judges every answer as
  Status Board does, and sends a message when a finding turns to a warning or a danger, when a
  danger becomes a warning, and when it is OK again. It compares every finding on its own, so a
  second finding in an add-on that is already a warning is told as well; the same change of the
  same finding comes at most once an hour. An add-on whose status cannot be read is a finding of
  its own. The first run after setting up sends a summary of what the add-ons report then.
- **Channels:** ntfy (any server; JSON to its root URL, the token as a header) and e-mail through
  the local mail server on `localhost:25`. A message that could not go out anywhere is sent
  again on the next run. Messages hold only what the add-ons report, their names and the
  server's name, in English or German.
- **The page** (tab Notifications) sets up the channels, the language and the regular check, and
  shows the add-ons as last read. For ntfy it shows the commands the ntfy server needs for this
  server's user, topic and token (Host Settings does not set up the server), and "Check
  connection": reachable, encrypted, the token may publish, the topic is closed to anyone
  without a login (read once a day by the timer as well; the try to send without a login only on
  request), and what the token may do.
- **No root for the notifier:** it runs as the system user `cockpit-host-settings` and gets the
  ntfy token from systemd as a credential (`LoadCredential=`, from
  `/etc/cockpit-host-settings/ntfy.token`, root, 0600). The page hands the token to the helper on
  standard input and never shows it.
- **Status contract:** Host Settings reports its own state (channels, last message, protection of
  the topic, last run) to Status Board and as the icon in Cockpit's menu.
- **Tested** in CI with the package and its real units against ntfy 2.32 and postfix, on Python
  3.8 and 3.12, installed on Debian 13 and Fedora 43, and the page in a browser in both themes,
  both languages and at phone width.
