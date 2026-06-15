#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SERVER2_HOST="${SUBSCRIPTION_SERVER2_HOST:-89.125.50.96}"
SERVER2_USER="${SUBSCRIPTION_SERVER2_USER:-root}"
SERVER2_DIR="${SUBSCRIPTION_SERVER2_DIR:-/home/tgvpn}"
DIRECT_HOST="${SUBSCRIPTION_DIRECT_HOST:-s2.nnqnn.tech}"
DIRECT_PORT="${SUBSCRIPTION_DIRECT_PORT:-9443}"
PUBLIC_REALITY_INBOUND_TAG="${SUBSCRIPTION_PUBLIC_REALITY_INBOUND_TAG:-direct-reality-8443}"
PUBLIC_REALITY_PORT="${SUBSCRIPTION_PUBLIC_REALITY_PORT:-443}"
NOFLOW_REALITY_INBOUND_TAG="${SUBSCRIPTION_NOFLOW_REALITY_INBOUND_TAG:-direct-reality-noflow-8443}"
NOFLOW_REALITY_PORT="${SUBSCRIPTION_NOFLOW_REALITY_PORT:-8443}"
XHTTP_PORT="${SUBSCRIPTION_XHTTP_PORT:-10087}"
XHTTP_PATH="${SUBSCRIPTION_XHTTP_PATH:-/kvpn-xhttp}"
XHTTP_MODE="${SUBSCRIPTION_XHTTP_MODE:-packet-up}"
HYSTERIA2_INBOUND_TAG="${SUBSCRIPTION_HYSTERIA2_INBOUND_TAG:-hysteria2-udp-443}"
HYSTERIA2_PORT="${SUBSCRIPTION_HYSTERIA2_PORT:-443}"
HYSTERIA2_CERT_FILE="${SUBSCRIPTION_HYSTERIA2_CERT_FILE:-/usr/local/etc/xray/certs/s2.fullchain.pem}"
HYSTERIA2_KEY_FILE="${SUBSCRIPTION_HYSTERIA2_KEY_FILE:-/usr/local/etc/xray/certs/s2.privkey.pem}"
HYSTERIA2_MASQUERADE_URL="${SUBSCRIPTION_HYSTERIA2_MASQUERADE_URL:-https://www.yandex.ru/}"
HYSTERIA2_AUTH="${SUBSCRIPTION_HYSTERIA2_AUTH:-}"
PUBLIC_VLESS_PORT="${SUBSCRIPTION_PUBLIC_VLESS_PORT:-443}"
NGINX_HTTPS_BACKEND_PORT="${SUBSCRIPTION_NGINX_HTTPS_BACKEND_PORT:-18443}"
NGINX_HTTPS_PUBLIC_PORT="${SUBSCRIPTION_NGINX_HTTPS_PUBLIC_PORT:-8444}"
SUBSCRIPTION_PORT="${SUBSCRIPTION_LISTEN_PORT:-8088}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"
ORIGIN_SECRET="${SUBSCRIPTION_ORIGIN_SECRET:-}"
REQUIRE_ORIGIN_SECRET="${SUBSCRIPTION_REQUIRE_ORIGIN_SECRET:-false}"
RESTART_XRAY="${SUBSCRIPTION_RESTART_XRAY:-false}"
ENABLE_CLOUDFLARED="${SUBSCRIPTION_ENABLE_CLOUDFLARED:-false}"
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
load_optional_env SUBSCRIPTION_ENABLE_CLOUDFLARED
load_optional_env SUBSCRIPTION_SERVER2_VLESS_PBK
load_optional_env SUBSCRIPTION_SERVER2_REALITY_PRIVATE_KEY
load_optional_env SUBSCRIPTION_SERVER2_HOST
load_optional_env SUBSCRIPTION_SERVER2_USER
load_optional_env SUBSCRIPTION_SERVER2_DIR
load_optional_env SUBSCRIPTION_DIRECT_HOST
load_optional_env SUBSCRIPTION_DIRECT_PORT
load_optional_env SUBSCRIPTION_PUBLIC_REALITY_INBOUND_TAG
load_optional_env SUBSCRIPTION_PUBLIC_REALITY_PORT
load_optional_env SUBSCRIPTION_NOFLOW_REALITY_INBOUND_TAG
load_optional_env SUBSCRIPTION_NOFLOW_REALITY_PORT
load_optional_env SUBSCRIPTION_XHTTP_PORT
load_optional_env SUBSCRIPTION_XHTTP_PATH
load_optional_env SUBSCRIPTION_XHTTP_MODE
load_optional_env SUBSCRIPTION_HYSTERIA2_INBOUND_TAG
load_optional_env SUBSCRIPTION_HYSTERIA2_PORT
load_optional_env SUBSCRIPTION_HYSTERIA2_CERT_FILE
load_optional_env SUBSCRIPTION_HYSTERIA2_KEY_FILE
load_optional_env SUBSCRIPTION_HYSTERIA2_MASQUERADE_URL
load_optional_env SUBSCRIPTION_HYSTERIA2_AUTH
load_optional_env SUBSCRIPTION_PUBLIC_VLESS_PORT
load_optional_env SUBSCRIPTION_NGINX_HTTPS_BACKEND_PORT
load_optional_env SUBSCRIPTION_NGINX_HTTPS_PUBLIC_PORT
load_optional_env LETSENCRYPT_EMAIL

SERVER2_HOST="${SUBSCRIPTION_SERVER2_HOST:-$SERVER2_HOST}"
SERVER2_USER="${SUBSCRIPTION_SERVER2_USER:-$SERVER2_USER}"
SERVER2_DIR="${SUBSCRIPTION_SERVER2_DIR:-$SERVER2_DIR}"
DIRECT_HOST="${SUBSCRIPTION_DIRECT_HOST:-$DIRECT_HOST}"
DIRECT_PORT="${SUBSCRIPTION_DIRECT_PORT:-$DIRECT_PORT}"
PUBLIC_REALITY_INBOUND_TAG="${SUBSCRIPTION_PUBLIC_REALITY_INBOUND_TAG:-$PUBLIC_REALITY_INBOUND_TAG}"
PUBLIC_REALITY_PORT="${SUBSCRIPTION_PUBLIC_REALITY_PORT:-$PUBLIC_REALITY_PORT}"
NOFLOW_REALITY_INBOUND_TAG="${SUBSCRIPTION_NOFLOW_REALITY_INBOUND_TAG:-$NOFLOW_REALITY_INBOUND_TAG}"
NOFLOW_REALITY_PORT="${SUBSCRIPTION_NOFLOW_REALITY_PORT:-$NOFLOW_REALITY_PORT}"
XHTTP_PORT="${SUBSCRIPTION_XHTTP_PORT:-$XHTTP_PORT}"
XHTTP_PATH="${SUBSCRIPTION_XHTTP_PATH:-$XHTTP_PATH}"
XHTTP_MODE="${SUBSCRIPTION_XHTTP_MODE:-$XHTTP_MODE}"
HYSTERIA2_INBOUND_TAG="${SUBSCRIPTION_HYSTERIA2_INBOUND_TAG:-$HYSTERIA2_INBOUND_TAG}"
HYSTERIA2_PORT="${SUBSCRIPTION_HYSTERIA2_PORT:-$HYSTERIA2_PORT}"
HYSTERIA2_CERT_FILE="${SUBSCRIPTION_HYSTERIA2_CERT_FILE:-$HYSTERIA2_CERT_FILE}"
HYSTERIA2_KEY_FILE="${SUBSCRIPTION_HYSTERIA2_KEY_FILE:-$HYSTERIA2_KEY_FILE}"
HYSTERIA2_MASQUERADE_URL="${SUBSCRIPTION_HYSTERIA2_MASQUERADE_URL:-$HYSTERIA2_MASQUERADE_URL}"
HYSTERIA2_AUTH="${SUBSCRIPTION_HYSTERIA2_AUTH:-$HYSTERIA2_AUTH}"
PUBLIC_VLESS_PORT="${SUBSCRIPTION_PUBLIC_VLESS_PORT:-$PUBLIC_VLESS_PORT}"
NGINX_HTTPS_BACKEND_PORT="${SUBSCRIPTION_NGINX_HTTPS_BACKEND_PORT:-$NGINX_HTTPS_BACKEND_PORT}"
NGINX_HTTPS_PUBLIC_PORT="${SUBSCRIPTION_NGINX_HTTPS_PUBLIC_PORT:-$NGINX_HTTPS_PUBLIC_PORT}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-$LETSENCRYPT_EMAIL}"
ORIGIN_SECRET="${SUBSCRIPTION_ORIGIN_SECRET:-$ORIGIN_SECRET}"
REQUIRE_ORIGIN_SECRET="${SUBSCRIPTION_REQUIRE_ORIGIN_SECRET:-$REQUIRE_ORIGIN_SECRET}"
RESTART_XRAY="${SUBSCRIPTION_RESTART_XRAY:-$RESTART_XRAY}"
ENABLE_CLOUDFLARED="${SUBSCRIPTION_ENABLE_CLOUDFLARED:-$ENABLE_CLOUDFLARED}"
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
server2_host="$SERVER2_HOST"
direct_host="$DIRECT_HOST"
direct_port="$DIRECT_PORT"
public_reality_inbound_tag="$PUBLIC_REALITY_INBOUND_TAG"
public_reality_port="$PUBLIC_REALITY_PORT"
noflow_reality_inbound_tag="$NOFLOW_REALITY_INBOUND_TAG"
noflow_reality_port="$NOFLOW_REALITY_PORT"
xhttp_port="$XHTTP_PORT"
xhttp_path="$XHTTP_PATH"
xhttp_mode="$XHTTP_MODE"
hysteria2_inbound_tag="$HYSTERIA2_INBOUND_TAG"
hysteria2_port="$HYSTERIA2_PORT"
hysteria2_cert_file="$HYSTERIA2_CERT_FILE"
hysteria2_key_file="$HYSTERIA2_KEY_FILE"
hysteria2_masquerade_url="$HYSTERIA2_MASQUERADE_URL"
hysteria2_auth="$HYSTERIA2_AUTH"
public_vless_port="$PUBLIC_VLESS_PORT"
nginx_https_backend_port="$NGINX_HTTPS_BACKEND_PORT"
nginx_https_public_port="$NGINX_HTTPS_PUBLIC_PORT"
subscription_port="$SUBSCRIPTION_PORT"
origin_secret="$ORIGIN_SECRET"
public_key="$PUBLIC_KEY"
reality_private_key="$REALITY_PRIVATE_KEY"
restart_xray="$RESTART_XRAY"
enable_cloudflared="$ENABLE_CLOUDFLARED"
xray_config=/usr/local/etc/xray/config.json

cd "\$server_dir"
tar -xzf /tmp/tgvpn-server2-subscription.tar.gz

apt-get update >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-httpx python3-dotenv curl >/dev/null
if [[ -f /etc/letsencrypt/live/s2.nnqnn.tech/fullchain.pem && -f /etc/letsencrypt/live/s2.nnqnn.tech/privkey.pem ]]; then
  xray_user="\$(systemctl show -p User --value xray.service 2>/dev/null || true)"
  xray_group="\$(systemctl show -p Group --value xray.service 2>/dev/null || true)"
  xray_user="\${xray_user:-nobody}"
  xray_group="\${xray_group:-nogroup}"
  mkdir -p /usr/local/etc/xray/certs
  cp -L /etc/letsencrypt/live/s2.nnqnn.tech/fullchain.pem /usr/local/etc/xray/certs/s2.fullchain.pem
  cp -L /etc/letsencrypt/live/s2.nnqnn.tech/privkey.pem /usr/local/etc/xray/certs/s2.privkey.pem
  chown "\$xray_user:\$xray_group" /usr/local/etc/xray/certs/s2.fullchain.pem /usr/local/etc/xray/certs/s2.privkey.pem
  chmod 640 /usr/local/etc/xray/certs/s2.fullchain.pem /usr/local/etc/xray/certs/s2.privkey.pem
fi
if [[ -z "\$hysteria2_auth" ]]; then
  if [[ -f /root/tgvpn-hysteria2-auth.txt ]]; then
    hysteria2_auth="\$(tr -d '\r\n' < /root/tgvpn-hysteria2-auth.txt)"
  else
    hysteria2_auth="\$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
    printf '%s\n' "\$hysteria2_auth" > /root/tgvpn-hysteria2-auth.txt
    chmod 600 /root/tgvpn-hysteria2-auth.txt
  fi
fi

configure_hysteria2_daemon() {
  local cert_dir="/etc/letsencrypt/live/\$direct_host"
  if [[ ! -f "\$cert_dir/fullchain.pem" || ! -f "\$cert_dir/privkey.pem" ]]; then
    echo "Skipping Hysteria2 daemon setup: Let's Encrypt certificate for \$direct_host is not available yet."
    return 0
  fi

  if ! command -v hysteria >/dev/null 2>&1; then
    curl -fsSL https://get.hy2.sh/ | bash
  fi

  mkdir -p /etc/hysteria/certs
  cp -L "\$cert_dir/fullchain.pem" /etc/hysteria/certs/s2.fullchain.pem
  cp -L "\$cert_dir/privkey.pem" /etc/hysteria/certs/s2.privkey.pem
  if id hysteria >/dev/null 2>&1; then
    chown -R hysteria:hysteria /etc/hysteria/certs
  else
    chown -R root:root /etc/hysteria/certs
  fi
  chmod 750 /etc/hysteria/certs
  chmod 640 /etc/hysteria/certs/s2.fullchain.pem /etc/hysteria/certs/s2.privkey.pem

  cat > /etc/hysteria/config.yaml <<HYSTERIA_CONFIG
listen: :\$hysteria2_port

tls:
  cert: /etc/hysteria/certs/s2.fullchain.pem
  key: /etc/hysteria/certs/s2.privkey.pem

auth:
  type: password
  password: "\$hysteria2_auth"

resolver:
  type: udp
  udp:
    addr: 1.1.1.1:53

outbounds:
  - name: direct
    type: direct
    direct:
      mode: 4

masquerade:
  type: proxy
  proxy:
    url: \$hysteria2_masquerade_url
    rewriteHost: true
HYSTERIA_CONFIG

  systemctl daemon-reload
  systemctl enable --now hysteria-server.service
  systemctl restart hysteria-server.service
  sleep 1
  systemctl is-active --quiet hysteria-server.service
}

configure_hysteria2_daemon

printf 'tcp_bbr\n' > /etc/modules-load.d/tgvpn-bbr.conf
modprobe tcp_bbr 2>/dev/null || true
cat > /etc/sysctl.d/99-tgvpn-network.conf <<SYSCTL
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
net.ipv4.tcp_mtu_probing=1
net.ipv4.tcp_fastopen=3
SYSCTL
sysctl --system >/dev/null || true
python3 -m compileall -q app scripts

if command -v nginx >/dev/null 2>&1; then
  if [[ -f /etc/nginx/sites-available/tgvpn-subscription.conf ]]; then
    sed -i \
      "s/listen 127\\.0\\.0\\.1:8443 ssl http2;/listen 127.0.0.1:\$nginx_https_backend_port ssl http2;/g; s/listen 127\\.0\\.0\\.1:18443 ssl http2;/listen 127.0.0.1:\$nginx_https_backend_port ssl http2;/g" \
      /etc/nginx/sites-available/tgvpn-subscription.conf
    if ! grep -q "listen \$nginx_https_public_port ssl http2;" /etc/nginx/sites-available/tgvpn-subscription.conf; then
      sed -i "/listen 127\\.0\\.0\\.1:\$nginx_https_backend_port ssl http2;/a\\    listen \$nginx_https_public_port ssl http2;" /etc/nginx/sites-available/tgvpn-subscription.conf
    fi
  fi
  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx || true
  fi
fi

before_hash="\$(sha256sum "\$xray_config" | awk '{print \$1}')"
python3 "\$server_dir/scripts/configure_server2_xray_api.py" \
  --config "\$xray_config" \
	  --api-port 10085 \
	  --inbound-tag upstream-in \
	  --direct-port "\$direct_port" \
	  --public-reality-inbound-tag "\$public_reality_inbound_tag" \
	  --public-reality-port "\$public_reality_port" \
	  --public-reality-server-name www.yandex.ru \
	  --public-reality-server-name yandex.ru \
	  --public-reality-dest www.yandex.ru:443 \
	  --noflow-reality-inbound-tag "\$noflow_reality_inbound_tag" \
	  --noflow-reality-port "\$noflow_reality_port" \
	  --noflow-reality-server-name www.yandex.ru \
	  --noflow-reality-server-name yandex.ru \
	  --noflow-reality-dest www.yandex.ru:443 \
	  --cdn-ws-inbound-tag cdn-ws-in \
	  --cdn-ws-port 10086 \
	  --cdn-ws-path /kvpn-ws \
	  --xhttp-inbound-tag xhttp-in \
	  --xhttp-port "\$xhttp_port" \
	  --xhttp-path "\$xhttp_path" \
	  --xhttp-mode "\$xhttp_mode" \
  --server-name www.cloudflare.com \
  --server-name www.yandex.ru \
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
effective_public_key="\$(python3 - <<'PY'
from pathlib import Path
import json
import re
import subprocess
cfg = json.loads(Path("/usr/local/etc/xray/config.json").read_text(encoding="utf-8"))
inbound = next(i for i in cfg.get("inbounds", []) if i.get("tag") == "direct-reality-8443")
private_key = inbound["streamSettings"]["realitySettings"].get("privateKey", "")
out = subprocess.check_output(["xray", "x25519", "-i", private_key], text=True)
for pattern in (
    r"Password\s*\(PublicKey\):\s*(\S+)",
    r"Password:\s*(\S+)",
    r"Public key:\s*(\S+)",
    r"PublicKey:\s*(\S+)",
):
    match = re.search(pattern, out)
    if match:
        print(match.group(1))
        raise SystemExit(0)
raise SystemExit("cannot derive REALITY public key")
PY
)"
if [[ -n "\$public_key" && "\$public_key" != "\$effective_public_key" ]]; then
  echo "WARNING: provided SUBSCRIPTION_SERVER2_VLESS_PBK does not match server2 REALITY private key; using derived public key." >&2
fi

	profile_host="\$server2_host"
	profile_port="\$public_vless_port"
	profile_security=reality
	profile_type=tcp
	profile_sni=www.yandex.ru
	profile_flow=xtls-rprx-vision
	profile_fp=chrome
	profile_pbk="\$effective_public_key"
	profile_sid=a1b2c3d4e5f6a7b8
	profile_path=
	
	cat > "\$server_dir/.env.subscription" <<ENV
LOG_LEVEL=INFO
SUBSCRIPTION_LISTEN_HOST=127.0.0.1
SUBSCRIPTION_LISTEN_PORT=\$subscription_port
SUBSCRIPTION_SNAPSHOT_PATH=/var/lib/tgvpn/subscription_snapshot.json
SUBSCRIPTION_ORIGIN_SECRET=\$origin_secret
SUBSCRIPTION_RESPONSE_FORMAT=xray_json
SUBSCRIPTION_PRODUCT=kVPN
SUBSCRIPTION_PUBLIC_BASE_URL=https://\$direct_host:\$nginx_https_public_port
SUBSCRIPTION_PROFILE_TITLE=kVPN @kkVPNrobot
SUBSCRIPTION_UPDATE_INTERVAL_HOURS=1
SUBSCRIPTION_TRAFFIC_TOTAL_BYTES=0
SUBSCRIPTION_ANNOUNCE_TEXT=kVPN: подписка обновляется автоматически.
SUBSCRIPTION_ANNOUNCE_URL=https://t.me/kvpnpublic
SUBSCRIPTION_PROFILE_WEB_PAGE_URL=https://t.me/kvpnpublic
VLESS_PUBLIC_HOST=\$profile_host
VLESS_PUBLIC_PORT=\$profile_port
VLESS_SECURITY=\$profile_security
VLESS_TYPE=\$profile_type
VLESS_SNI=\$profile_sni
VLESS_FLOW=\$profile_flow
VLESS_FP=\$profile_fp
VLESS_PBK=\$profile_pbk
VLESS_SID=\$profile_sid
VLESS_PATH=\$profile_path
VLESS_XHTTP_MODE=\$xhttp_mode
VLESS_HEADER_TYPE=
VLESS_REMARK_PREFIX=kVPN
VLESS_FALLBACK_PUBLIC_HOST=
VLESS_FALLBACK_PUBLIC_PORT=\$public_vless_port
VLESS_FALLBACK_SECURITY=reality
VLESS_FALLBACK_TYPE=tcp
VLESS_FALLBACK_SNI=yandex.ru
VLESS_FALLBACK_FLOW=xtls-rprx-vision
VLESS_FALLBACK_FP=chrome
VLESS_FALLBACK_PBK=
VLESS_FALLBACK_SID=a1b2c3d4e5f6a7b8
VLESS_FALLBACK_PATH=
VLESS_FALLBACK_XHTTP_MODE=packet-up
VLESS_NOFLOW_PUBLIC_HOST=\$server2_host
VLESS_NOFLOW_PUBLIC_PORT=\$noflow_reality_port
VLESS_NOFLOW_SECURITY=reality
VLESS_NOFLOW_TYPE=tcp
VLESS_NOFLOW_SNI=www.yandex.ru
VLESS_NOFLOW_FP=chrome
VLESS_NOFLOW_PBK=\$effective_public_key
VLESS_NOFLOW_SID=a1b2c3d4e5f6a7b8
VLESS_NOFLOW_PATH=
VLESS_NOFLOW_XHTTP_MODE=packet-up
VLESS_XHTTP_PUBLIC_HOST=\$direct_host
VLESS_XHTTP_PUBLIC_PORT=\$nginx_https_public_port
VLESS_XHTTP_SECURITY=tls
VLESS_XHTTP_TYPE=xhttp
VLESS_XHTTP_SNI=\$direct_host
VLESS_XHTTP_FP=chrome
VLESS_XHTTP_PATH=\$xhttp_path
VLESS_XHTTP_XHTTP_MODE=\$xhttp_mode
HYSTERIA2_PUBLIC_HOST=\$server2_host
HYSTERIA2_PUBLIC_PORT=\$hysteria2_port
HYSTERIA2_SNI=\$direct_host
HYSTERIA2_FP=chrome
HYSTERIA2_AUTH=\$hysteria2_auth
HYSTERIA2_UDP_IDLE_TIMEOUT=60
VLESS_LEGACY_PUBLIC_HOST=
VLESS_LEGACY_PUBLIC_PORT=8443
VLESS_LEGACY_SECURITY=reality
VLESS_LEGACY_TYPE=tcp
VLESS_LEGACY_SNI=yandex.ru
VLESS_LEGACY_FLOW=xtls-rprx-vision
VLESS_LEGACY_FP=chrome
VLESS_LEGACY_PBK=
VLESS_LEGACY_SID=c0ba09b546ccb4a8
VLESS_LEGACY_PATH=
VLESS_LEGACY_XHTTP_MODE=packet-up
SUPPORT_URL=https://t.me/kvpn_support
WHITELIST_PROFILE_URL=https://vpn.nnqnn.tech/
WHITELIST_SOURCE_URL=https://raw.githubusercontent.com/zieng2/wl/main/vless_universal.txt
WHITELIST_MAX_NODES=300
MAIN_VPN_BRIDGE_ENABLED=false
MAIN_VPN_BRIDGE_MAX_NODES=8
SUBSCRIPTION_ENABLE_CLOUDFLARED=\$enable_cloudflared
WHITELIST_CACHE_SECONDS=300
WHITELIST_FETCH_TIMEOUT_SECONDS=4
WHITELIST_PROFILE_CACHE_PATH=/var/lib/tgvpn/whitelist_profile_cache.json
ENV
chmod 600 "\$server_dir/.env.subscription"
written_pbk="\$(grep -E '^VLESS_PBK=' "\$server_dir/.env.subscription" | head -n1 | cut -d= -f2-)"
if [[ "\$written_pbk" != "\$effective_public_key" ]]; then
  echo "VLESS_PBK mismatch after writing .env.subscription" >&2
  exit 1
fi
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
	cp "\$server_dir/deploy/systemd/tgvpn-cloudflared.service" /etc/systemd/system/tgvpn-cloudflared.service
	chmod +x "\$server_dir/scripts/run_cloudflared_quick_tunnel.sh"
	systemctl daemon-reload
	systemctl enable --now tgvpn-subscription.service
systemctl restart tgvpn-subscription.service
for _ in \$(seq 1 20); do
  if systemctl is-active --quiet tgvpn-subscription.service \
    && curl -fsS "http://127.0.0.1:\$subscription_port/healthz" >/dev/null; then
    subscription_ready=true
    break
  fi
  sleep 1
done
if [[ "\${subscription_ready:-false}" != "true" ]]; then
  systemctl status tgvpn-subscription.service --no-pager || true
  journalctl -u tgvpn-subscription.service -n 80 --no-pager || true
  exit 1
fi

if command -v nginx >/dev/null 2>&1; then
  if [[ -f /etc/nginx/stream-conf.d/tgvpn-sni.conf ]]; then
    mv /etc/nginx/stream-conf.d/tgvpn-sni.conf "/etc/nginx/stream-conf.d/tgvpn-sni.conf.disabled-\$(date +%Y%m%d%H%M%S)"
  fi
  nginx -t
  systemctl reload nginx || true
fi

if [[ "\$xray_config_changed" == "true" || "\$restart_xray" == "true" ]]; then
  echo "restarting xray after config change or explicit request"
  systemctl restart xray
  sleep 3
else
  echo "xray config unchanged; checking without restart"
fi
	systemctl is-active --quiet xray
	python3 - <<'PY'
from pathlib import Path
import json
import subprocess
import tempfile

snapshot_path = Path("/var/lib/tgvpn/subscription_snapshot.json")
if not snapshot_path.exists():
    raise SystemExit(0)
snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
users = snapshot.get("users", {})
if not isinstance(users, dict):
    raise SystemExit(0)

expected = {}
managed = []
for user in users.values():
    if not isinstance(user, dict):
        continue
    telegram_id = user.get("telegram_id")
    uuid = user.get("uuid")
    if not telegram_id or not uuid:
        continue
    email = f"user-{telegram_id}@vpn.local"
    managed.append(email)
    if user.get("main_vpn_active"):
        expected[email] = str(uuid)

payload = {
    "xray_bin_path": "xray",
    "xray_api_server": "127.0.0.1:10085",
    "xray_api_timeout_seconds": 5,
    "command_timeout_seconds": 120,
    "xray_config_path": "/usr/local/etc/xray/config.json",
    "xray_inbound_tag": "direct-reality-8443",
    "xray_extra_inbound_tags": ["upstream-in", "cdn-ws-in", "xhttp-in", "direct-reality-noflow-8443"],
    "xray_flow_inbound_tags": ["direct-reality-8443", "upstream-in"],
    "persist_users_in_config": False,
    "vless_flow": "xtls-rprx-vision",
    "expected": expected,
    "managed_emails": managed,
}
with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".json") as f:
    json.dump(payload, f, ensure_ascii=False)
    payload_path = f.name
try:
    subprocess.run(
        ["python3", "/home/tgvpn/scripts/reconcile_server2_xray_users.py", "--payload", payload_path],
        check=True,
    )
finally:
    Path(payload_path).unlink(missing_ok=True)
PY
	xray api inboundusercount --server=127.0.0.1:10085 --timeout=5 -tag=direct-reality-8443 --json >/dev/null
	xray api inboundusercount --server=127.0.0.1:10085 --timeout=5 -tag=upstream-in --json >/dev/null
	xray api inboundusercount --server=127.0.0.1:10085 --timeout=5 -tag=cdn-ws-in --json >/dev/null
	xray api inboundusercount --server=127.0.0.1:10085 --timeout=5 -tag=xhttp-in --json >/dev/null
	xray api inboundusercount --server=127.0.0.1:10085 --timeout=5 -tag=direct-reality-noflow-8443 --json >/dev/null
	if [[ "\$enable_cloudflared" == "true" ]]; then
	  if ! command -v cloudflared >/dev/null 2>&1; then
	    curl -fsSL -o /tmp/cloudflared-linux-amd64.deb \
	      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
	    DEBIAN_FRONTEND=noninteractive apt-get install -y /tmp/cloudflared-linux-amd64.deb >/dev/null
	  fi
	  systemctl enable tgvpn-cloudflared.service
	  systemctl restart tgvpn-cloudflared.service
	else
	  systemctl disable --now tgvpn-cloudflared.service >/dev/null 2>&1 || true
	  rm -f /var/lib/tgvpn/cloudflared_quick_url
	fi
	for _ in \$(seq 1 20); do
	  if systemctl is-active --quiet tgvpn-subscription.service \
	    && grep -q '^VLESS_TYPE=tcp$' "\$server_dir/.env.subscription" \
	    && grep -q '^VLESS_SECURITY=reality$' "\$server_dir/.env.subscription" \
	    && grep -q "^VLESS_PUBLIC_HOST=\${server2_host}\$" "\$server_dir/.env.subscription"; then
	    break
	  fi
	  sleep 1
	done

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
direct_port="$DIRECT_PORT"
public_reality_port="$PUBLIC_REALITY_PORT"
xhttp_port="$XHTTP_PORT"
xhttp_path="$XHTTP_PATH"
public_vless_port="$PUBLIC_VLESS_PORT"
nginx_https_backend_port="$NGINX_HTTPS_BACKEND_PORT"
nginx_https_public_port="$NGINX_HTTPS_PUBLIC_PORT"
subscription_port="$SUBSCRIPTION_PORT"
letsencrypt_email="$LETSENCRYPT_EMAIL"
hysteria2_port="$HYSTERIA2_PORT"
hysteria2_masquerade_url="$HYSTERIA2_MASQUERADE_URL"
hysteria2_auth="$HYSTERIA2_AUTH"
if [[ -z "\$hysteria2_auth" && -f /root/tgvpn-hysteria2-auth.txt ]]; then
  hysteria2_auth="\$(tr -d '\r\n' < /root/tgvpn-hysteria2-auth.txt)"
fi

apt-get update >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx libnginx-mod-stream certbot python3-certbot-nginx >/dev/null
mkdir -p /var/www/html
mkdir -p /etc/nginx/stream-conf.d
rm -f /etc/nginx/sites-enabled/tgvpn-subscription.conf
if ! grep -q 'include /etc/nginx/stream-conf.d/\*.conf;' /etc/nginx/nginx.conf; then
  printf '\ninclude /etc/nginx/stream-conf.d/*.conf;\n' >> /etc/nginx/nginx.conf
fi

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

if [[ -n "\$hysteria2_auth" ]]; then
  if ! command -v hysteria >/dev/null 2>&1; then
    curl -fsSL https://get.hy2.sh/ | bash
  fi
  mkdir -p /etc/hysteria/certs
  cp -L "/etc/letsencrypt/live/\$direct_host/fullchain.pem" /etc/hysteria/certs/s2.fullchain.pem
  cp -L "/etc/letsencrypt/live/\$direct_host/privkey.pem" /etc/hysteria/certs/s2.privkey.pem
  if id hysteria >/dev/null 2>&1; then
    chown -R hysteria:hysteria /etc/hysteria/certs
  else
    chown -R root:root /etc/hysteria/certs
  fi
  chmod 750 /etc/hysteria/certs
  chmod 640 /etc/hysteria/certs/s2.fullchain.pem /etc/hysteria/certs/s2.privkey.pem
  cat > /etc/hysteria/config.yaml <<HYSTERIA_CONFIG
listen: :\$hysteria2_port

tls:
  cert: /etc/hysteria/certs/s2.fullchain.pem
  key: /etc/hysteria/certs/s2.privkey.pem

auth:
  type: password
  password: "\$hysteria2_auth"

resolver:
  type: udp
  udp:
    addr: 1.1.1.1:53

outbounds:
  - name: direct
    type: direct
    direct:
      mode: 4

masquerade:
  type: proxy
  proxy:
    url: \$hysteria2_masquerade_url
    rewriteHost: true
HYSTERIA_CONFIG
  systemctl daemon-reload
  systemctl enable --now hysteria-server.service
  systemctl restart hysteria-server.service
  sleep 1
  systemctl is-active --quiet hysteria-server.service
fi

cp "\$server_dir/deploy/nginx/s2.nnqnn.tech.conf" /etc/nginx/sites-available/tgvpn-subscription.conf
escaped_xhttp_path="\$(printf '%s' "\$xhttp_path" | sed 's/[\/&]/\\\\&/g')"
sed -i "s/s2\\.nnqnn\\.tech/\$direct_host/g; s/127\\.0\\.0\\.1:8088/127.0.0.1:\$subscription_port/g; s/127\\.0\\.0\\.1:8443/127.0.0.1:\$nginx_https_backend_port/g; s/127\\.0\\.0\\.1:18443/127.0.0.1:\$nginx_https_backend_port/g; s/127\\.0\\.0\\.1:10087/127.0.0.1:\$xhttp_port/g; s/\\/kvpn-xhttp/\$escaped_xhttp_path/g" /etc/nginx/sites-available/tgvpn-subscription.conf
if ! grep -q "listen \$nginx_https_public_port ssl http2;" /etc/nginx/sites-available/tgvpn-subscription.conf; then
  sed -i "/listen 127\\.0\\.0\\.1:\$nginx_https_backend_port ssl http2;/a\\    listen \$nginx_https_public_port ssl http2;" /etc/nginx/sites-available/tgvpn-subscription.conf
fi
ln -sf /etc/nginx/sites-available/tgvpn-subscription.conf /etc/nginx/sites-enabled/tgvpn-subscription.conf
rm -f /etc/nginx/sites-enabled/tgvpn-subscription-bootstrap.conf
if [[ -f /etc/nginx/stream-conf.d/tgvpn-sni.conf ]]; then
  mv /etc/nginx/stream-conf.d/tgvpn-sni.conf "/etc/nginx/stream-conf.d/tgvpn-sni.conf.disabled-\$(date +%Y%m%d%H%M%S)"
fi
nginx -t
systemctl reload nginx
curl -fsS "https://\$direct_host:\$nginx_https_public_port/healthz" >/dev/null
timeout 5 bash -c "</dev/tcp/127.0.0.1/\$nginx_https_backend_port"
python3 "\$server_dir/scripts/smoke_server2_direct_vless.py"
REMOTE_NGINX
fi

log "Done"
