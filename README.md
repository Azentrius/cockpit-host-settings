# Host Settings

A [Cockpit](https://cockpit-project.org) add-on that sends a message through
[ntfy](https://ntfy.sh), e-mail or both when one of the Azentrius add-ons on a server needs
attention: when a finding turns to a warning or a danger, and when it is OK again.

Host Settings is installed on every server. Its notifier reads, every 5 minutes, what the
Azentrius add-ons of that server report through the status contract they share
([Cron Jobs](https://github.com/Azentrius/cockpit-cronjobs),
[Image Update Monitor](https://github.com/Azentrius/cockpit-image-update-monitor),
[Update Gaps](https://github.com/Azentrius/cockpit-update-gaps),
[Security Monitor](https://github.com/Azentrius/cockpit-security-monitor) and any other add-on with
`azentrius.status` in its manifest), and tells what changed. The page sets up the channels and
checks that the way there is safe.

The add-on has one tab so far, Notifications. A second one for Cockpit's own settings
(`/etc/cockpit/cockpit.conf`) is planned.

## What it does

- **Reads every add-on's status** as Status Board does: the same checks of every answer, the same
  states, an item past its `stale_after` counts as a warning, an add-on whose status cannot be read
  is a warning of its own.
- **Tells changes, finding by finding:** a finding that turns to a warning or a danger, a danger
  that becomes a warning, a finding that is OK again or no longer reported. A second finding in an
  add-on that is already a warning is told as well. The same change of the same finding comes at
  most once an hour, so a flapping finding does not flood the phone. The first run after setting
  up sends a summary of what the add-ons report then.
- **ntfy:** any ntfy server, with a token (recommended) or without. Danger comes with a high
  priority, warnings with the default one, "OK again" with a low one.
- **E-mail:** handed to the mail server of the server itself on `localhost:25` (postfix or exim,
  for example), which delivers or relays it.
- **Checks the way to ntfy** ("Check connection"): the server answers, the connection is
  encrypted, the token may publish, the topic is closed to anyone without a login (reading, and on
  request sending), and the token may do no more than it needs.
- **Shows the commands for the ntfy server:** the user, its write access to the topic and the
  token this server needs, ready to copy. Host Settings does not set up the server itself.
- **Reports its own state** through the status contract: Status Board shows whether messages are
  set up, whether the last one went out, whether the topic is closed, and when the notifier last ran;
  Cockpit's menu shows it as an icon.

## What it does not do

- It makes no checks of its own: it says only what the add-ons say, and only on its own server.
- Nothing comes while the server is off or cannot reach the ntfy server or its mail server. Status
  Board on the bastion shows such a server as unreachable.
- It does not install, configure or run an ntfy server.
- E-mail needs a mail server that accepts mail on `localhost:25`. A `sendmail` command alone
  (nullmailer, msmtp, dma) is not used yet.
- It holds no settings of the other add-ons; each keeps its own.

## Messages

A message holds only what the add-ons report, their names and the server's name, in English or
German (a setting on the page). For example:

```
web1: Cron Jobs, Failed runs

Danger · Cron Jobs · Failed runs (2)
backup, mail

Host Settings on web1
```

Several changes in one run come as one message ("web1: 3 changes"). A message that could
not go out on any channel is sent again on the next run.

## Security model

- **The notifier never runs as root.** `cockpit-host-settings-notify.service` runs as the system
  user `cockpit-host-settings`, hardened (`systemd-analyze security`: 1.5), and may write only its
  state in `/var/lib/cockpit-host-settings`. The status verbs of the add-ons work without root, as
  the status contract asks; a status command runs only when it lies below `/usr/lib/cockpit-NAME/`
  and it and its folders belong to root and nobody else can change them.
- **The token** goes from the page to the helper on standard input, lands in
  `/etc/cockpit-host-settings/ntfy.token` (root, 0600), and reaches the notifier as a systemd
  credential (`LoadCredential=`). It leaves the server only as the `Authorization` header of a
  request to the ntfy server set up; redirects are not followed. The page never shows it.
- **The page** changes something only through its dialog, with administrative access, and calls
  only the fixed verbs of `/usr/lib/cockpit-host-settings/host-settings.py`. Everything an add-on
  reports reaches the page and the messages as text, cut and cleaned of control and bidirectional
  characters.
- **Sending without a login** is tried only when you press "Check connection" (the page says so
  first); should the topic accept it, that message itself is the warning.

## Requirements

- Cockpit 264 or newer, Python 3.8 or newer, systemd 247 or newer (`LoadCredential=`), `ss`
  (iproute2).
- For e-mail: a mail server listening on `localhost:25`.
- For ntfy: an ntfy server the server can reach. With `auth-default-access: deny-all` on that
  server and a token for this server's user, nobody else can read or send to its topic.

## Install

After the first release, from the [Azentrius package repository](https://packages.azentrius.com)
(Debian, Ubuntu, Fedora): set up the repository once, then install Host Settings in Cockpit under
Applications, or:

```bash
sudo apt install cockpit-host-settings
```

The package creates the user `cockpit-host-settings`, an empty token file and the state folder.
The regular check stays off until it is set up on the page.

## Setting up

1. **ntfy:** "Set up ntfy" with the server's address and a topic (the page suggests
   `cockpit-<server name>`). On the ntfy server, run the commands the page shows:

   ```bash
   ntfy user add cockpit-web1
   ntfy access cockpit-web1 cockpit-web1 write-only
   ntfy token add --label="Host Settings web1" cockpit-web1
   ```

   and set the token on the page. For the ntfy app, a user that may read the topics of every
   server: `ntfy access READER 'cockpit-*' read-only`. "Check connection" then shows whether
   everything is as it should be.
2. **E-mail:** "Set up e-mail" with a recipient, then "Send a test message".
3. **The regular check:** "Set up the regular check". The first run sends the summary.

## Operation and troubleshooting

- The state of the last run: the page, or `sudo /usr/lib/cockpit-host-settings/host-settings.py show`.
- The log: `journalctl -u cockpit-host-settings-notify.service`.
- A run now: "Run now" on the page, or `sudo systemctl start cockpit-host-settings-notify.service`.
- The status for the board: `/usr/lib/cockpit-host-settings/host-settings.py status --lang en`.
- Switching off: "Switch off" on the page (`host-settings.py remove timer`); Status Board then shows
  Host Settings as switched off.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md); interfaces, formats and flows are in
[docs/INTERNALS.md](docs/INTERNALS.md).

## License

[Apache License 2.0](LICENSE). Third-party material (fonts, icons) keeps its own license, see
[NOTICE](NOTICE).
