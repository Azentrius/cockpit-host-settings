#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# E2E-1, step 6 (test/e2e-suite/core.py): stands in for iptables-save, ip6tables-save and nft
# inside cockpit-security-scan.service only. A drop-in in /run bind-mounts it over those programs
# for one scan, so that the scan's listing of the packet filter fails where the scan collects it;
# outside that unit the programs stay as they are. Never installed on a server.
echo "e2e-suite: the packet filter listing is unavailable to this scan on purpose" >&2
exit 1
