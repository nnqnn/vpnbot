#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SERVER2_HOST="${SUBSCRIPTION_SERVER2_HOST:-89.125.50.96}"
SERVER2_USER="${SUBSCRIPTION_SERVER2_USER:-root}"
SERVER2_DIR="${SUBSCRIPTION_SERVER2_DIR:-/home/tgvpn}"
DIRECT_HOST="${SUBSCRIPTION_DIRECT_HOST:-s2.nnqnn.tech}"
DIRECT_PORT="${SUBSCRIPTION_DIRECT_PORT:-9443}"
SUBSCRIPTION_PORT="${SUBSCRIPTION_LISTEN_PORT:-8088}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"
ORIGIN_SECRET="${SUBSCRIPTION_ORIGIN_SECRET:-}"
REQUIRE_ORIGIN_SECRET="${SUBSCRIPTION_REQUIRE_ORIGIN_SECRET:-false}"
RESTART_XRAY="${SUBSCRIPTION_RESTART_XRAY:-false}"
PUBLIC_KEY="${SUBSCRIPTION_SERVER2_VLESS_PBK:-}"
REALITY_PRIVATE_KEY="${SUBSCRIPTION_SERVER2_REALITY_PRIVATE_KEY:-}"
ARCHIVE="/tmp/tgvpn-server2-subscription.tar.gz"

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

load_optional_env() {
  local key="$1"
  local current="${!key:-}"
  if [[ -z "$current" ]] && value="$(load_env_value "$key" .env 2>/dev/null)"; then
    export "$key=$value"
  fi
}

require_cmd sshpass
require_cmd ssh
require_cmd scp
require_cmd tar

load_optional_env TGVPN_SERVER2_PASSWORD
load_optional_env SUBSCRIPTION_ORIGIN_SECRET
load_optional_env SUBSCRIPTION_REQUIRE_ORIGIN_SECRET
load_optional_env SUBSCRIPTION_RESTART_XRAY
load_optional_env SUBSCRIPTION_SERVER2_VLESS_PBK
load_optional_env SUBSCRIPTION_SERVER2_REALITY_PRIVATE_KEY
load_optional_env SUBSCRIPTION_SERVER2_HOST
load_optional_env SUBSCRIPTION_SERVER2_USER
load_optional_env SUBSCRIPTION_SERVER2_DIR
load_optional_env SUBSCRIPTION_DIRECT_HOST
load_optional_env SUBSCRIPTION_DIRECT_PORT
load_optional_env LETSENCRYPT_EMAIL

SERVER2_HOST="${SUBSCRIPTION_SERVER2_HOST:-$SERVER2_HOST}"
SERVER2_USER="${SUBSCRIPTION_SERVER2_USER:-$SERVER2_USER}"
SERVER2_DIR="${SUBSCRIPTION_SERVER2_DIR:-$SERVER2_DIR}"
DIRECT_HOST="${SUBSCRIPTION_DIRECT_HOST:-$DIRECT_HOST}"
DIRECT_PORT="${SUBSCRIPTION_DIRECT_PORT:-$DIRECT_PORT}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-$LETSENCRYPT_EMAIL}"
ORIGIN_SECRET="${SUBSCRIPTION_ORIGIN_SECRET:-$ORIGIN_SECRET}"
REQUIRE_ORIGIN_SECRET="${SUBSCRIPTION_REQUIRE_ORIGIN_SECRET:-$REQUIRE_ORIGIN_SECRET}"
RESTART_XRAY="${SUBSCRIPTION_RESTART_XRAY:-$RESTART_XRAY}"
PUBLIC_KEY="${SUBSCRIPTION_SERVER2_VLESS_PBK:-$PUBLIC_KEY}"
REALITY_PRIVATE_KEY="${SUBSCRIPTION_SERVER2_REALITY_PRIVATE_KEY:-$REALITY_PRIVATE_KEY}"

if [[ -z "${TGVPN_SERVER2_PASSWORD:-}" ]]; then
  echo "TGVPN_SERVER2_PASSWORD is required in environment or .env" >&2
  exit 1
fi
if [[ "$REQUIRE_ORIGIN_SECRET" == "true" && -z "$ORIGIN_SECRET" ]]; then
  ORIGIN_SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
fi
if [[ -z "$PUBLIC_KEY" ]]; then
  echo "SUBSCRIPTION_SERVER2_VLESS_PBK is required. Get it on server2 with: xray x25519 -i <server2_private_key>" >&2
  exit 1
fi

ssh_base=(
  sshpass -p "$TGVPN_SERVER2_PASSWORD"
  ssh -o StrictHostKeyChecking=accept-new
  "${SERVER2_USER}@${SERVER2_HOST}"
)
scp_base=(
  sshpass -p "$TGVPN_SERVER2_PASSWORD"
  scp -o StrictHostKeyChecking=accept-new
)

log "Packaging subscription service"
tar --exclude='.git' \
  --exclude='.venv' \
  --exclude='logs' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  -czf "$ARCHIVE" \
  app requirements.txt deploy scripts

log "Uploading code to ${SERVER2_USER}@${SERVER2_HOST}:${SERVER2_DIR}"
"${ssh_base[@]}" "mkdir -p '$SERVER2_DIR' /var/lib/tgvpn"
"${scp_base[@]}" "$ARCHIVE" "${SERVER2_USER}@${SERVER2_HOST}:/tmp/tgvpn-server2-subscription.tar.gz"

log "Installing and starting services on server2"
"${ssh_base[@]}" "bash -s" <<REMOTE_SCRIPT
set -Eeuo pipefail

server_dir="$SERVER2_DIR"
direct_host="$DIRECT_HOST"
direct_port="$DIRECT_PORT"
subscription_port="$SUBSCRIPTION_PORT"
origin_secret="$ORIGIN_SECRET"
public_key="$PUBLIC_KEY"
reality_private_key="$REALITY_PRIVATE_KEY"
restart_xray="$RESTART_XRAY"
xray_config=/usr/local/etc/xray/config.json

cd "\$server_dir"
tar -xzf /tmp/tgvpn-server2-subscription.tar.gz

apt-get update >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-httpx python3-dotenv curl >/dev/null

before_hash="\$(sha256sum "\$xray_config" | awk '{print \$1}')"
python3 "\$server_dir/scripts/configure_server2_xray_api.py" \
  --config "\$xray_config" \
  --api-port 10085 \
  --inbound-tag upstream-in \
  --direct-port "\$direct_port" \
  --server-name www.cloudflare.com \
  --server-name yandex.ru \
  --short-id a1b2c3d4e5f6a7b8 \
  --flow xtls-rprx-vision \
  --private-key "\$reality_private_key"
xray run -test -c "\$xray_config"
after_hash="\$(sha256sum "\$xray_config" | awk '{print \$1}')"
xray_config_changed=false
if [[ "\$before_hash" != "\$after_hash" ]]; then
  xray_config_changed=true
fi

cat > "\$server_dir/.env.subscription" <<ENV
LOG_LEVEL=INFO
SUBSCRIPTION_LISTEN_HOST=127.0.0.1
SUBSCRIPTION_LISTEN_PORT=\$subscription_port
SUBSCRIPTION_SNAPSHOT_PATH=/var/lib/tgvpn/subscription_snapshot.json
SUBSCRIPTION_ORIGIN_SECRET=\$origin_secret
SUBSCRIPTION_RESPONSE_FORMAT=xray_json
SUBSCRIPTION_PRODUCT=kVPN
SUBSCRIPTION_PUBLIC_BASE_URL=https://\$direct_host
SUBSCRIPTION_PROFILE_TITLE=kVPN @kkVPNrobot
SUBSCRIPTION_UPDATE_INTERVAL_HOURS=1
SUBSCRIPTION_TRAFFIC_TOTAL_BYTES=0
SUBSCRIPTION_ANNOUNCE_TEXT=kVPN: подписка обновляется автоматически.
SUBSCRIPTION_ANNOUNCE_URL=https://t.me/kvpnpublic
VLESS_PUBLIC_HOST=\$direct_host
VLESS_PUBLIC_PORT=\$direct_port
VLESS_SECURITY=reality
VLESS_TYPE=tcp
VLESS_SNI=yandex.ru
VLESS_FLOW=xtls-rprx-vision
VLESS_FP=chrome
VLESS_PBK=\$public_key
VLESS_SID=a1b2c3d4e5f6a7b8
VLESS_PATH=
VLESS_HEADER_TYPE=
VLESS_REMARK_PREFIX=kVPN
SUPPORT_URL=https://t.me/kvpn_support
WHITELIST_PROFILE_URL=https://vpn.nnqnn.tech/
WHITELIST_SOURCE_URL=https://raw.githubusercontent.com/zieng2/wl/main/vless_universal.txt
WHITELIST_MAX_NODES=300
WHITELIST_CACHE_SECONDS=300
ENV
chmod 600 "\$server_dir/.env.subscription"
if [[ -n "\$origin_secret" ]]; then
  printf '%s\n' "\$origin_secret" > /root/tgvpn-origin-secret.txt
  chmod 600 /root/tgvpn-origin-secret.txt
else
  rm -f /root/tgvpn-origin-secret.txt
fi

if [[ ! -f /var/lib/tgvpn/subscription_snapshot.json ]]; then
  printf '{"version":1,"product":"kVPN","generated_at":"bootstrap","users":{}}' \
    > /var/lib/tgvpn/subscription_snapshot.json
fi

cp "\$server_dir/deploy/systemd/tgvpn-subscription.service" /etc/systemd/system/tgvpn-subscription.service
systemctl daemon-reload
systemctl enable --now tgvpn-subscription.service
systemctl restart tgvpn-subscription.service
sleep 2
systemctl is-active --quiet tgvpn-subscription.service
curl -fsS "http://127.0.0.1:\$subscription_port/healthz" >/dev/null

if [[ "\$xray_config_changed" == "true" || "\$restart_xray" == "true" ]]; then
  echo "restarting xray after config change or explicit request"
  systemctl restart xray
  sleep 3
else
  echo "xray config unchanged; checking without restart"
fi
systemctl is-active --quiet xray
xray api inboundusercount --server=127.0.0.1:10085 --timeout=5 -tag=upstream-in --json >/dev/null
python3 "\$server_dir/scripts/smoke_server2_direct_vless.py"

echo "server2 subscription deployed"
if [[ -n "\$origin_secret" ]]; then
  echo "origin secret saved at /root/tgvpn-origin-secret.txt"
else
  echo "origin secret disabled for direct s2 subscription endpoint"
fi
REMOTE_SCRIPT

log "Checking DNS for ${DIRECT_HOST}"
dns_ips="$(dig +short "$DIRECT_HOST" A 2>/dev/null | tr '\n' ' ' || true)"
if [[ "$dns_ips" != *"$SERVER2_HOST"* ]]; then
  echo "WARNING: ${DIRECT_HOST} does not resolve to ${SERVER2_HOST} yet. Current A records: ${dns_ips:-none}" >&2
  echo "Skipping Nginx/Let's Encrypt setup until DNS is ready." >&2
else
  log "Configuring Nginx HTTPS origin for ${DIRECT_HOST}"
  "${ssh_base[@]}" "bash -s" <<REMOTE_NGINX
set -Eeuo pipefail

server_dir="$SERVER2_DIR"
direct_host="$DIRECT_HOST"
subscription_port="$SUBSCRIPTION_PORT"
letsencrypt_email="$LETSENCRYPT_EMAIL"

apt-get update >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot python3-certbot-nginx >/dev/null
mkdir -p /var/www/html
rm -f /etc/nginx/sites-enabled/tgvpn-subscription.conf

cat > /etc/nginx/sites-available/tgvpn-subscription-bootstrap.conf <<NGINX
server {
    listen 80;
    server_name \$direct_host;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        proxy_pass http://127.0.0.1:\$subscription_port;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Forwarded-Proto http;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/tgvpn-subscription-bootstrap.conf /etc/nginx/sites-enabled/tgvpn-subscription-bootstrap.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx

certbot_args=(certonly --webroot -w /var/www/html -d "\$direct_host" --noninteractive --agree-tos)
if [[ -n "\$letsencrypt_email" ]]; then
  certbot_args+=(--email "\$letsencrypt_email")
else
  certbot_args+=(--register-unsafely-without-email)
fi
certbot "\${certbot_args[@]}"

cp "\$server_dir/deploy/nginx/s2.nnqnn.tech.conf" /etc/nginx/sites-available/tgvpn-subscription.conf
sed -i "s/s2\\.nnqnn\\.tech/\$direct_host/g; s/127\\.0\\.0\\.1:8088/127.0.0.1:\$subscription_port/g" /etc/nginx/sites-available/tgvpn-subscription.conf
ln -sf /etc/nginx/sites-available/tgvpn-subscription.conf /etc/nginx/sites-enabled/tgvpn-subscription.conf
rm -f /etc/nginx/sites-enabled/tgvpn-subscription-bootstrap.conf
nginx -t
systemctl reload nginx
curl -fsS "https://\$direct_host/healthz" >/dev/null
REMOTE_NGINX
fi

log "Done"
