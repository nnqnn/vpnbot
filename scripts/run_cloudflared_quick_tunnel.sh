#!/usr/bin/env bash

set -Eeuo pipefail

ORIGIN_URL="${CLOUDFLARED_ORIGIN_URL:-http://127.0.0.1:10086}"
URL_FILE="${CLOUDFLARED_URL_FILE:-/var/lib/tgvpn/cloudflared_quick_url}"
ENV_FILE="${SUBSCRIPTION_ENV_FILE:-/home/tgvpn/.env.subscription}"
SERVICE="${SUBSCRIPTION_SERVICE:-tgvpn-subscription.service}"
WS_PATH="${VLESS_PATH:-/kvpn-ws}"

mkdir -p "$(dirname "$URL_FILE")"

update_env_value() {
  local key="$1"
  local value="$2"
  python3 - "$ENV_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
out = []
seen = False
for line in lines:
    if line.startswith(f"{key}="):
        out.append(f"{key}={value}")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

apply_tunnel_url() {
  local url="$1"
  local host="${url#https://}"
  host="${host#http://}"
  host="${host%%/*}"
  [[ -n "$host" ]] || return 0

  local current=""
  [[ -f "$URL_FILE" ]] && current="$(cat "$URL_FILE" || true)"
  if [[ "$current" == "$url" ]]; then
    return 0
  fi

  printf '%s\n' "$url" > "${URL_FILE}.tmp"
  mv "${URL_FILE}.tmp" "$URL_FILE"

  update_env_value VLESS_PUBLIC_HOST "$host"
  update_env_value VLESS_PUBLIC_PORT "443"
  update_env_value VLESS_SECURITY "tls"
  update_env_value VLESS_TYPE "ws"
  update_env_value VLESS_SNI "$host"
  update_env_value VLESS_FLOW ""
  update_env_value VLESS_FP "chrome"
  update_env_value VLESS_PBK ""
  update_env_value VLESS_SID ""
  update_env_value VLESS_PATH "$WS_PATH"
  update_env_value VLESS_HEADER_TYPE ""

  systemctl restart "$SERVICE"
}

cloudflared tunnel --url "$ORIGIN_URL" --no-autoupdate 2>&1 | while IFS= read -r line; do
  printf '%s\n' "$line"
  if [[ "$line" =~ https://[-a-zA-Z0-9]+\.trycloudflare\.com ]]; then
    apply_tunnel_url "${BASH_REMATCH[0]}"
  fi
done
