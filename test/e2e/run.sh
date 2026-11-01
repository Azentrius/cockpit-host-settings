#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# The end-to-end test of Host Settings, on a throw-away machine with systemd and Docker (CI, job
# e2e: the runner itself with the .deb; job e2e-el: a Rocky Linux VM with SELinux enforcing and
# the .rpm, Podman standing in for Docker; never on a server): the package with its real units, a
# real ntfy server, postfix for local mail and a stand-in add-on whose state the test switches.
#
#   sudo env OUT=e2e-session sh test/e2e/run.sh dist/cockpit-host-settings_*.deb
#   sudo env OUT=e2e-session sh test/e2e/run.sh dist/cockpit-host-settings-*.rpm
#
# What it shows (docs/INTERNALS.md, "Tests"):
#   - the commands the page shows for the ntfy server run there exactly as shown, and give a
#     token (concept: what ntfy answers is only read from its documentation so far);
#   - "Check connection" against a topic closed without a login and against an open one: the
#     HTTP answers ntfy really gives (401 or 403), and that the write-only token cannot read;
#   - the first run sends the summary through ntfy and e-mail, a change sends one message, a run
#     without a change none; once the stand-in lists findings, a second finding sends one message
#     although its item stays a danger; a target profile states its gaps in one message, and the
#     stand-in it expects going away sends one more; the status verb answers as nobody, with the
#     profile; switching off says so;
#   - the watchdog (test/e2e/watchdog.py, a stand-in for Healthchecks and Uptime Kuma): a success
#     after a run, a failure when a message did not reach every channel although the run ends with
#     0 and when the settings cannot be read, no signal from a run that loses the lock, answers
#     that say no (an unknown check, a redirect); the timer stopped, the signals stay away, the
#     timer started again, exactly one recovery, for both protocols; with KUMA_IMAGE (job e2e) the
#     same against a real Uptime Kuma in a container, its monitor made through its own API;
#   - each channel on its own: ntfy takes a message while postfix is stopped; e-mail stays a
#     failure in last.json, the status and the watchdog's signal through runs with nothing to
#     send, also once postfix is back, and no message goes to ntfy twice; "Send a test message"
#     ends it, and the next run is exactly one recovery (ntfy the same way, through "Check
#     connection" after its address was set back);
#   - the token and the watchdog's address are in no file but their own, in no log and in no
#     process's arguments.
# Writes OUT/report.md and the raw answers. Exit 1 at the first step that fails.
set -eu
pkg=${1:?usage: run.sh PACKAGE.deb|PACKAGE.rpm}
case $pkg in
    *.deb) kind=deb ;;
    *.rpm) kind=rpm ;;
    *) echo "run.sh: a .deb or an .rpm, not $pkg" >&2; exit 64 ;;
esac
out=${OUT:-e2e-session}
mkdir -p "$out"
here=$(cd "$(dirname "$0")" && pwd)
report=$out/report.md
helper=/usr/lib/cockpit-host-settings/host-settings.py
# The version the Azentrius servers run; raise both together. With the registry,
# as Podman takes no short names without asking.
ntfy_image=docker.io/binwiederhier/ntfy:v2.32.0
base=http://127.0.0.1:8090
# The stand-in watchdog (test/e2e/watchdog.py) and its folder; the IDs of its checks are secrets
# of the test.
dog=http://127.0.0.1:8091
beat_dir=/tmp/hs-e2e-watchdog
hc_id=
kuma_id=
kuma_token=
step=0
echo "# Host Settings: end-to-end" > "$report"

ok() { echo "- ok: $*" | tee -a "$report"; }
fail() { echo "- FAILED: $*" | tee -a "$report"; exit 1; }
next() { step=$((step + 1)); printf '\n## %s. %s\n\n' "$step" "$*" | tee -a "$report"; }
# The JSON after "RESULT " in the output of check/test.
result() { sed -n 's/^RESULT //p'; }
field() { python3 -c "import json, sys; d = json.load(sys.stdin); print(eval(sys.argv[1], {}, {'d': d}))" "$1"; }
ntfy() { docker exec "$@"; }
# What the stand-in watchdog saw of a check (check-1: Healthchecks, check-2: Uptime Kuma): a count
# of success, failure, log, missed or recoveries.
seen() { curl -fsS "$dog/_status" | python3 -c "import json, sys; print(json.load(sys.stdin)[sys.argv[1]][sys.argv[2]])" "$1" "$2"; }
# No process holds a watchdog address in its arguments (the IDs reach python3 in the environment).
args_clean() {
    HS_IDS="$hc_id $kuma_id $kuma_token" python3 -c '
import os
ids = os.environ["HS_IDS"].split()
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        with open("/proc/%s/cmdline" % pid, "rb") as f:
            args = f.read().decode("utf-8", "replace")
    except OSError:
        continue
    if any(i in args for i in ids):
        raise SystemExit("a watchdog address in the arguments of process " + pid)'
}
# Waits up to SECONDS until seen CHECK KEY is at least WANT; looks at the processes meanwhile.
wait_seen() {
    i=0
    while :; do
        n=$(seen "$1" "$2") || return 1
        [ "$n" -lt "$4" ] || return 0
        i=$((i + 1))
        [ "$i" -le "$3" ] || return 1
        args_clean || fail "a watchdog address in the arguments of a process"
        sleep 1
    done
}
# The watchdog's last answer as the notifier saved it (heartbeat.json, root only).
last_beat() { python3 -c "import json, sys; d = json.load(open(sys.argv[1])); print(eval(sys.argv[2], {}, {'d': d}))" /var/lib/cockpit-host-settings/heartbeat.json "$1"; }
# A field of last.json (the notifier's, 0600; root reads it).
last() { python3 -c "import json, sys; d = json.load(open(sys.argv[1])); print(eval(sys.argv[2], {}, {'d': d}))" /var/lib/cockpit-host-settings/last.json "$1"; }
# The state of an item of the status, as nobody asks for it.
item_state() { runuser -u nobody -- "$helper" status | field "[i['state'] for i in d['items'] if i['id'] == '$1']"; }
# Whether something accepts connections on localhost:25 (yes or no), and waiting up to 20 seconds
# for WANT.
port25() { python3 -c 'import socket; s = socket.socket(); s.settimeout(2); print("no" if s.connect_ex(("127.0.0.1", 25)) else "yes")'; }
wait_port25() {
    for _ in $(seq 1 20); do [ "$(port25)" = "$1" ] && return 0; sleep 1; done
    return 1
}
# postfix off and on again. On Debian and Ubuntu postfix.service only stands for the instance
# postfix@-.service, which does the work; on the RHEL rebuilds postfix.service is the server.
mta_stop() { systemctl stop postfix 'postfix@*' 2> /dev/null || true; }
mta_start() {
    systemctl start postfix
    if systemctl cat postfix@-.service > /dev/null 2>&1; then systemctl start postfix@-.service; fi
}
# A test signal through the page's helper to the watchdog set up now: its answer.
beat_test() {
    "$helper" heartbeat test > "$out/beat-test.txt" 2>&1 || { cat "$out/beat-test.txt"; fail "heartbeat test"; }
    result < "$out/beat-test.txt" | field "d['result']['reason']"
}
# The watchdog set to ADAPTER and URL, the URL on standard input as the page sends it.
beat_set() {
    printf '%s\n' "$2" | "$helper" heartbeat set "$1" > "$out/beat-set.txt" 2>&1 || { cat "$out/beat-set.txt"; fail "heartbeat set $1"; }
}
# Every message the reader can see on a topic, one JSON per line.
messages() { curl -fsS -H "Authorization: Bearer $reader" "$base/$1/json?poll=1"; }
# What a step checks, before it checks it.
note() { echo "  $*" | tee -a "$report"; }
# One run of the notifier through the page's helper, its output in OUT/NAME.txt. The steps start
# the service more often than systemd's start limit allows (5 starts in 10 seconds; on a server
# the timer starts it every 5 minutes), so the counter is reset first.
run_notifier() {
    systemctl reset-failed cockpit-host-settings-notify.service 2> /dev/null || true
    "$helper" run > "$out/$1.txt" 2>&1 || { cat "$out/$1.txt"; fail "run of the notifier ($1)"; }
}

next "ntfy: deny-all, a user with write access to its topic only, a reader"
# The server's databases live in /var/lib/ntfy, which the image does not have: a folder of the host.
mkdir -p /tmp/ntfy-e2e/lib
cat > /tmp/ntfy-e2e/server.yml <<'EOF'
base-url: "http://127.0.0.1:8090"
auth-file: "/var/lib/ntfy/user.db"
auth-default-access: "deny-all"
cache-file: "/var/lib/ntfy/cache.db"
EOF
# Z: SELinux labels the folders for the container (Podman on the RHEL rebuilds); Docker without
# SELinux ignores it.
docker run -d --name ntfy -p 127.0.0.1:8090:80 -v /tmp/ntfy-e2e/server.yml:/etc/ntfy/server.yml:ro,Z \
    -v /tmp/ntfy-e2e/lib:/var/lib/ntfy:Z "$ntfy_image" serve > /dev/null
for _ in $(seq 1 30); do curl -fsS "$base/v1/health" > /dev/null 2>&1 && break; sleep 1; done
if ! curl -fsS "$base/v1/health" > "$out/health.json"; then
    docker logs ntfy 2>&1 | tail -n 20
    fail "ntfy did not start"
fi
ok "ntfy $(docker exec ntfy ntfy --help 2>/dev/null | head -n 1 || true) answers: $(cat "$out/health.json")"

# Exactly the commands the page shows (plugin/settings.js, commandsText), for the topic cockpit-e2e.
topic=cockpit-e2e
pw=$(openssl rand -hex 16)
ntfy -e NTFY_PASSWORD="$pw" ntfy ntfy user add "$topic" > "$out/user-add.txt" 2>&1 || fail "ntfy user add (password from NTFY_PASSWORD)"
ntfy ntfy ntfy access "$topic" "$topic" write-only > "$out/access.txt" 2>&1 || fail "ntfy access TOPIC TOPIC write-only"
ntfy ntfy ntfy token add --label="Host Settings e2e" "$topic" > "$out/token-add.txt" 2>&1 || fail "ntfy token add"
token=$(grep -o 'tk_[A-Za-z0-9]*' "$out/token-add.txt" | head -n 1)
[ -n "$token" ] || fail "no token in the output of ntfy token add"
sed -i "s/$token/tk_REDACTED/g" "$out/token-add.txt"
ok "the page's three commands ran as shown; the token starts with tk_"
ntfy -e NTFY_PASSWORD="$pw" ntfy ntfy user add reader > /dev/null 2>&1
ntfy ntfy ntfy access reader 'cockpit-*' read-only > /dev/null 2>&1
reader=$(ntfy ntfy ntfy token add reader 2>&1 | grep -o 'tk_[A-Za-z0-9]*' | head -n 1)
[ -n "$reader" ] || fail "no token for the reader"
ok "reader with read-only access to cockpit-*"

next "postfix for local mail, a stand-in add-on, the package"
if [ "$kind" = deb ]; then
    echo "postfix postfix/main_mailer_type select Local only" | debconf-set-selections
    echo "postfix postfix/mailname string e2e.localdomain" | debconf-set-selections
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postfix > /dev/null
else
    # The rebuilds' postfix delivers locally and listens on localhost only as it comes.
    dnf install -y -q postfix > /dev/null
    systemctl enable -q postfix
fi
useradd --create-home hsmail 2> /dev/null || true
systemctl restart postfix
mkdir -p /usr/share/cockpit/e2e-addon /usr/lib/cockpit-e2e-addon /var/lib/e2e-addon
cat > /usr/share/cockpit/e2e-addon/manifest.json <<'EOF'
{"menu": {"index": {"label": "E2E Add-on"}}, "azentrius": {"status": ["/usr/lib/cockpit-e2e-addon/status", "status"]}}
EOF
# The status verb of the stand-in: its one item takes the state written to /var/lib/e2e-addon/state.
# With /var/lib/e2e-addon/findings (a JSON list), the item lists those findings, and a check of
# state "checked" says the list is complete.
cat > /usr/lib/cockpit-e2e-addon/status <<'EOF'
#!/bin/sh
state=$(cat /var/lib/e2e-addon/state 2>/dev/null || echo ok)
now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ext=''
list=''
if [ -s /var/lib/e2e-addon/findings ]; then
    ext=',"features":["checks","findings"],"checks":[{"id":"thing","state":"checked","title":"The thing","item":"thing"}]'
    list=$(printf ',"findings":{"complete":true,"list":%s}' "$(cat /var/lib/e2e-addon/findings)")
fi
printf '{"schema":1,"addon":"e2e-addon","label":"E2E Add-on","version":"1.0.0","checked_at":"%s","state":"%s","summary":"s"%s,"items":[{"id":"thing","state":"%s","title":"The thing","value":"1","checked_at":"%s","stale_after":3600%s}]}\n' "$now" "$state" "$ext" "$state" "$now" "$list"
EOF
chmod 755 /usr/lib/cockpit-e2e-addon/status
echo warning > /var/lib/e2e-addon/state
chmod 644 /var/lib/e2e-addon/state
if [ "$kind" = deb ]; then
    apt-get install -y -qq "./$pkg" > /dev/null || apt-get install -y -qq "$pkg" > /dev/null
    installed=$(dpkg-query -W -f '${Package} ${Version}' cockpit-host-settings)
else
    dnf install -y -q "$pkg" > /dev/null
    installed=$(rpm -q cockpit-host-settings)
fi
getent passwd cockpit-host-settings > /dev/null || fail "no user cockpit-host-settings"
# The distribution's Cockpit takes the page's manifest.
cockpit-bridge --packages > "$out/packages.txt" 2>&1 || fail "cockpit-bridge --packages"
grep -qw host-settings "$out/packages.txt" || fail "Cockpit does not list host-settings: $(cat "$out/packages.txt")"
ok "postfix (local only), the stand-in add-on in warning, $installed, listed by Cockpit"

next "the settings through the helper, the token on standard input"
"$helper" set ntfy "$base" "$topic"
"$helper" set mail hsmail@localhost
printf '%s\n' "$token" | "$helper" token set
test "$(stat -c '%a %U' /etc/cockpit-host-settings/ntfy.token)" = "600 root" || fail "mode of ntfy.token"
ok "ntfy, e-mail, token (0600 root)"

next "Check connection against the closed topic"
"$helper" check ntfy > "$out/check-closed.txt" 2>&1 || { cat "$out/check-closed.txt"; fail "check ntfy"; }
result < "$out/check-closed.txt" > "$out/check-closed.json"
for s in reach publish anon-read anon-write token; do
    printf '  %s: %s\n' "$s" "$(field "[(x['result'], x['reason'], x['code']) for x in d['steps'] if x['id'] == '$s'][0]" < "$out/check-closed.json")" | tee -a "$report"
done
[ "$(field "d['access']['state']" < "$out/check-closed.json")" = ok ] || fail "the closed topic is not judged closed"
[ "$(field "[x['result'] for x in d['steps'] if x['id'] == 'publish'][0]" < "$out/check-closed.json")" = ok ] || fail "the test message with the token"
[ "$(field "[x['result'] for x in d['steps'] if x['id'] == 'token'][0]" < "$out/check-closed.json")" = ok ] || fail "the write-only token is not judged write-only"
[ "$(messages "$topic" | grep -c 'test message')" -ge 1 ] || fail "the test message did not arrive"
ok "closed: reading and sending without a login refused, the token may only write"

next "the first run: the summary through ntfy and e-mail"
"$helper" install timer > "$out/install.txt" 2>&1 || { cat "$out/install.txt"; fail "install timer"; }
systemctl is-active --quiet cockpit-host-settings-notify.timer || fail "the timer is not on"
messages "$topic" > "$out/messages-1.json"
grep -q 'messages set up' "$out/messages-1.json" || fail "no summary on ntfy"
grep -q 'Warning · E2E Add-on · The thing' "$out/messages-1.json" || fail "the summary does not name the warning"
for _ in $(seq 1 20); do grep -qs 'messages set up' /var/mail/hsmail && break; sleep 1; done
grep -q 'messages set up' /var/mail/hsmail || fail "no summary by e-mail"
grep -q '^Auto-Submitted: auto-generated' /var/mail/hsmail || fail "the e-mail is not marked auto-generated"
ok "summary on ntfy and in /var/mail/hsmail"

next "a change: one message; no change: none"
echo danger > /var/lib/e2e-addon/state
run_notifier run-2
messages "$topic" > "$out/messages-2.json"
[ "$(grep -c 'Danger · E2E Add-on · The thing' "$out/messages-2.json")" = 1 ] || fail "the change was not told once"
run_notifier run-2-again
[ "$(messages "$topic" | wc -l)" = "$(wc -l < "$out/messages-2.json")" ] || fail "a run without a change sent a message"
ok "warning to danger told once, the next run told nothing"

next "findings: a second finding gives a second message"
# The item was told as a danger already: the finding it lists first is taken as told. The second
# one is told on its own, although the item stays a danger.
before=$(messages "$topic" | wc -l)
note "messages on the topic before: $before"
echo '[{"fid":"port.5432-tcp","state":"danger","title":"5432/tcp postgres"}]' > /var/lib/e2e-addon/findings
chmod 644 /var/lib/e2e-addon/findings
run_notifier run-3
count=$(messages "$topic" | wc -l)
note "after the first finding: $count messages (expected $before)"
[ "$count" = "$before" ] || fail "the finding behind the danger already told was told again"
echo '[{"fid":"port.5432-tcp","state":"danger","title":"5432/tcp postgres"},{"fid":"port.6379-tcp","state":"danger","title":"6379/tcp redis"}]' \
    > /var/lib/e2e-addon/findings
run_notifier run-4
messages "$topic" > "$out/messages-4.json"
count=$(wc -l < "$out/messages-4.json")
second=$(grep -c 'Danger · E2E Add-on · 6379/tcp redis' "$out/messages-4.json" || true)
first=$(grep -c '5432/tcp postgres' "$out/messages-4.json" || true)
note "after the second finding: $count messages (expected $((before + 1))); naming 6379/tcp redis: $second (expected 1); naming 5432/tcp postgres: $first (expected 0)"
[ "$count" = $((before + 1)) ] || fail "the second finding did not give exactly one message"
[ "$second" = 1 ] || fail "the second finding was not told"
[ "$first" = 0 ] || fail "the first finding was told"
run_notifier run-5
count=$(messages "$topic" | wc -l)
note "after a run without a change: $count messages (expected $((before + 1)))"
[ "$count" = $((before + 1)) ] || fail "a run without a change sent a message"
ok "the first finding taken as told, the second told once while the item stayed a danger, the next run told nothing"

next "a target profile: its gaps in one message, the stand-in it expects gone in one more"
# The stand-in OK again and its findings gone first (told, not counted here).
echo ok > /var/lib/e2e-addon/state
echo '[]' > /var/lib/e2e-addon/findings
run_notifier run-6
# No role: the add-ons of every host, none of which is installed here; and the stand-in.
{ "$helper" profile roles && "$helper" profile expect add e2e-addon &&
  echo "not installed in this test" | "$helper" profile skip add security/fail2ban; } > "$out/profile.txt" 2>&1 ||
    { cat "$out/profile.txt"; fail "profile roles, expect add, skip add"; }
if "$helper" profile roles mail > /dev/null 2>&1; then fail "an unknown role was taken"; fi
if echo r | "$helper" profile skip add security > /dev/null 2>&1; then fail "an add-on alone was left out"; fi
test "$(stat -c '%a %U' /etc/cockpit-host-settings/profile.conf)" = "644 root" || fail "mode of profile.conf"
before=$(messages "$topic" | wc -l)
run_notifier run-7
messages "$topic" > "$out/messages-7.json"
count=$(wc -l < "$out/messages-7.json")
gaps=$(grep -c 'Check no longer possible' "$out/messages-7.json" || true)
note "after setting the profile: $count messages (expected $((before + 1))); messages naming a check no longer possible: $gaps (expected 1)"
[ "$count" = $((before + 1)) ] || fail "the gaps of the new profile did not come as one message"
[ "$gaps" = 1 ] || fail "the gaps of the new profile were not told"
mkdir -p /var/lib/e2e-addon/off
mv /usr/share/cockpit/e2e-addon/manifest.json /var/lib/e2e-addon/off/
run_notifier run-8
messages "$topic" > "$out/messages-8.json"
count=$(wc -l < "$out/messages-8.json")
gone=$(grep -c 'Check no longer possible · E2E Add-on' "$out/messages-8.json" || true)
note "after the stand-in went away: $count messages (expected $((before + 2))); naming it: $gone (expected 1)"
[ "$count" = $((before + 2)) ] || fail "the expected add-on that went away did not give exactly one message"
[ "$gone" = 1 ] || fail "the expected add-on that went away was not told"
tail -n 1 "$out/messages-8.json" | grep -q 'the add-on is not installed' || fail "the message does not say why"
run_notifier run-9
count=$(messages "$topic" | wc -l)
note "after a run without a change: $count messages (expected $((before + 2)))"
[ "$count" = $((before + 2)) ] || fail "a run without a change sent a message"
ok "the profile's gaps in one message, the expected stand-in gone in one more, the next run told nothing"

next "the status verb as nobody, and for the board"
runuser -u nobody -- "$helper" status --lang de > "$out/status-de.json"
field "(d['state'], d['summary'])" < "$out/status-de.json" | tee -a "$report"
[ "$(field "d['addon']" < "$out/status-de.json")" = host-settings ] || fail "status verb"
field "(d['features'], d['profile']['roles'], d['profile']['expect'], d['profile']['skip'])" < "$out/status-de.json" | tee -a "$report"
[ "$(field "'profile' in d.get('features', []) and 'e2e-addon' in d['profile']['expect']" < "$out/status-de.json")" = True ] ||
    fail "the status as nobody does not name the profile"
ok "the status verb answers without root, with the target profile"

next "Check connection against an open topic"
ntfy ntfy ntfy access everyone cockpit-open rw > /dev/null 2>&1
ntfy ntfy ntfy access "$topic" cockpit-open write-only > /dev/null 2>&1
"$helper" set ntfy "$base" cockpit-open
"$helper" check ntfy > "$out/check-open.txt" 2>&1 || { cat "$out/check-open.txt"; fail "check ntfy (open)"; }
result < "$out/check-open.txt" > "$out/check-open.json"
[ "$(field "d['access']" < "$out/check-open.json")" = "{'state': 'warning', 'anon_read': 'warning', 'anon_write': 'warning'}" ] ||
    fail "the open topic is not judged open: $(cat "$out/check-open.json")"
ok "open: readable and writable without a login, both warnings"

next "the watchdog: a stand-in for Healthchecks and Uptime Kuma; the address a secret on standard input"
# The regular run every 5 seconds instead of every 5 minutes, for this test only: a drop-in that
# the end of the test removes. Stopped until the helper starts it.
systemctl stop cockpit-host-settings-notify.timer
mkdir -p /etc/systemd/system/cockpit-host-settings-notify.timer.d
printf '[Timer]\nOnCalendar=\nOnCalendar=*:*:0/5\nRandomizedDelaySec=0\nAccuracySec=1s\n' \
    > /etc/systemd/system/cockpit-host-settings-notify.timer.d/e2e.conf
systemctl daemon-reload
mkdir -p "$beat_dir"
hc_id=$(python3 -c 'import uuid; print(uuid.uuid4())')
kuma_id=$(openssl rand -hex 16)
printf '%s\n%s\n' "$hc_id" "$kuma_id" > "$beat_dir/ids"
# A check is missed after 15 seconds without a success: three runs of the test's timer.
python3 "$here/watchdog.py" 8091 "$beat_dir" 15 > "$beat_dir/stub.log" 2>&1 &
stub=$!
trap 'kill "$stub" 2> /dev/null || true' EXIT
for _ in $(seq 1 30); do curl -fsS "$dog/_status" > /dev/null 2>&1 && break; sleep 1; done
curl -fsS "$dog/_status" > /dev/null || { cat "$beat_dir/stub.log"; fail "the stand-in watchdog did not start"; }
beat_set healthchecks "$dog/ping/$hc_id"
test "$(stat -c '%a %U' /etc/cockpit-host-settings/heartbeat.conf)" = "600 root" || fail "mode of heartbeat.conf"
if grep -qs -e "$hc_id" "$out/beat-set.txt" /etc/cockpit-host-settings/notify.conf; then fail "the address in the output or in notify.conf"; fi
got=$(beat_test)
note "test signal: $got (expected accepted); log entries: $(seen check-1 log) (expected 1), successes: $(seen check-1 success) (expected 0)"
[ "$got" = accepted ] || fail "the test signal was not taken"
[ "$(seen check-1 log)" = 1 ] && [ "$(seen check-1 success)" = 0 ] || fail "the test signal is not Healthchecks' log entry"
run_notifier beat-1
note "after a run: successes $(seen check-1 success) (expected 1); $(grep 'watchdog:' "$out/beat-1.txt" || true)"
[ "$(seen check-1 success)" = 1 ] || fail "no success signal after a run"
args_clean || fail "a watchdog address in the arguments of a process"
ok "the address only on standard input and in heartbeat.conf (0600 root); a test is a log entry; a run gives one success"

next "the watchdog: failures, although the run ends with 0; no signal from a run that loses the lock"
# ntfy unreachable and a change to tell (the stand-in add-on back): the message reaches e-mail
# only. The run ends with 0, as before; the watchdog is told of the failure.
"$helper" set ntfy http://127.0.0.1:9 cockpit-open > /dev/null
mv /var/lib/e2e-addon/off/manifest.json /usr/share/cockpit/e2e-addon/
run_notifier beat-2
journalctl --no-pager -o cat -u cockpit-host-settings-notify.service | grep -E 'message:|watchdog:' | tail -n 2 > "$out/beat-2-journal.txt"
note "$(tr '\n' ' ' < "$out/beat-2-journal.txt")"
[ "$(seen check-1 failure)" = 1 ] || fail "no failure signal for the message that did not reach ntfy"
grep -q 'message: mail sent, ntfy network' "$out/beat-2-journal.txt" || fail "the message did not go out by e-mail only"
grep -q 'watchdog: failure, accepted' "$out/beat-2-journal.txt" || fail "the run does not say it told the failure"
# ntfy set back: a new address is no repair, and a run with nothing to send proves nothing. Only
# "Check connection" with the address set now ends the failure; the next run is the recovery.
"$helper" set ntfy "$base" cockpit-open > /dev/null
run_notifier beat-3
note "ntfy set back, a run with nothing to send: failures $(seen check-1 failure) (expected 2), recoveries $(seen check-1 recoveries) (expected 0)"
[ "$(seen check-1 failure)" = 2 ] && [ "$(seen check-1 recoveries)" = 0 ] || fail "a run with nothing to send ended the failure of ntfy"
[ "$(item_state ntfy)" = "['warning']" ] || fail "the status does not show ntfy failing"
"$helper" check ntfy > "$out/beat-check.txt" 2>&1 || { cat "$out/beat-check.txt"; fail "check ntfy"; }
got=$(result < "$out/beat-check.txt" | field "(d.get('health'), d.get('recovered'))")
note "Check connection: health, recovered: $got (expected ('ok', True))"
[ "$got" = "('ok', True)" ] || fail "Check connection did not end the failure of ntfy"
run_notifier beat-3b
[ "$(seen check-1 recoveries)" = 1 ] || fail "the run after Check connection is no recovery"
# The settings cannot be read by the notifier's user: the run fails, the watchdog is told.
chmod 600 /etc/cockpit-host-settings/notify.conf
systemctl reset-failed cockpit-host-settings-notify.service 2> /dev/null || true
if systemctl start cockpit-host-settings-notify.service 2> /dev/null; then fail "a run without readable settings succeeded"; fi
chmod 644 /etc/cockpit-host-settings/notify.conf
note "settings not readable: failures $(seen check-1 failure) (expected 3), the reason: $(last_beat "d['why']")"
[ "$(seen check-1 failure)" = 3 ] && [ "$(last_beat "d['why']")" = config ] || fail "no failure signal when the settings cannot be read"
# Another run holds the lock: the run that loses it gives no signal at all.
before="$(seen check-1 success) $(seen check-1 failure)"
flock /var/lib/cockpit-host-settings/.lock sleep 6 &
locker=$!
sleep 1
systemctl reset-failed cockpit-host-settings-notify.service 2> /dev/null || true
if systemctl start cockpit-host-settings-notify.service 2> /dev/null; then fail "a run took the lock another one holds"; fi
wait "$locker"
note "signals before and after the run that lost the lock: $before, $(seen check-1 success) $(seen check-1 failure)"
[ "$(seen check-1 success) $(seen check-1 failure)" = "$before" ] || fail "a run that lost the lock gave a signal"
run_notifier beat-4
[ "$(seen check-1 recoveries)" = 2 ] || fail "no recovery after the settings were readable again"
ok "a message that reached e-mail only and settings that cannot be read are failures; ntfy stays failed until Check connection; a run without the lock says nothing"

next "each channel on its own: e-mail fails while ntfy takes the message, and stays failed until it accepts a test"
# postfix stopped: nothing accepts mail on localhost:25. A new finding goes to ntfy only. The runs
# after it have nothing to send: e-mail stays a failure, also once postfix is back, and ntfy gets
# nothing twice. "Send a test message" ends it; the next run is exactly one recovery.
failures=$(seen check-1 failure)
back=$(seen check-1 recoveries)
tests=$(grep -c 'test message' /var/mail/hsmail || true)
mta_stop
wait_port25 no || fail "something still accepts mail on localhost:25"
echo warning > /var/lib/e2e-addon/state
echo '[{"fid":"port.8080-tcp","state":"warning","title":"8080/tcp web"}]' > /var/lib/e2e-addon/findings
run_notifier chan-1
journalctl --no-pager -o cat -u cockpit-host-settings-notify.service | grep -E 'message:' | tail -n 1 > "$out/chan-1-journal.txt"
note "$(cat "$out/chan-1-journal.txt")"
grep -q 'message: mail no-mta, ntfy sent, failing: mail no-mta' "$out/chan-1-journal.txt" || fail "the message did not reach ntfy only"
told=$(messages cockpit-open | grep -c '8080/tcp web' || true)
[ "$told" = 1 ] || fail "ntfy did not get the new finding once"
[ "$(seen check-1 failure)" = $((failures + 1)) ] || fail "no failure signal for the message e-mail did not accept"
since=$(last "d['channels']['mail']['failed']['since']")
note "last.json: e-mail failing since $since, $(last "d['channels']['mail']['failed']['reason']"); status: e-mail $(item_state mail), ntfy $(item_state ntfy)"
[ "$(last "d['channels']['mail']['failed']['reason']")" = no-mta ] || fail "last.json does not keep the failure of e-mail"
[ "$(item_state mail)" = "['warning']" ] && [ "$(item_state ntfy)" = "['ok']" ] || fail "the status does not show e-mail failing and ntfy working"
run_notifier chan-2
mta_start
wait_port25 yes || fail "postfix did not come back"
run_notifier chan-3
note "two runs with nothing to send, postfix back for the second: failures $(seen check-1 failure) (expected $((failures + 3))), recoveries $(seen check-1 recoveries) (expected $back), messages naming the finding on ntfy $(messages cockpit-open | grep -c '8080/tcp web' || true) (expected 1)"
[ "$(seen check-1 failure)" = $((failures + 3)) ] && [ "$(seen check-1 recoveries)" = "$back" ] || fail "a run with nothing to send ended the failure of e-mail"
[ "$(messages cockpit-open | grep -c '8080/tcp web' || true)" = 1 ] || fail "ntfy got the finding twice"
[ "$(last "d['channels']['mail']['failed']['since']")" = "$since" ] || fail "the failure of e-mail lost its beginning"
[ "$(last_beat "d['why']")" = channel ] || fail "the signal does not say a channel is failing"
"$helper" test mail > "$out/chan-test.txt" 2>&1 || { cat "$out/chan-test.txt"; fail "test mail"; }
got=$(result < "$out/chan-test.txt" | field "(d['result']['reason'], d.get('health'), d.get('recovered'))")
note "Send a test message: $got (expected ('sent', 'ok', True)); status: e-mail $(item_state mail)"
[ "$got" = "('sent', 'ok', True)" ] || fail "the test message did not end the failure of e-mail"
[ "$(item_state mail)" = "['ok']" ] || fail "the status does not show e-mail working again"
for _ in $(seq 1 20); do [ "$(grep -c 'test message' /var/mail/hsmail || true)" -gt "$tests" ] && break; sleep 1; done
[ "$(grep -c 'test message' /var/mail/hsmail || true)" -gt "$tests" ] || fail "the test message did not arrive"
run_notifier chan-4
run_notifier chan-5
again=$(journalctl --no-pager -o cat -u 'cockpit-host-settings*' | grep -c 'channel mail: accepted again' || true)
note "after the test, two runs: failures $(seen check-1 failure) (expected $((failures + 3))), recoveries $(seen check-1 recoveries) (expected $((back + 1))), recoveries in the journal $again (expected 1)"
[ "$(seen check-1 recoveries)" = $((back + 1)) ] && [ "$(seen check-1 failure)" = $((failures + 3)) ] || fail "not exactly one recovery"
[ "$again" = 1 ] || fail "the journal does not name exactly one recovery of e-mail"
ok "e-mail failed while ntfy took the finding once; runs with nothing to send kept the failure, also with postfix back; the test message ended it, one recovery"

next "the watchdog: Uptime Kuma's protocol; answers that say no"
beat_set uptime-kuma "$dog/api/push/$kuma_id?status=up&msg=OK&ping="
grep -qx "url = $dog/api/push/$kuma_id" /etc/cockpit-host-settings/heartbeat.conf || fail "the push URL was not stored without its query"
[ "$(beat_test)" = accepted ] && [ "$(seen check-2 success)" = 1 ] || fail "Uptime Kuma's test signal (status=up)"
run_notifier beat-5
[ "$(seen check-2 success)" = 2 ] || fail "no push after a run"
beat_set uptime-kuma "$dog/api/push/unknownToken0"
got=$(beat_test)
note "an unknown push token: $got (expected not-found)"
[ "$got" = not-found ] || fail "an unknown push token was taken for a success"
beat_set healthchecks "$dog/ping/00000000-0000-4000-8000-000000000000"
run_notifier beat-6
note "a check Healthchecks does not know (200 \"OK (not found)\"): $(last_beat "(d['result']['code'], d['result']['reason'])") (expected (200, 'not-found'))"
[ "$(last_beat "(d['result']['code'], d['result']['reason'])")" = "(200, 'not-found')" ] || fail "a 200 that says not found was taken"
[ "$(runuser -u nobody -- "$helper" status | field "[i['state'] for i in d['items'] if i['id'] == 'heartbeat']")" = "['warning']" ] ||
    fail "the status does not show the signal that did not get through"
logs=$(seen check-1 log)
beat_set healthchecks "$dog/redirect/ping/$hc_id"
got=$(beat_test)
note "a redirect to a real check: $got (expected redirect); log entries of that check: $(seen check-1 log) (expected $logs)"
[ "$got" = redirect ] && [ "$(seen check-1 log)" = "$logs" ] || fail "a redirect was followed"
ok "push URL stored without its query, status=up and down; an unknown push token, a 200 that says not found and a redirect are no success"

next "the watchdog: the timer stops, the signals stay away, it starts again, exactly one recovery"
cycle() {
    beat_set "$1" "$2"
    "$helper" install timer > "$out/cycle-$3-on.txt" 2>&1 || { cat "$out/cycle-$3-on.txt"; fail "install timer"; }
    grep -q 'The watchdog gets a signal after every run.' "$out/cycle-$3-on.txt" || fail "install timer does not name the watchdog"
    wait_seen "$3" success 30 $(($(seen "$3" success) + 3)) || fail "$1: no regular signals"
    "$helper" remove timer > "$out/cycle-$3-off.txt" 2>&1
    grep -q 'raises an alarm once its grace time is over' "$out/cycle-$3-off.txt" || fail "switching off does not say what the watchdog does"
    missed=$(seen "$3" missed)
    back=$(seen "$3" recoveries)
    wait_seen "$3" missed 40 $((missed + 1)) || fail "$1: the stand-in did not see the signals stay away"
    "$helper" install timer > /dev/null 2>&1
    wait_seen "$3" recoveries 30 $((back + 1)) || fail "$1: no recovery after the timer started again"
    wait_seen "$3" success 30 $(($(seen "$3" success) + 3)) || fail "$1: the signals did not come regularly again"
    note "$1: missed $(seen "$3" missed) (expected $((missed + 1))), recoveries $(seen "$3" recoveries) (expected $((back + 1)))"
    [ "$(seen "$3" recoveries)" = $((back + 1)) ] || fail "$1: not exactly one recovery"
    [ "$(seen "$3" missed)" = $((missed + 1)) ] || fail "$1: signals stayed away after the recovery"
    "$helper" remove timer > /dev/null
}
cycle healthchecks "$dog/ping/$hc_id" check-1
cycle uptime-kuma "$dog/api/push/$kuma_id" check-2
ok "for both protocols: missed once while the timer was off, one recovery when it ran again"

if [ -n "${KUMA_IMAGE:-}" ]; then
    next "a real Uptime Kuma ${KUMA_VERSION:?} (image pinned by digest) as the watchdog"
    kuma=http://127.0.0.1:3001
    # The made-up user, its password and the push URL stay in this file of root's, never in OUT.
    session=/tmp/kuma-e2e-session.json
    mkdir -p /tmp/kuma-e2e
    docker run -d --name kuma -p 127.0.0.1:3001:3001 -e UPTIME_KUMA_DB_TYPE=sqlite -v /tmp/kuma-e2e:/app/data:Z \
        "$KUMA_IMAGE" > /dev/null
    python3 "$here/kuma.py" wait "$kuma" 240 > /dev/null || { docker logs kuma 2>&1 | tail -n 30; fail "Uptime Kuma did not start"; }
    python3 "$here/kuma.py" setup "$kuma" "$session" > "$out/kuma-setup.txt" || { docker logs kuma 2>&1 | tail -n 30; fail "the user and the push monitor"; }
    note "$(cat "$out/kuma-setup.txt")"
    grep -qx "Uptime Kuma $KUMA_VERSION, push monitor [0-9]*" "$out/kuma-setup.txt" || fail "not Uptime Kuma $KUMA_VERSION"
    push=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["push_url"])' "$session")
    kuma_token=${push##*/}
    beat_set uptime-kuma "$push?status=up&msg=OK&ping="
    "$helper" install timer > /dev/null
    got=$(beat_test)
    note "test signal: $got (expected accepted)"
    [ "$got" = accepted ] || fail "Uptime Kuma did not take the test signal"
    python3 "$here/kuma.py" wait-beat "$kuma" "$session" 0 1 30 > /dev/null || fail "Uptime Kuma recorded no heartbeat"
    mark=$(python3 "$here/kuma.py" last "$kuma" "$session")
    # Longer than the monitor's heartbeat interval (20 seconds): Uptime Kuma checks and stays up.
    sleep 30
    python3 "$here/kuma.py" beats "$kuma" "$session" "$mark" > "$out/kuma-beats-on.jsonl"
    note "pushes while the timer ran: $(grep -c '"status": 1' "$out/kuma-beats-on.jsonl" || true)"
    if grep -q '"status": 0' "$out/kuma-beats-on.jsonl"; then fail "Uptime Kuma saw the monitor down while the timer ran"; fi
    "$helper" remove timer > /dev/null
    stopped=$(python3 "$here/kuma.py" last "$kuma" "$session")
    down=$(python3 "$here/kuma.py" wait-beat "$kuma" "$session" "$stopped" 0 60) || fail "Uptime Kuma did not see the heartbeats stay away"
    note "timer off, Uptime Kuma: $(printf '%s' "$down" | field "d['msg']")"
    down_id=$(printf '%s' "$down" | field "d['id']")
    "$helper" install timer > /dev/null
    up=$(python3 "$here/kuma.py" wait-beat "$kuma" "$session" "$down_id" 1 30) || fail "no recovery in Uptime Kuma"
    [ "$(printf '%s' "$up" | field "d['important']")" = True ] || fail "the recovery is no change of state in Uptime Kuma"
    sleep 15
    python3 "$here/kuma.py" beats "$kuma" "$session" "$down_id" > "$out/kuma-beats-back.jsonl"
    # The recoveries (up, a change of state) after the down beat, and the down beats after the first.
    counts=$(python3 -c '
import json, sys
b = [json.loads(x) for x in open(sys.argv[1])]
up = [x["id"] for x in b if x["status"] == 1 and x["important"]]
print(len(up), sum(1 for x in b if x["status"] == 0 and up and x["id"] > up[0]))' "$out/kuma-beats-back.jsonl")
    note "after the timer started again: recoveries, down beats after it: $counts (expected 1 0)"
    [ "$counts" = "1 0" ] || fail "not exactly one recovery in Uptime Kuma"
    # A failure signal: the settings cannot be read.
    "$helper" remove timer > /dev/null
    before=$(python3 "$here/kuma.py" last "$kuma" "$session")
    chmod 600 /etc/cockpit-host-settings/notify.conf
    systemctl reset-failed cockpit-host-settings-notify.service 2> /dev/null || true
    if systemctl start cockpit-host-settings-notify.service 2> /dev/null; then fail "a run without readable settings succeeded"; fi
    chmod 644 /etc/cockpit-host-settings/notify.conf
    down=$(python3 "$here/kuma.py" wait-beat "$kuma" "$session" "$before" 0 20) || fail "Uptime Kuma got no failure"
    note "failure signal, Uptime Kuma: $(printf '%s' "$down" | field "d['msg']")"
    [ "$(printf '%s' "$down" | field "d['msg']")" = "Host Settings: the settings could not be read" ] || fail "the failure is not the one sent"
    run_notifier kuma-back
    python3 "$here/kuma.py" wait-beat "$kuma" "$session" "$(printf '%s' "$down" | field "d['id']")" 1 20 > /dev/null || fail "no recovery after the failure"
    docker rm -f kuma > /dev/null
    ok "Uptime Kuma $KUMA_VERSION: heartbeats while the timer runs, down when it stops, one recovery, a failure with its reason"
else
    next "a real Uptime Kuma"
    note "not in this run: the real Uptime Kuma runs in job e2e (the runner's Docker), the stand-in above here"
fi

next "the watchdog removed; the timer as it was"
"$helper" heartbeat remove > "$out/beat-remove.txt" 2>&1
grep -q 'Pause or delete the check at the watchdog' "$out/beat-remove.txt" || fail "removing does not say what the watchdog does"
[ ! -s /etc/cockpit-host-settings/heartbeat.conf ] || fail "heartbeat.conf is not empty"
rm -f /etc/systemd/system/cockpit-host-settings-notify.timer.d/e2e.conf "${session:-/nonexistent}"
systemctl daemon-reload
kill "$stub" 2> /dev/null || true
cp "$beat_dir/status.json" "$out/watchdog-status.json"
cp "$beat_dir/requests.log" "$out/watchdog-requests.log"
ok "heartbeat.conf empty, the stand-in stopped; its log names the checks only by number"

next "the token and the watchdog's address stay where they belong; switching off says so"
if grep -rqs "$token" /var/lib/cockpit-host-settings /etc/cockpit-host-settings/notify.conf "$out"; then fail "the token is in a file"; fi
if journalctl --no-pager -u 'cockpit-host-settings*' | grep -q "$token"; then fail "the token is in the journal"; fi
for secret in "$hc_id" "$kuma_id" "${kuma_token:-$hc_id}"; do
    if grep -rqs -e "$secret" /var/lib/cockpit-host-settings /etc/cockpit-host-settings "$out"; then fail "a watchdog address is in a file"; fi
    if journalctl --no-pager -u 'cockpit-host-settings*' | grep -q -e "$secret"; then fail "a watchdog address is in the journal"; fi
done
"$helper" remove timer > /dev/null
[ "$(runuser -u nobody -- "$helper" status | field "d['summary']")" = "The regular check is off." ] || fail "switched off"
ok "the token only in /etc/cockpit-host-settings/ntfy.token, the watchdog's address only in heartbeat.conf while set; the status says the check is off"

next "remove the package"
if [ "$kind" = deb ]; then
    apt-get remove -y -qq cockpit-host-settings > /dev/null
else
    dnf remove -y -q cockpit-host-settings > /dev/null
fi
[ ! -e /var/lib/cockpit-host-settings ] || fail "the state stayed"
[ -e /etc/cockpit-host-settings/ntfy.token ] || fail "the settings went with the package"
ok "state gone, settings kept"
echo "ALL STEPS PASSED" | tee -a "$report"
