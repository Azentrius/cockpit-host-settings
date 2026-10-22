# Changelog

## 0.1.0 – Unreleased

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
