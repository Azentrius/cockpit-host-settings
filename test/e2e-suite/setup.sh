#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Prepares a throw-away machine for E2E-1, the end-to-end test of the suite with its real
# packages (CI, workflow e2e-suite; never a machine you use, never a server). As root:
#
#   sudo env ADMIN_USER=... ADMIN_PW=... PLAIN_USER=... PLAIN_PW=... DEBS=DIR sh test/e2e-suite/setup.sh
#
# DEBS holds the three packages of the run (cockpit-security-monitor, cockpit-host-settings,
# cockpit-status-board). Afterwards the machine has:
#   - Cockpit from the backports, as Status Board's own e2e job installs it, with two users:
#     ADMIN_USER in the sudo group, PLAIN_USER not; AllowMultiHost on;
#   - the three packages, installed with apt; no timer of theirs is on;
#   - a second host for the board, "e2e-peer": a container with an SSH server on 127.0.0.1:2022,
#     cockpit-bridge and Host Settings, whose target profile (no role) expects Security Monitor,
#     which the container does not have; both users log in there with a key of their own, its
#     host key is known (as in Status Board's e2e job, with a container instead of this machine
#     under another name, so that the two hosts differ);
#   - ntfy in Docker (deny-all, a user with write access to its topic only, a reader), set up in
#     Host Settings with the token on standard input; both tokens stay in /run/e2e-suite (root,
#     0600) and nowhere else;
#   - a public address on a dummy interface (pub0 203.0.113.10/24): the "internet" of the scan,
#     as in Security Monitor's own e2e job;
#   - UFW on: incoming denied, 5432/tcp and 6379/tcp allowed (the ACCEPT rules the test's
#     listeners will meet), with nothing listening there yet;
#   - /run/sshd made at boot (the line Security Monitor names for a socket-activated sshd), so
#     that the scan reads `sshd -T` every time;
#   - the helpers of the test in /usr/local/lib/e2e-suite.
# Every missing piece stops the script (set -e). All output goes to stderr.
set -eu
: "${ADMIN_USER:?}" "${ADMIN_PW:?}" "${PLAIN_USER:?}" "${PLAIN_PW:?}" "${DEBS:?}"
exec 1>&2
export DEBIAN_FRONTEND=noninteractive
here=$(cd "$(dirname "$0")" && pwd)
run=/run/e2e-suite
lib=/usr/local/lib/e2e-suite
hs=/usr/lib/cockpit-host-settings/host-settings.py
# The version on the Azentrius servers, as in test/e2e/run.sh; raise both together.
ntfy_image=docker.io/binwiederhier/ntfy:v2.32.0
peer_image=ubuntu:24.04
topic=cockpit-e2e
# shellcheck disable=SC1091
. /etc/os-release
# shellcheck disable=SC2154 # set by /etc/os-release
backports=$VERSION_CODENAME-backports

apt_get() {
    timeout 600 apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 "$@"
}
step() { printf '\n== %s\n' "$*"; }
die() { echo "setup.sh: $*"; exit 1; }

step "The helpers of the test"
install -d -m 700 "$run"
install -d -m 755 "$lib"
install -m 755 "$here/listen.py" "$here/security_stub.py" "$here/no_packet_filter.sh" "$lib/"

step "Two Cockpit users"
# The runner image sets XDG_CONFIG_HOME=/home/runner/.config for everyone in /etc/environment:
# Cockpit's bridge, started for another user, then fails on that folder and the login ends with
# 401 (found by Security Monitor's e2e job).
if grep -q '^XDG_CONFIG_HOME=' /etc/environment 2> /dev/null; then
    sed -i '/^XDG_CONFIG_HOME=/d' /etc/environment
fi
useradd -m -s /bin/bash -G sudo "$ADMIN_USER"
useradd -m -s /bin/bash "$PLAIN_USER"
printf '%s:%s\n%s:%s\n' "$ADMIN_USER" "$ADMIN_PW" "$PLAIN_USER" "$PLAIN_PW" | chpasswd

step "Cockpit from $backports and the three packages"
apt_get update -qq
apt_get install -y -qq --no-install-recommends -t "$backports" cockpit-ws cockpit-bridge cockpit-system > /dev/null
set -- "$DEBS"/*.deb
[ "$#" = 3 ] || die "three packages expected in $DEBS, found: $*"
apt_get install -y -qq --no-install-recommends "$@" > /dev/null
dpkg-query -W cockpit-ws cockpit-bridge cockpit-security-monitor cockpit-host-settings cockpit-status-board
printf '[WebService]\nAllowMultiHost = true\n' > /etc/cockpit/cockpit.conf
for t in cockpit-security-scan.timer cockpit-host-settings-notify.timer; do
    if systemctl is-active --quiet "$t"; then die "$t is on after the install"; fi
done

step "The second host: a container with SSH, cockpit-bridge and Host Settings"
hs_deb=$(ls "$DEBS"/cockpit-host-settings_*.deb)
docker run -d --name e2e-peer --hostname e2e-peer -p 127.0.0.1:2022:22 \
    -v "$hs_deb:/tmp/cockpit-host-settings.deb:ro" "$peer_image" sleep infinity > /dev/null
docker exec e2e-peer sh -ec '
    export DEBIAN_FRONTEND=noninteractive
    . /etc/os-release
    apt="-o Acquire::Retries=3 -o Acquire::http::Timeout=30"
    timeout 600 apt-get $apt update -qq
    timeout 600 apt-get $apt install -y -qq --no-install-recommends -t "$VERSION_CODENAME-backports" cockpit-bridge > /dev/null
    timeout 600 apt-get $apt install -y -qq --no-install-recommends openssh-server /tmp/cockpit-host-settings.deb > /dev/null
    dpkg-query -W cockpit-bridge openssh-server cockpit-host-settings
    install -d -m 755 /etc/ssh/e2e-keys
    printf "AuthorizedKeysFile /etc/ssh/e2e-keys/%%u\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nLogLevel VERBOSE\n" \
        > /etc/ssh/sshd_config.d/e2e-suite.conf
    ssh-keygen -A
    install -d -m 755 /run/sshd
    /usr/sbin/sshd -t
    # No syslog in the container: the log goes to a file (job step Logs).
    /usr/sbin/sshd -E /var/log/sshd-e2e.log
    # Its target profile: no role, so the add-ons of every host are expected, Security Monitor
    # among them.
    /usr/lib/cockpit-host-settings/host-settings.py profile roles
    runuser -u nobody -- /usr/lib/cockpit-host-settings/host-settings.py status --lang en |
        python3 -c "import json, sys; d = json.load(sys.stdin); print(d[\"state\"], d[\"profile\"][\"expect\"]); assert \"security\" in d[\"profile\"][\"expect\"]"
    ! test -e /usr/share/cockpit/security'
echo "127.0.0.1 e2e-peer" >> /etc/hosts
for u in "$ADMIN_USER" "$PLAIN_USER"; do
    docker exec e2e-peer useradd -m -s /bin/sh "$u"
    home=$(getent passwd "$u" | cut -d: -f6)
    install -d -m 700 -o "$u" -g "$u" "$home/.ssh"
    runuser -u "$u" -- ssh-keygen -q -t ed25519 -N '' -C "e2e-suite $u" -f "$home/.ssh/id_ed25519"
    # The runner's umask leaves the key readable for others, which ssh refuses.
    chmod 600 "$home/.ssh/id_ed25519"
    docker exec -i e2e-peer tee "/etc/ssh/e2e-keys/$u" < "$home/.ssh/id_ed25519.pub" > /dev/null
done
: > "$run/known_hosts"
for _ in $(seq 1 50); do
    ssh-keyscan -p 2022 -t ed25519 e2e-peer > "$run/known_hosts" 2> /dev/null || true
    [ -s "$run/known_hosts" ] && break
    sleep 0.2
done
[ -s "$run/known_hosts" ] || die "no host key from e2e-peer:2022"
for u in "$ADMIN_USER" "$PLAIN_USER"; do
    home=$(getent passwd "$u" | cut -d: -f6)
    install -m 644 -o "$u" -g "$u" "$run/known_hosts" "$home/.ssh/known_hosts"
    got=$(runuser -u "$u" -- ssh -p 2022 -o BatchMode=yes e2e-peer echo ok)
    [ "$got" = ok ] || die "$u cannot log in to e2e-peer with its key"
done
install -d /etc/cockpit/machines.d
cat > /etc/cockpit/machines.d/99-e2e-suite.json << 'EOF'
{"e2e-peer": {"address": "e2e-peer", "port": "2022", "visible": true, "color": "#67d300"}}
EOF
echo "e2e-peer: SSH for $ADMIN_USER and $PLAIN_USER, Host Settings with a profile, no Security Monitor"

step "ntfy: deny-all, a user with write access to its topic only, a reader"
install -d -m 755 /var/lib/e2e-suite-ntfy/lib
cat > /var/lib/e2e-suite-ntfy/server.yml << 'EOF'
base-url: "http://127.0.0.1:8090"
auth-file: "/var/lib/ntfy/user.db"
auth-default-access: "deny-all"
cache-file: "/var/lib/ntfy/cache.db"
EOF
docker run -d --name ntfy -p 127.0.0.1:8090:80 -v /var/lib/e2e-suite-ntfy/server.yml:/etc/ntfy/server.yml:ro \
    -v /var/lib/e2e-suite-ntfy/lib:/var/lib/ntfy "$ntfy_image" serve > /dev/null
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:8090/v1/health > /dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:8090/v1/health || { docker logs ntfy 2>&1 | tail -n 20; die "ntfy did not start"; }
echo
pw=$(openssl rand -hex 16)
docker exec -e NTFY_PASSWORD="$pw" ntfy ntfy user add "$topic" > /dev/null 2>&1 || die "ntfy user add $topic"
docker exec ntfy ntfy access "$topic" "$topic" write-only > /dev/null 2>&1 || die "ntfy access $topic"
docker exec ntfy ntfy token add --label="E2E suite" "$topic" 2>&1 | grep -o 'tk_[A-Za-z0-9]*' | head -n 1 > "$run/writer.token"
docker exec -e NTFY_PASSWORD="$pw" ntfy ntfy user add reader > /dev/null 2>&1 || die "ntfy user add reader"
docker exec ntfy ntfy access reader 'cockpit-*' read-only > /dev/null 2>&1 || die "ntfy access reader"
docker exec ntfy ntfy token add reader 2>&1 | grep -o 'tk_[A-Za-z0-9]*' | head -n 1 > "$run/reader.token"
chmod 600 "$run/writer.token" "$run/reader.token"
[ -s "$run/writer.token" ] && [ -s "$run/reader.token" ] || die "no ntfy token"
"$hs" set ntfy http://127.0.0.1:8090 "$topic"
"$hs" token set < "$run/writer.token"
"$hs" set language en
echo "ntfy $ntfy_image, topic $topic; Host Settings set up with the token"

step "A public address (pub0) and UFW"
ip link add pub0 type dummy
ip addr add 203.0.113.10/24 dev pub0
ip link set pub0 up
command -v ufw > /dev/null || apt_get install -y -qq ufw > /dev/null
ufw --force reset > /dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 5432/tcp comment 'e2e-suite listener A'
ufw allow 6379/tcp comment 'e2e-suite listener B'
ufw --force enable
ufw status verbose
ip -br addr
echo 'd /run/sshd 0755 root root -' > /etc/tmpfiles.d/sshd-privsep.conf
systemd-tmpfiles --create /etc/tmpfiles.d/sshd-privsep.conf

step "Cockpit"
systemctl start cockpit.socket
systemctl is-active cockpit.socket
# With UFW on, the second host is still reachable for both users.
for u in "$ADMIN_USER" "$PLAIN_USER"; do
    got=$(runuser -u "$u" -- ssh -p 2022 -o BatchMode=yes e2e-peer echo ok)
    [ "$got" = ok ] || die "$u cannot log in to e2e-peer with UFW on"
done
echo "setup done"
