#!/usr/bin/env bash

set -Eeuo pipefail

SERVER_HOST="${SERVER_HOST:-5.129.213.120}"
SERVER_USER="${SERVER_USER:-root}"
SERVER_DIR="${SERVER_DIR:-/home/tgvpn}"
DIRECT_HOST="${SUBSCRIPTION_DIRECT_HOST:-s2.nnqnn.tech}"
DIRECT_IP="${SUBSCRIPTION_SERVER2_HOST:-89.125.50.96}"
PUBLIC_BASE_URL="${SUBSCRIPTION_PUBLIC_BASE_URL:-https://s2.nnqnn.tech}"
PRODUCT="${SUBSCRIPTION_PRODUCT:-kVPN}"
SERVICE="${SERVICE:-tgvpn-bot.service}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

log() {
  printf '\n==> %s\n' "$*"
}

require_cmd() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "Required command not found: $name" >&2
    exit 1
  fi
}

load_env_value() {
  local key="$1"
  local file="${2:-.env}"
  local line value
  [[ -f "$file" ]] || return 1
  line="$(grep -E "^[[:space:]]*${key}=" "$file" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 1

  value="${line#*=}"
  value="${value%$'\r'}"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then
    value="${value:1:${#value}-2}"
  elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "$value"
}

require_cmd sshpass
require_cmd ssh
require_cmd dig

if [[ -z "${TGVPN_SERVER_PASSWORD:-}" ]]; then
  if TGVPN_SERVER_PASSWORD="$(load_env_value TGVPN_SERVER_PASSWORD .env 2>/dev/null)"; then
    export TGVPN_SERVER_PASSWORD
  else
    echo "TGVPN_SERVER_PASSWORD is required in environment or .env" >&2
    exit 1
  fi
fi

log "Checking ${DIRECT_HOST} DNS"
dns_ips="$(dig +short "$DIRECT_HOST" A | tr '\n' ' ' || true)"
if [[ "$dns_ips" != *"$DIRECT_IP"* ]]; then
  echo "${DIRECT_HOST} does not resolve to ${DIRECT_IP}; current A records: ${dns_ips:-none}" >&2
  exit 1
fi

log "Verifying public subscription before enabling bot links"
SSHPASS="$TGVPN_SERVER_PASSWORD" sshpass -e ssh \
  -o StrictHostKeyChecking=accept-new \
  "${SERVER_USER}@${SERVER_HOST}" \
  "bash -s -- '$SERVER_DIR' '$PUBLIC_BASE_URL' '$PRODUCT' '$SERVICE'" <<'REMOTE_SCRIPT'
set -Eeuo pipefail

server_dir="$1"
public_base_url="$2"
product="$3"
service="$4"

cd "$server_dir"
".venv/bin/python" scripts/sync_subscription_snapshot.py >/dev/null

token="$(
  ".venv/bin/python" - <<'PY'
import json
from pathlib import Path

snapshot = json.loads(Path("logs/subscription_snapshot.json").read_text(encoding="utf-8"))
users = snapshot.get("users", {})
for candidate, user in users.items():
    if user.get("main_vpn_active") or user.get("whitelist_enabled"):
        print(candidate)
        break
PY
)"

if [[ -z "$token" ]]; then
  echo "No eligible subscription token found in snapshot." >&2
  exit 1
fi

url="${public_base_url%/}/sub/${product}/${token}"
tmp_headers="$(mktemp)"
tmp_body="$(mktemp)"
trap 'rm -f "$tmp_headers" "$tmp_body"' EXIT

curl -fsS --compressed -D "$tmp_headers" -o "$tmp_body" "$url" >/dev/null
grep -aiq '^cache-control: no-store' "$tmp_headers"
grep -aiq '^profile-title:' "$tmp_headers"
grep -aiq '^profile-update-interval:' "$tmp_headers"
grep -aiq '^subscription-userinfo:' "$tmp_headers"

".venv/bin/python" - "$tmp_body" <<'PY'
import base64
import json
import sys
from pathlib import Path

body = Path(sys.argv[1]).read_bytes()
try:
    payload = json.loads(body.decode("utf-8"))
except json.JSONDecodeError:
    decoded = base64.b64decode(body).decode("utf-8")
    nodes = [line for line in decoded.splitlines() if line.strip()]
    if not nodes:
        raise SystemExit("subscription body has no nodes")
    if not all(line.startswith(("vless://", "vmess://", "trojan://", "ss://", "socks://")) for line in nodes):
        raise SystemExit("subscription body contains unsupported node lines")
else:
    if not isinstance(payload, dict):
        raise SystemExit("JSON subscription body is not an object")
    outbounds = payload.get("outbounds")
    routing = payload.get("routing")
    if not isinstance(outbounds, list) or not outbounds:
        raise SystemExit("JSON subscription has no outbounds")
    if not isinstance(routing, dict):
        raise SystemExit("JSON subscription has no routing object")
PY

curl -fsS --compressed "${public_base_url%/}/add/${product}/${token}" >/dev/null

PUBLIC_BASE_URL="$public_base_url" ".venv/bin/python" - <<'PY'
import os
from pathlib import Path

path = Path(".env")
updates = {
    "SUBSCRIPTION_LINKS_ENABLED": "true",
    "SUBSCRIPTION_PUBLIC_BASE_URL": os.environ["PUBLIC_BASE_URL"],
}
lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

systemctl restart "$service"
sleep 3
systemctl is-active --quiet "$service"
echo "subscription links enabled"
REMOTE_SCRIPT

log "Cutover completed"
