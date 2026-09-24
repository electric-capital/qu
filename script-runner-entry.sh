#!/bin/bash
# Entrypoint for the quest-script-runner container.
#
# This script runs as root inside the container (via --user=0:0) so it can
# set up iptables rules and then drop to the unprivileged target user.
# The container uses --userns=keep-id so UID/GID mapping is correct; root
# inside the container maps to a sub-UID on the host, not actual host root.
#
# Steps:
#   1. Start socat forwarder (localhost:<port> -> 10.0.2.2:<port>)
#   2. Load iptables rules (one atomic iptables-restore) allowing ONLY the sandbox tool-API port on the
#      slirp gateway, and REJECT every other private / link-local
#      destination; REJECT all IPv6 (fail closed if iptables/ip6tables
#      are unavailable)
#   3. Remove execute permission from iptables binaries
#   4. Drop all capabilities and switch to the target user via setpriv

PORT="${QUEST_PORT:-8000}"
RUN_UID="${QUEST_RUN_UID:-1000}"
RUN_GID="${QUEST_RUN_GID:-1000}"

socat "TCP-LISTEN:${PORT},fork,reuseaddr,bind=127.0.0.1" "TCP:10.0.2.2:${PORT}" &
SOCAT_PID=$!

# Give socat a moment to bind the port
sleep 0.05

# Lock down host access: the ONLY permitted destination is the sandbox
# tool-API port on the slirp gateway. Everything else -- including the host
# itself reached via its real LAN IP -- must be rejected.
#
# 10.0.2.2 is the slirp4netns host gateway, but the host is ALSO reachable
# at its real address (e.g. host.containers.internal in /etc/hosts, or any
# 10.x/172.16.x/192.168.x IP the host holds). outbound_addr=127.0.0.1 kills
# genuine internet egress but NOT these host-local addresses: slirp delivers
# them to the host's loopback, so any service bound to 0.0.0.0 (the main
# Quest server, SSHd, the cloud metadata endpoint) answers. A rule that only
# names 10.0.2.2 leaves all of those wide open. So we ACCEPT the one allowed
# gateway:port and then REJECT every private + link-local range outright.
if ! command -v iptables-restore >/dev/null 2>&1 || ! command -v ip6tables >/dev/null 2>&1; then
    echo "restricted sandbox: iptables/ip6tables unavailable, refusing to start" >&2
    exit 1
fi

# IPv6: the container is launched with slirp4netns enable_ipv6=false and
# net.ipv6.conf.all.disable_ipv6=1, so there should be no IPv6 stack at all.
# Belt and braces -- none of the rules below cover IPv6, and slirp's default
# IPv6 gateway (fd00::2) is the host's loopback -- reject every IPv6
# destination and fail closed if the rule cannot be installed.
if ! ip6tables -A OUTPUT -j REJECT; then
    echo "restricted sandbox: failed to install IPv6 egress policy, refusing to start" >&2
    exit 1
fi

# The single allowed channel: the sandbox tool-API port on the gateway,
# then reject the host and the rest of the LAN. 10.0.0.0/8 covers the slirp
# gateway (10.0.2.2) too, so anything not matched by the ACCEPT is denied.
# 169.254.0.0/16 covers cloud metadata (e.g. 169.254.169.254).
#
# Loaded in ONE iptables-restore call (one process start instead of one per
# rule, on the critical path of every sandbox run). The restore commits
# atomically, so the policy is all-or-nothing and a failure fails closed.
if ! iptables-restore <<EOF
*filter
-A OUTPUT -d 10.0.2.2/32 -p tcp -m tcp --dport ${PORT} -j ACCEPT
-A OUTPUT -d 10.0.0.0/8 -j REJECT
-A OUTPUT -d 172.16.0.0/12 -j REJECT
-A OUTPUT -d 192.168.0.0/16 -j REJECT
-A OUTPUT -d 169.254.0.0/16 -j REJECT
-A OUTPUT -d 100.64.0.0/10 -j REJECT
COMMIT
EOF
then
    echo "restricted sandbox: failed to install IPv4 egress policy, refusing to start" >&2
    exit 1
fi

# Remove execute permission from iptables binaries so the user's script
# cannot call them even if capabilities were somehow retained.
chmod 0 /usr/sbin/xtables-nft-multi /usr/sbin/xtables-legacy-multi 2>/dev/null

# Drop to the target user with all capabilities cleared and no-new-privs set.
# --reuid/--regid: switch to the unprivileged user that owns the workspace
# --clear-groups: remove root's supplementary groups
# --inh-caps=-all: clear inheritable capabilities
# --bounding-set=-all: clear the capability bounding set
# --no-new-privs: prevent the exec'd process from gaining any capabilities
exec setpriv \
    --reuid="${RUN_UID}" --regid="${RUN_GID}" \
    --clear-groups \
    --inh-caps=-all \
    --bounding-set=-all \
    --no-new-privs \
    -- "$@"
