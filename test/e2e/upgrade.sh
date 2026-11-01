#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# The upgrade test of Host Settings, on a throw-away machine with systemd and Docker (CI, job
# upgrade: the runner itself with the .deb; job e2e-el, suite upgrade: a Rocky Linux VM with
# SELinux enforcing and the .rpm, Podman standing in for Docker; never on a server). The previous
# release, as its GitHub release serves it (test/e2e/release-asset.sh), is set up as an admin
# would set it up; then the package of this build goes over it, as an update from the package
# repository does. A real ntfy server and the stand-in add-on as in test/e2e/run.sh.
#
#   sudo env OUT=upgrade-session sh test/e2e/upgrade.sh OLD.deb NEW.deb
#   sudo env OUT=upgrade-session sh test/e2e/upgrade.sh OLD.rpm NEW.rpm
#
# What it shows (docs/INTERNALS.md, "Tests"):
#   - the previous release with ntfy, e-mail (nothing on localhost:25), the token on standard
#     input and the timer on tells the warning of the stand-in in its summary through ntfy, and
#     its last.json holds the warning as told and the delivery that e-mail did not take;
#   - after the upgrade notify.conf, the token and the timer are as they were, heartbeat.conf is
#     there and empty (root, 0600), and systemd runs the new units;
#   - the next run reads the old last.json on: no message, neither the warning again nor an "OK
#     again" for it, and no new summary; the failure of e-mail is kept (last.json, the status as
#     nobody), ntfy is not made confirmed, since the previous release kept no target;
#   - the stand-in then lists findings for the item: one no more severe than the warning told is
#     taken as told without a message, a danger is told once (ntfy is confirmed by it, e-mail
#     stays failed), and a run without a change tells nothing;
#   - a target profile set after the upgrade states its gaps in one message, and the status verb
#     as nobody names it;
#   - from 0.2.0 on (0.1.0 knows neither findings nor coverage): back to the previous release and
#     forth again; each reads last.json on without a message, the previous release keeps the
#     channels' records, and what it sent is taken in again without making up a target;
#   - e-mail taken out of the settings is no longer counted, as a change and not a repair;
#   - removing the package keeps the settings in /etc/cockpit-host-settings and removes the state;
#     the token is in no file but its own and in no log.
# Writes OUT/report.md and the raw answers. Exit 1 at the first step that fails.
set -eu
usage="usage: upgrade.sh OLD.deb NEW.deb | OLD.rpm NEW.rpm"
old=${1:?$usage}
new=${2:?$usage}
case $old:$new in
    *.deb:*.deb) kind=deb ;;
    *.rpm:*.rpm) kind=rpm ;;
    *) echo "upgrade.sh: two .deb or two .rpm, not $old and $new" >&2; exit 64 ;;
esac
# apt and dnf take a local package by its path.
case $old in /*) ;; *) old=$PWD/$old ;; esac
case $new in /*) ;; *) new=$PWD/$new ;; esac
out=${OUT:-upgrade-session}
mkdir -p "$out"
report=$out/report.md
helper=/usr/lib/cockpit-host-settings/host-settings.py
conf=/etc/cockpit-host-settings
state=/var/lib/cockpit-host-settings
timer=cockpit-host-settings-notify.timer
service=cockpit-host-settings-notify.service
# Hashes of the settings and the token, outside OUT (the artifact holds nothing of the token).
keep=/run/hs-upgrade
# The version the Azentrius servers run, as in test/e2e/run.sh; raise both together. With the
# registry, as Podman takes no short names without asking.
ntfy_image=docker.io/binwiederhier/ntfy:v2.32.0
base=http://127.0.0.1:8090
topic=cockpit-e2e
step=0
echo "# Host Settings: upgrade" > "$report"

ok() { echo "- ok: $*" | tee -a "$report"; }
fail() { echo "- FAILED: $*" | tee -a "$report"; exit 1; }
next() { step=$((step + 1)); printf '\n## %s. %s\n\n' "$step" "$*" | tee -a "$report"; }
note() { echo "  $*" | tee -a "$report"; }
field() { python3 -c "import json, sys; d = json.load(sys.stdin); print(eval(sys.argv[1], {}, {'d': d}))" "$1"; }
# A field of last.json (the notifier's, 0600; root reads it).
last() { python3 -c "import json, sys; d = json.load(open(sys.argv[1])); print(eval(sys.argv[2], {}, {'d': d}))" "$state/last.json" "$1"; }
ntfy() { docker exec "$@"; }
# Every message the reader can see on the topic, one JSON per line, and how many.
messages() { curl -fsS -H "Authorization: Bearer $reader" "$base/$topic/json?poll=1"; }
count() { messages | wc -l; }
# One run of the notifier through the page's helper, its output in OUT/NAME.txt (start limit as in
# test/e2e/run.sh).
run_notifier() {
    systemctl reset-failed "$service" 2> /dev/null || true
    "$helper" run > "$out/$1.txt" 2>&1 || { cat "$out/$1.txt"; fail "run of the notifier ($1)"; }
}
installed() {
    if [ "$kind" = deb ]; then dpkg-query -W -f '${Version}' cockpit-host-settings; else rpm -q --qf '%{VERSION}-%{RELEASE}' cockpit-host-settings; fi
}
# The state of an item of the status, as nobody asks for it.
item_state() { runuser -u nobody -- "$helper" status | field "[i['state'] for i in d['items'] if i['id'] == '$1']"; }
# Whether something accepts connections on localhost:25 (yes or no).
port25() { python3 -c 'import socket; s = socket.socket(); s.settimeout(2); print("no" if s.connect_ex(("127.0.0.1", 25)) else "yes")'; }
version_of() {
    if [ "$kind" = deb ]; then dpkg-deb -f "$1" Version; else rpm -qp --qf '%{VERSION}-%{RELEASE}' "$1"; fi
}
install_package() {
    if [ "$kind" = deb ]; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$1" > /dev/null
    else
        dnf install -y -q "$1" > /dev/null
    fi
}

next "ntfy: deny-all, a user with write access to its topic only, a reader"
mkdir -p /tmp/ntfy-upgrade/lib
cat > /tmp/ntfy-upgrade/server.yml <<'EOF'
base-url: "http://127.0.0.1:8090"
auth-file: "/var/lib/ntfy/user.db"
auth-default-access: "deny-all"
cache-file: "/var/lib/ntfy/cache.db"
EOF
docker run -d --name ntfy -p 127.0.0.1:8090:80 -v /tmp/ntfy-upgrade/server.yml:/etc/ntfy/server.yml:ro,Z \
    -v /tmp/ntfy-upgrade/lib:/var/lib/ntfy:Z "$ntfy_image" serve > /dev/null
for _ in $(seq 1 30); do curl -fsS "$base/v1/health" > /dev/null 2>&1 && break; sleep 1; done
if ! curl -fsS "$base/v1/health" > "$out/health.json"; then
    docker logs ntfy 2>&1 | tail -n 20
    fail "ntfy did not start"
fi
pw=$(openssl rand -hex 16)
ntfy -e NTFY_PASSWORD="$pw" ntfy ntfy user add "$topic" > /dev/null 2>&1 || fail "ntfy user add"
ntfy ntfy ntfy access "$topic" "$topic" write-only > /dev/null 2>&1 || fail "ntfy access"
token=$(ntfy ntfy ntfy token add --label="Host Settings upgrade" "$topic" 2>&1 | grep -o 'tk_[A-Za-z0-9]*' | head -n 1)
[ -n "$token" ] || fail "no token for the topic's user"
ntfy -e NTFY_PASSWORD="$pw" ntfy ntfy user add reader > /dev/null 2>&1
ntfy ntfy ntfy access reader 'cockpit-*' read-only > /dev/null 2>&1
reader=$(ntfy ntfy ntfy token add reader 2>&1 | grep -o 'tk_[A-Za-z0-9]*' | head -n 1)
[ -n "$reader" ] || fail "no token for the reader"
ok "ntfy answers: $(cat "$out/health.json"); a write-only token for $topic, a reader"

next "the stand-in add-on in warning, the previous release"
# The stand-in of test/e2e/run.sh: its one item takes the state written to /var/lib/e2e-addon/state;
# with /var/lib/e2e-addon/findings (a JSON list) the item lists those findings, and a check of
# state "checked" says the list is complete.
mkdir -p /usr/share/cockpit/e2e-addon /usr/lib/cockpit-e2e-addon /var/lib/e2e-addon
cat > /usr/share/cockpit/e2e-addon/manifest.json <<'EOF'
{"menu": {"index": {"label": "E2E Add-on"}}, "azentrius": {"status": ["/usr/lib/cockpit-e2e-addon/status", "status"]}}
EOF
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
[ ! -e "$conf" ] && [ ! -e "$state" ] || fail "Host Settings was here before the test"
install_package "$old"
before=$(installed)
note "installed: $before (from $(basename "$old"))"
[ "$before" = "$(version_of "$old")" ] || fail "the previous release is not the one installed"
getent passwd cockpit-host-settings > /dev/null || fail "no user cockpit-host-settings"
ok "the stand-in in warning, cockpit-host-settings $before"

next "set up as an admin does: ntfy, e-mail, the token on standard input, the timer on"
# E-mail with nothing on localhost:25: the previous release cannot hand its summary over, so its
# last.json keeps a delivery that e-mail did not take.
for unit in postfix exim4 sendmail; do systemctl stop "$unit" 2> /dev/null || true; done
[ "$(port25)" = no ] || fail "something accepts mail on localhost:25"
"$helper" set ntfy "$base" "$topic" > /dev/null
"$helper" set mail hsmail@localhost > /dev/null
printf '%s\n' "$token" | "$helper" token set > /dev/null
"$helper" install timer > "$out/install.txt" 2>&1 || { cat "$out/install.txt"; fail "install timer"; }
systemctl is-enabled --quiet "$timer" && systemctl is-active --quiet "$timer" || fail "the timer is not on"
messages > "$out/messages-before.json"
grep -q 'messages set up' "$out/messages-before.json" || fail "no summary on ntfy"
grep -q 'Warning · E2E Add-on · The thing' "$out/messages-before.json" || fail "the summary does not name the warning"
told=$(last "d['sent']['e2e-addon#thing']['level']")
baseline=$(last "d['baseline_at']")
delivered=$(last "(d['delivery']['results']['ntfy']['reason'], d['delivery']['results']['mail']['reason'])")
note "last.json of $before: the stand-in's item told as $told, baseline $baseline, the summary to ntfy and e-mail: $delivered (expected ('sent', 'no-mta'))"
[ "$told" = warning ] || fail "last.json does not hold the warning as told"
[ "$delivered" = "('sent', 'no-mta')" ] || fail "the summary did not reach ntfy only"
delivered_at=$(last "d['delivery']['at']")
cp "$state/last.json" "$out/last-before.json"
mkdir -p "$keep"
chmod 700 "$keep"
sha256sum "$conf/notify.conf" "$conf/ntfy.token" > "$keep/settings.sha256"
test "$(stat -c '%a %U' "$conf/ntfy.token")" = "600 root" || fail "mode of ntfy.token"
# 0.1.0 had no watchdog and no heartbeat.conf; from 0.2.0 on the package makes an empty one.
case $before in
    0.1.*) [ ! -e "$conf/heartbeat.conf" ] || fail "the previous release has a heartbeat.conf already" ;;
    *) [ "$(stat -c '%a %U %s' "$conf/heartbeat.conf")" = "600 root 0" ] || fail "heartbeat.conf of $before is not an empty file of root's" ;;
esac
ok "summary on ntfy naming the warning; last.json holds it and the delivery e-mail did not take; the settings, the token (0600 root) and the timer on; heartbeat.conf as $before leaves it"

next "the package of this build over it"
install_package "$new"
after=$(installed)
note "installed: $after (from $(basename "$new"))"
[ "$after" = "$(version_of "$new")" ] || fail "the package of this build is not the one installed"
[ "$after" != "$before" ] || fail "no upgrade: the same version"
sha256sum -c --quiet "$keep/settings.sha256" || fail "notify.conf or the token changed"
test "$(stat -c '%a %U' "$conf/ntfy.token")" = "600 root" || fail "mode of ntfy.token after the upgrade"
systemctl is-enabled --quiet "$timer" || fail "the timer is no longer enabled"
systemctl is-active --quiet "$timer" || fail "the timer is no longer active"
beat=$(stat -c '%a %U %s' "$conf/heartbeat.conf") || fail "no heartbeat.conf"
note "heartbeat.conf: mode, owner, size $beat (expected 600 root 0)"
[ "$beat" = "600 root 0" ] || fail "heartbeat.conf is not an empty file of root's (0600)"
[ "$(systemctl show -p NeedDaemonReload --value "$service")" = no ] || fail "systemd still runs the previous units"
systemctl cat "$service" > "$out/unit-after.txt"
grep -qx "LoadCredential=heartbeat.conf:$conf/heartbeat.conf" "$out/unit-after.txt" ||
    fail "the unit systemd runs does not hand over heartbeat.conf"
[ "$(last "d['baseline_at']")" = "$baseline" ] || fail "the upgrade changed last.json"
ok "notify.conf and the token unchanged, the timer on, heartbeat.conf empty (0600 root), the new units loaded"

next "the first run after the upgrade reads last.json on"
before_count=$(count)
run_notifier run-after
messages > "$out/messages-after.json"
after_count=$(wc -l < "$out/messages-after.json")
again=$(grep -c 'OK again' "$out/messages-after.json" || true)
note "messages before and after the run: $before_count, $after_count (expected the same); naming OK again: $again (expected 0)"
[ "$after_count" = "$before_count" ] || fail "the first run after the upgrade sent a message"
[ "$again" = 0 ] || fail "the warning still open was told as OK again"
[ "$(last "d['baseline_at']")" = "$baseline" ] || fail "last.json was started anew"
[ "$(last "d['sent']['e2e-addon#thing']['level']")" = warning ] || fail "the warning told is no longer in last.json"
runuser -u nobody -- "$helper" status > "$out/status-after.json"
note "status as nobody: $(field "(d['version'], d['state'], d['summary'])" < "$out/status-after.json")"
failed=$(last "(d['channels']['mail']['failed']['reason'], d['channels']['mail']['failed']['since'], d['channels']['mail']['failed']['target'])")
note "e-mail in last.json: $failed (expected ('no-mta', '$delivered_at', None)); status: e-mail $(item_state mail), ntfy $(item_state ntfy) (expected warning, none)"
[ "$failed" = "('no-mta', '$delivered_at', None)" ] || fail "the failed delivery to e-mail of $before was not kept as it was"
[ "$(item_state mail)" = "['warning']" ] || fail "the status does not show e-mail failing"
[ "$(item_state ntfy)" = "['none']" ] || fail "ntfy is shown as confirmed, although $before kept no target"
ok "no message, no OK again, no new summary; the warning still told; the failure of e-mail kept, ntfy not confirmed"

next "findings for the item told: taken as told; a danger told once"
echo '[{"fid":"port.5432-tcp","state":"warning","title":"5432/tcp postgres"}]' > /var/lib/e2e-addon/findings
chmod 644 /var/lib/e2e-addon/findings
run_notifier findings-1
n=$(count)
note "after the item listed a warning: $n messages (expected $before_count)"
[ "$n" = "$before_count" ] || fail "a finding no more severe than the warning told was told"
entry=$(last "(d['sent']['e2e-addon#thing/port.5432-tcp']['level'], d['sent']['e2e-addon#thing/port.5432-tcp']['status'])")
note "its entry in last.json: $entry (expected ('warning', 'open'))"
[ "$entry" = "('warning', 'open')" ] || fail "the finding taken over is not open as a warning"
echo '[{"fid":"port.5432-tcp","state":"warning","title":"5432/tcp postgres"},{"fid":"port.6379-tcp","state":"danger","title":"6379/tcp redis"}]' \
    > /var/lib/e2e-addon/findings
run_notifier findings-2
messages > "$out/messages-findings.json"
n=$(wc -l < "$out/messages-findings.json")
second=$(grep -c 'Danger · E2E Add-on · 6379/tcp redis' "$out/messages-findings.json" || true)
first=$(grep -c '5432/tcp postgres' "$out/messages-findings.json" || true)
note "after a danger was added: $n messages (expected $((before_count + 1))); naming 6379/tcp redis: $second (expected 1); naming 5432/tcp postgres: $first (expected 0)"
[ "$n" = $((before_count + 1)) ] || fail "the danger did not give exactly one message"
[ "$second" = 1 ] || fail "the danger was not told"
[ "$first" = 0 ] || fail "the finding taken over was told"
note "status: ntfy $(item_state ntfy), e-mail $(item_state mail) (expected ok, warning); e-mail failing since $(last "d['channels']['mail']['failed']['since']")"
[ "$(item_state ntfy)" = "['ok']" ] && [ "$(item_state mail)" = "['warning']" ] || fail "ntfy is not confirmed by the message it took, or e-mail no longer failing"
[ "$(last "d['channels']['mail']['failed']['since']")" = "$delivered_at" ] || fail "the failure of e-mail lost its beginning"
run_notifier findings-3
n=$(count)
note "after a run without a change: $n messages (expected $((before_count + 1)))"
[ "$n" = $((before_count + 1)) ] || fail "a run without a change sent a message"
ok "the warning listed under the item told taken as told, the danger told once, the next run told nothing"

next "a target profile set after the upgrade"
# No role: the add-ons of every host, none of which is installed here; and the stand-in.
{ "$helper" profile roles && "$helper" profile expect add e2e-addon &&
  echo "not installed in this test" | "$helper" profile skip add security/fail2ban; } > "$out/profile.txt" 2>&1 ||
    { cat "$out/profile.txt"; fail "profile roles, expect add, skip add"; }
test "$(stat -c '%a %U' "$conf/profile.conf")" = "644 root" || fail "mode of profile.conf"
before_count=$(count)
run_notifier profile-1
messages > "$out/messages-profile.json"
n=$(wc -l < "$out/messages-profile.json")
gaps=$(grep -c 'Check no longer possible' "$out/messages-profile.json" || true)
note "after setting the profile: $n messages (expected $((before_count + 1))); naming a check no longer possible: $gaps (expected 1)"
[ "$n" = $((before_count + 1)) ] || fail "the gaps of the profile did not come as one message"
[ "$gaps" = 1 ] || fail "the gaps of the profile were not told"
runuser -u nobody -- "$helper" status --lang de > "$out/status-profile.json"
field "(d['features'], d['profile']['roles'], d['profile']['expect'], d['profile']['skip'])" < "$out/status-profile.json" | tee -a "$report"
[ "$(field "'profile' in d.get('features', []) and 'e2e-addon' in d['profile']['expect']" < "$out/status-profile.json")" = True ] ||
    fail "the status as nobody does not name the profile"
run_notifier profile-2
n=$(count)
note "after a run without a change: $n messages (expected $((before_count + 1)))"
[ "$n" = $((before_count + 1)) ] || fail "a run without a change sent a message"
ok "the profile's gaps in one message, the status as nobody with the profile, the next run told nothing"

next "back to the previous release and forth"
case $before in
    0.1.*)
        # 0.1.0 knows neither findings nor coverage: going back to it is not supported, and not
        # tested.
        note "not from $before: going back is tested from 0.2.0 on (job upgrade, the matrix entry for 0.2.0)"
        ;;
    *)
        since=$(last "d['channels']['mail']['failed']['since']")
        ntfy_ok=$(last "d['channels']['ntfy']['ok']")
        before_count=$(count)
        if [ "$kind" = deb ]; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --allow-downgrades "$old" > /dev/null
        else
            dnf downgrade -y -q "$old" > /dev/null
        fi
        [ "$(installed)" = "$before" ] || fail "the previous release is not installed again"
        run_notifier back-1
        n=$(count)
        note "$before again: messages $n (expected $before_count); format $(last "d['format']"), the channels' records kept: $(last "'channels' in d")"
        [ "$n" = "$before_count" ] || fail "$before sent a message after going back"
        [ "$(last "d['format']")" = 1 ] && [ "$(last "d['baseline_at']")" = "$baseline" ] || fail "$before started last.json anew"
        [ "$(last "'channels' in d")" = True ] || fail "$before dropped the channels' records"
        # A change $before tells: the danger gone from a complete list. ntfy takes it, e-mail not.
        echo '[{"fid":"port.5432-tcp","state":"warning","title":"5432/tcp postgres"}]' > /var/lib/e2e-addon/findings
        run_notifier back-2
        [ "$(count)" = $((before_count + 1)) ] || fail "$before did not tell the change once"
        [ "$(last "(d['delivery']['results']['ntfy']['reason'], d['delivery']['results']['mail']['reason'])")" = "('sent', 'no-mta')" ] ||
            fail "the message of $before did not reach ntfy only"
        install_package "$new"
        [ "$(installed)" = "$after" ] || fail "the package of this build is not installed again"
        run_notifier forth-1
        n=$(count)
        taken=$(last "d['channels']['mail']['failed']['at'] == d['delivery']['at']")
        note "this build again: messages $n (expected $((before_count + 1))); e-mail failing since $(last "d['channels']['mail']['failed']['since']") (expected $since); ntfy confirmed as before: $(last "d['channels']['ntfy']['ok'] == $ntfy_ok"); the message of $before taken in: $taken"
        [ "$n" = $((before_count + 1)) ] || fail "a message after coming back"
        [ "$(last "d['channels']['mail']['failed']['since']")" = "$since" ] || fail "the failure of e-mail lost its beginning"
        [ "$(last "d['channels']['ntfy']['ok'] == $ntfy_ok")" = True ] || fail "a message of $before without a target replaced the confirmation of ntfy"
        [ "$(item_state mail)" = "['warning']" ] && [ "$(item_state ntfy)" = "['ok']" ] || fail "the status after coming back"
        [ "$taken" = True ] || fail "the message of $before was not taken in"
        ok "$before read last.json on and kept the channels' records; this build took in what it sent, the failure of e-mail kept its beginning"
        ;;
esac

next "e-mail taken out of the settings: no longer counted, a change and not a repair"
"$helper" unset mail > /dev/null
run_notifier unset-mail
removed=$(last "d['channels']['mail']")
note "e-mail in last.json: $removed; the status names it: $(item_state mail) (expected [])"
[ "$(last "sorted(d['channels']['mail'])")" = "['removed']" ] || fail "the removal is not recorded"
[ "$(last "d['channels']['mail']['removed']['failed_since']")" = "$delivered_at" ] || fail "the removal does not say e-mail was failing"
[ "$(item_state mail)" = "[]" ] || fail "e-mail still in the status"
journalctl --no-pager -o cat -u "$service" | grep -q 'channel mail: removed from the settings while failing since' ||
    fail "the journal does not name the removal"
if journalctl --no-pager -o cat -u "$service" | grep -q 'channel mail: accepted again'; then fail "the removal was taken as a repair"; fi
# notify.conf changed on purpose: the removal of the package must keep it as it is now.
sha256sum "$conf/notify.conf" "$conf/ntfy.token" > "$keep/settings.sha256"
ok "e-mail removed while failing: recorded as such, no longer in the status, no recovery"

next "remove the package"
if [ "$kind" = deb ]; then
    DEBIAN_FRONTEND=noninteractive apt-get remove -y -qq cockpit-host-settings > /dev/null
else
    dnf remove -y -q cockpit-host-settings > /dev/null
fi
if [ -e "$state" ]; then fail "the state stayed: $(ls -A "$state" | tr '\n' ' ')"; fi
for f in notify.conf ntfy.token heartbeat.conf profile.conf; do
    [ -f "$conf/$f" ] || fail "$conf/$f went with the package"
done
sha256sum -c --quiet "$keep/settings.sha256" || fail "notify.conf or the token changed"
if [ -e "/etc/systemd/system/timers.target.wants/$timer" ] || [ -L "/etc/systemd/system/timers.target.wants/$timer" ]; then
    fail "the timer is still enabled"
fi
if systemctl is-active --quiet "$timer"; then fail "the timer still runs"; fi
getent passwd cockpit-host-settings > /dev/null || fail "the user cockpit-host-settings went (it stays, as documented)"
ok "the state gone; notify.conf, ntfy.token, heartbeat.conf and profile.conf kept; the timer gone; the user kept"

next "the token stays where it belongs"
if grep -rqs "$token" "$conf/notify.conf" "$conf/profile.conf" "$out"; then fail "the token is in a file"; fi
if journalctl --no-pager -u 'cockpit-host-settings*' | grep -q "$token"; then fail "the token is in the journal"; fi
rm -rf "$keep"
ok "the token only in $conf/ntfy.token"
echo "ALL STEPS PASSED" | tee -a "$report"
