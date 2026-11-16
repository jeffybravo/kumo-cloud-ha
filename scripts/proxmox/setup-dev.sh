#!/usr/bin/env bash
# Runs inside the container (Debian 13, Python 3.13). Idempotent.
set -euo pipefail
REPO_URL="${REPO_URL:?set REPO_URL}"
DIR="${DIR:-/root/kumo-cloud-ha}"

apt-get update
apt-get install -y --no-install-recommends git ca-certificates curl python3 python3-venv python3-dev build-essential libffi-dev libssl-dev

[ -d "$DIR/.git" ] && git -C "$DIR" pull --ff-only || git clone "$REPO_URL" "$DIR"
cd "$DIR"
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements_test.txt aiohttp
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q

if [ "${WITH_HA:-0}" = 1 ]; then
  command -v docker >/dev/null || { curl -fsSL https://get.docker.com | sh; }
  mkdir -p /opt/ha/config/custom_components
  ln -sfn "$DIR/custom_components/kumo_cloud" /opt/ha/config/custom_components/kumo_cloud
  docker rm -f homeassistant 2>/dev/null || true
  docker run -d --name homeassistant --restart unless-stopped --network host \
    -v /opt/ha/config:/config -v "$DIR:$DIR:ro" \
    ghcr.io/home-assistant/home-assistant:stable
  echo "Home Assistant: http://$(hostname -I | awk '{print $1}'):8123"
fi
echo "Dev env ready: cd $DIR && source .venv/bin/activate && pytest"
