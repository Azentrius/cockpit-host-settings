#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# The end-to-end test of Host Settings, on a throw-away machine with systemd and Docker (CI, job
# e2e; never on a server): the package with its real units, a real ntfy server, postfix for local
# mail and a stand-in add-on whose state the test switches.
#
#   sudo env OUT=e2e-session sh test/e2e/run.sh dist/cockpit-host-settings_*.deb
#
# What it shows (docs/INTERNALS.md, "Tests"):
#   - the commands the page shows for the ntfy server run there exactly as shown, and give a
#     token (concept: what ntfy answers is only read from its documentation so far);
#   - "Check connection" against a topic closed without a login and against an open one: the
#     HTTP answers ntfy really gives (401 or 403), and that the write-only token cannot read;
#   - the first run sends the summary through ntfy and e-mail, a change sends one message, a run
#     without a change none; once the stand-in lists findings, a second finding sends one message
#     although its item stays a danger; the status verb answers as nobody; switching off says so;
#   - the token is in no file but its own and in no log.
# Writes OUT/report.md and the raw answers. Exit 1 at the first step that fails.
set -eu
deb=${1:?usage: run.sh PACKAGE.deb}
out=${OUT:-e2e-session}
mkdir -p "$out"
report=$out/report.md
helper=/usr/lib/cockpit-host-settings/host-settings.py
# The version the Azentrius servers run (public-host); raise both together.
ntfy_image=binwiederhier/ntfy:v2.32.0
base=http://127.0.0.1:8090
step=0
echo "# Host Settings: end-to-end" > "$report"

ok() { echo "- ok: $*" | tee -a "$report"; }
fail() { echo "- FAILED: $*" | tee -a "$report"; exit 1; }
next() { step=$((step + 1)); printf '\n## %s. %s\n\n' "$step" "$*" | tee -a "$report"; }
# The JSON after "RESULT " in the output of check/test.
result() { sed -n 's/^RESULT //p'; }
field() { python3 -c "import json, sys; d = json.load(sys.stdin); print(eval(sys.argv[1], {}, {'d': d}))" "$1"; }
ntfy() { docker exec "$@"; }
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
docker run -d --name ntfy -p 127.0.0.1:8090:80 -v /tmp/ntfy-e2e/server.yml:/etc/ntfy/server.yml:ro \
    -v /tmp/ntfy-e2e/lib:/var/lib/ntfy "$ntfy_image" serve > /dev/null
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
echo "postfix postfix/main_mailer_type select Local only" | debconf-set-selections
echo "postfix postfix/mailname string e2e.localdomain" | debconf-set-selections
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postfix > /dev/null
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
apt-get install -y -qq "./$deb" > /dev/null || apt-get install -y -qq "$deb" > /dev/null
getent passwd cockpit-host-settings > /dev/null || fail "no user cockpit-host-settings"
ok "postfix (local only), the stand-in add-on in warning, $(dpkg-query -W -f '${Package} ${Version}' cockpit-host-settings)"

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

next "the status verb as nobody, and for the board"
runuser -u nobody -- "$helper" status --lang de > "$out/status-de.json"
field "(d['state'], d['summary'])" < "$out/status-de.json" | tee -a "$report"
[ "$(field "d['addon']" < "$out/status-de.json")" = host-settings ] || fail "status verb"
ok "the status verb answers without root"

next "Check connection against an open topic"
ntfy ntfy ntfy access everyone cockpit-open rw > /dev/null 2>&1
ntfy ntfy ntfy access "$topic" cockpit-open write-only > /dev/null 2>&1
"$helper" set ntfy "$base" cockpit-open
"$helper" check ntfy > "$out/check-open.txt" 2>&1 || { cat "$out/check-open.txt"; fail "check ntfy (open)"; }
result < "$out/check-open.txt" > "$out/check-open.json"
[ "$(field "d['access']" < "$out/check-open.json")" = "{'state': 'warning', 'anon_read': 'warning', 'anon_write': 'warning'}" ] ||
    fail "the open topic is not judged open: $(cat "$out/check-open.json")"
ok "open: readable and writable without a login, both warnings"

next "the token stays where it belongs; switching off says so"
if grep -rqs "$token" /var/lib/cockpit-host-settings /etc/cockpit-host-settings/notify.conf "$out"; then fail "the token is in a file"; fi
if journalctl --no-pager -u 'cockpit-host-settings*' | grep -q "$token"; then fail "the token is in the journal"; fi
"$helper" remove timer > /dev/null
[ "$(runuser -u nobody -- "$helper" status | field "d['summary']")" = "The regular check is off." ] || fail "switched off"
ok "the token only in /etc/cockpit-host-settings/ntfy.token; the status says the check is off"

next "remove the package"
apt-get remove -y -qq cockpit-host-settings > /dev/null
[ ! -e /var/lib/cockpit-host-settings ] || fail "the state stayed"
[ -e /etc/cockpit-host-settings/ntfy.token ] || fail "the settings went with the package"
ok "state gone, settings kept"
echo "ALL STEPS PASSED" | tee -a "$report"
