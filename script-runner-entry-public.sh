#!/bin/bash
# Entrypoint for the quest-script-runner-public container (public projects).
#
# The public profile is the inverse of the restricted one: internet egress
# is OPEN, and everything internal is CLOSED. The container is launched with
# --network=slirp4netns:allow_host_loopback=false (no host mapping, working
# DNS at 10.0.2.3) and WITHOUT the QUEST_API_KEY / QUEST_PORT env vars, so
# even if a network path to an internal service existed, the script has no
# credential to authenticate with.
#
# This script runs as root inside the container (via --user=0:0) so it can
# set up iptables rules and then drop to the unprivileged target user.
# The container uses --userns=keep-id so UID/GID mapping is correct; root
# inside the container maps to a sub-UID on the host, not actual host root.
#
# Steps:
#   1. Allow DNS to the slirp4netns resolver (10.0.2.3)
#   2. REJECT all private / link-local destinations: without these rules
#      slirp4netns happily routes to other hosts on the LAN and to the
#      cloud metadata service at 169.254.169.254 (service-account tokens!)
#      REJECT all IPv6 (the container has IPv6 disabled anyway)
#   3. Remove execute permission from iptables binaries
#   4. Drop all capabilities and switch to the target user via setpriv

RUN_UID="${QUEST_RUN_UID:-1000}"
RUN_GID="${QUEST_RUN_GID:-1000}"

if command -v iptables >/dev/null 2>&1 && command -v ip6tables >/dev/null 2>&1; then
    # IPv6: launched with slirp4netns enable_ipv6=false and
    # net.ipv6.conf.all.disable_ipv6=1, so there should be no IPv6 stack at
    # all. Belt and braces -- every REJECT below is IPv4-only, and IPv6
    # link-local / ULA / metadata (fd00:ec2::254) would slip past them --
    # reject every IPv6 destination and fail closed if that is impossible.
    if ! ip6tables -A OUTPUT -j REJECT; then
        echo "public sandbox: failed to install IPv6 egress policy, refusing to start" >&2
        exit 1
    fi
    # DNS: slirp4netns serves DNS from 10.0.2.3 (inside 10.0.0.0/8, so it
    # must be allowed explicitly before the private-range rejects).
    iptables -A OUTPUT -d 10.0.2.3 -p udp --dport 53 -j ACCEPT
    iptables -A OUTPUT -d 10.0.2.3 -p tcp --dport 53 -j ACCEPT
    # Block every private, link-local, and special-use destination. The
    # slirp gateway (10.0.2.2) is covered by 10.0.0.0/8: it remains usable
    # as the routing next hop (these rules match destinations), but is not
    # addressable as a destination itself.
    iptables -A OUTPUT -d 10.0.0.0/8 -j REJECT
    iptables -A OUTPUT -d 172.16.0.0/12 -j REJECT
    iptables -A OUTPUT -d 192.168.0.0/16 -j REJECT
    iptables -A OUTPUT -d 169.254.0.0/16 -j REJECT
    iptables -A OUTPUT -d 100.64.0.0/10 -j REJECT
else
    # Refuse to run with open egress and no filtering.
    echo "public sandbox: iptables/ip6tables unavailable, refusing to start" >&2
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
