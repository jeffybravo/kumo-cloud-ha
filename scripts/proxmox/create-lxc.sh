#!/usr/bin/env bash
# Run on the Proxmox HOST (as root). Creates a Debian 13 LXC for developing and
# testing this integration, then runs setup-dev.sh inside it.
#
#   REPO_URL=https://github.com/jeffybravo/kumo-cloud-ha.git \
#   bash create-lxc.sh            # add WITH_HA=1 to also run a Home Assistant container
set -euo pipefail

CTID="${CTID:-$(pvesh get /cluster/nextid)}"
HOSTNAME="${HOSTNAME_CT:-kumo-dev}"
STORAGE="${STORAGE:-local-lvm}"          # rootfs storage
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
BRIDGE="${BRIDGE:-vmbr0}"
DISK_GB="${DISK_GB:-16}"
MEMORY="${MEMORY:-2048}"
CORES="${CORES:-2}"
WITH_HA="${WITH_HA:-0}"
REPO_URL="${REPO_URL:?set REPO_URL}"

pveam update >/dev/null
TEMPLATE="$(pveam available --section system | awk '/debian-13-standard/ {print $2}' | sort -V | tail -1)"
[ -n "$TEMPLATE" ] || { echo "No Debian 13 template found"; exit 1; }
pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE" || pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"

FEATURES="nesting=1"
[ "$WITH_HA" = 1 ] && FEATURES="nesting=1,keyctl=1"

pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$HOSTNAME" --cores "$CORES" --memory "$MEMORY" \
  --rootfs "$STORAGE:$DISK_GB" --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
  --features "$FEATURES" --unprivileged 1 --onboot 1
pct start "$CTID"
sleep 8

pct push "$CTID" "$(dirname "$0")/setup-dev.sh" /root/setup-dev.sh
pct exec "$CTID" -- env REPO_URL="$REPO_URL" WITH_HA="$WITH_HA" bash /root/setup-dev.sh
echo "Done. Enter with: pct enter $CTID"
