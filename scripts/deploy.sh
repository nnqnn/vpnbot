#!/usr/bin/env bash

set -Eeuo pipefail

REMOTE="${REMOTE:-vpnbot}"
BRANCH="${BRANCH:-main}"
SERVER_HOST="${SERVER_HOST:-5.129.213.120}"
SERVER_USER="${SERVER_USER:-root}"
SERVER_DIR="${SERVER_DIR:-/home/tgvpn}"
SERVER_REMOTE="${SERVER_REMOTE:-origin}"
SERVICE="${SERVICE:-tgvpn-bot.service}"
DEPLOY_SERVER2_SUBSCRIPTION="${DEPLOY_SERVER2_SUBSCRIPTION:-true}"
SUBSCRIPTION_RESTART_XRAY="${SUBSCRIPTION_RESTART_XRAY:-false}"
AUTO_COMMIT_MESSAGE="${AUTO_COMMIT_MESSAGE:-Auto deploy $(date +'%Y-%m-%d %H:%M:%S')}"

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

ensure_server_password() {
  if [[ -n "${TGVPN_SERVER_PASSWORD:-}" ]]; then
    export TGVPN_SERVER_PASSWORD
    return
  fi

  if TGVPN_SERVER_PASSWORD="$(load_env_value TGVPN_SERVER_PASSWORD .env)"; then
    export TGVPN_SERVER_PASSWORD
    return
  fi

  echo "TGVPN_SERVER_PASSWORD is not set and was not found in .env" >&2
  exit 1
}

auto_commit_if_needed() {
  if [[ -z "$(git status --porcelain)" ]]; then
    log "No local changes to commit"
    return
  fi

  log "Committing local changes"
  git add -A
  if git diff --cached --quiet --ignore-submodules --; then
    log "No staged changes after git add -A"
    return
  fi
  git commit -m "$AUTO_COMMIT_MESSAGE"
}

require_cmd git
require_cmd python3
require_cmd sshpass
require_cmd ssh

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$current_branch" != "$BRANCH" ]]; then
  echo "Current branch is '$current_branch', expected '$BRANCH'." >&2
  exit 1
fi

log "Preparing local virtualenv"
if [[ ! -x ".venv/bin/python" ]]; then
  python3 -m venv .venv
fi
".venv/bin/python" -m pip install --upgrade pip
".venv/bin/python" -m pip install -r requirements.txt -r requirements-dev.txt

log "Running local checks"
".venv/bin/python" -m pytest -q
".venv/bin/python" -m compileall -q app scripts tests

ensure_server_password
auto_commit_if_needed

log "Pushing ${BRANCH} to ${REMOTE}"
git push "$REMOTE" "$BRANCH"

if [[ "$DEPLOY_SERVER2_SUBSCRIPTION" == "true" ]]; then
  log "Deploying server2 subscription service"
  SUBSCRIPTION_RESTART_XRAY="$SUBSCRIPTION_RESTART_XRAY" ./scripts/deploy_server2_subscription.sh
else
  log "Skipping server2 subscription deploy"
fi

log "Deploying on ${SERVER_USER}@${SERVER_HOST}:${SERVER_DIR}"
SSHPASS="$TGVPN_SERVER_PASSWORD" sshpass -e ssh \
  -o StrictHostKeyChecking=accept-new \
  "${SERVER_USER}@${SERVER_HOST}" \
  "bash -s -- '$SERVER_DIR' '$BRANCH' '$SERVICE' '$SERVER_REMOTE' '$REMOTE'" <<'REMOTE_SCRIPT'
set -Eeuo pipefail

server_dir="$1"
branch="$2"
service="$3"
server_remote="$4"
fallback_remote="$5"

log() {
  printf '\n==> %s\n' "$*"
}

cd "$server_dir"

upsert_env_value() {
  local key="$1"
  local value="$2"
  local env_file=".env"
  if [[ ! -f "$env_file" ]]; then
    echo "$env_file not found on server." >&2
    exit 1
  fi
  if grep -qE "^[[:space:]]*${key}=" "$env_file"; then
    python3 - "$env_file" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
out = []
changed = False
for line in lines:
    if line.lstrip().startswith(f"{key}="):
        out.append(f"{key}={value}")
        changed = True
    else:
        out.append(line)
if not changed:
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$env_file"
  fi
}

ensure_csv_env_contains() {
  local key="$1"
  shift
  local raw current next tag
  raw="$(grep -E "^[[:space:]]*${key}=" .env | tail -n 1 || true)"
  current="${raw#*=}"
  current="${current%$'\r'}"
  current="${current// /}"
  next="$current"
  for tag in "$@"; do
    if [[ ",$next," != *",$tag,"* ]]; then
      if [[ -z "$next" ]]; then
        next="$tag"
      else
        next="$next,$tag"
      fi
    fi
  done
  if [[ "$next" != "$current" ]]; then
    log "Updating ${key} in .env: ${next}"
    upsert_env_value "$key" "$next"
  fi
}

upsert_env_value XRAY_INBOUND_TAG direct-reality-8443
ensure_csv_env_contains XRAY_EXTRA_INBOUND_TAGS upstream-in cdn-ws-in xhttp-in
ensure_csv_env_contains XRAY_EXTRA_INBOUND_TAGS direct-reality-noflow-8443 hysteria2-udp-443
upsert_env_value XRAY_FLOW_INBOUND_TAGS direct-reality-8443,upstream-in
upsert_env_value VLESS_PUBLIC_HOST 89.125.50.96
upsert_env_value VLESS_PUBLIC_PORT 443
upsert_env_value VLESS_SECURITY reality
upsert_env_value VLESS_TYPE tcp
upsert_env_value VLESS_SNI www.yandex.ru
upsert_env_value VLESS_FLOW xtls-rprx-vision
upsert_env_value VLESS_PATH ""
upsert_env_value VLESS_XHTTP_MODE packet-up
upsert_env_value SUBSCRIPTION_PUBLIC_BASE_URL https://s2.nnqnn.tech:8444
upsert_env_value SUBSCRIPTION_NGINX_HTTPS_PUBLIC_PORT 8444
upsert_env_value SUBSCRIPTION_PUBLIC_REALITY_PORT 443
upsert_env_value SUBSCRIPTION_PUBLIC_VLESS_PORT 443
upsert_env_value SUBSCRIPTION_NOFLOW_REALITY_INBOUND_TAG direct-reality-noflow-8443
upsert_env_value SUBSCRIPTION_NOFLOW_REALITY_PORT 8443
upsert_env_value SUBSCRIPTION_HYSTERIA2_INBOUND_TAG hysteria2-udp-443
upsert_env_value SUBSCRIPTION_HYSTERIA2_PORT 443

if ! systemctl is-active --quiet xray; then
  echo "xray.service is not active. Refusing to deploy bot changes." >&2
  systemctl status xray --no-pager || true
  exit 1
fi

remote_name="$server_remote"
if ! git remote get-url "$remote_name" >/dev/null 2>&1; then
  if git remote get-url "$fallback_remote" >/dev/null 2>&1; then
    remote_name="$fallback_remote"
  else
    remote_name="$(git remote | head -n1)"
  fi
fi

if [[ -z "$remote_name" ]]; then
  echo "No git remote configured on server." >&2
  exit 1
fi

previous_commit="$(git rev-parse HEAD)"
server_stash_name=""
server_stash_ref=""

stash_server_changes_if_needed() {
  local status
  status="$(git status --porcelain --untracked-files=all)"
  if [[ -z "$status" ]]; then
    log "Server working tree is clean"
    return
  fi

  server_stash_name="pre-deploy-${branch}-$(date +'%Y%m%d_%H%M%S')"
  log "Saving server working tree changes to git stash"
  printf '%s\n' "$status"
  git stash push --include-untracked -m "$server_stash_name"
  server_stash_ref="$(git stash list --format='%gd' -n 1)"

  if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
    echo "Server working tree is still dirty after stash. Refusing to deploy." >&2
    git status --short
    exit 1
  fi

  echo "Server changes saved as stash: ${server_stash_ref} (${server_stash_name})"
}

rollback() {
  local reason="$1"
  echo "$reason" >&2
  echo "Rolling back to $previous_commit" >&2
  git reset --hard "$previous_commit"
  if [[ -n "$server_stash_ref" ]]; then
    echo "Restoring server pre-deploy stash: $server_stash_ref ($server_stash_name)" >&2
    git stash apply "$server_stash_ref" || true
  fi
  if [[ -x ".venv/bin/python" ]]; then
    ".venv/bin/python" -m pip install -r requirements.txt || true
    ".venv/bin/python" -m compileall -q app scripts tests || true
  fi
  systemctl restart "$service" || true
  sleep 3
  if ! systemctl is-active --quiet "$service"; then
    systemctl status "$service" --no-pager || true
  fi
}

stash_server_changes_if_needed

log "Fetching ${branch} from ${remote_name}"
git fetch "$remote_name" "$branch"
git checkout "$branch"
git pull --ff-only "$remote_name" "$branch"

log "Updating server virtualenv"
if [[ ! -x ".venv/bin/python" ]]; then
  python3 -m venv .venv
fi
".venv/bin/python" -m pip install --upgrade pip
".venv/bin/python" -m pip install -r requirements.txt
".venv/bin/python" -m compileall -q app scripts tests

log "Restarting ${service}"
restart_started_at="$(date '+%Y-%m-%d %H:%M:%S')"
if ! systemctl restart "$service"; then
  rollback "Failed to restart ${service}."
  exit 1
fi

sleep 3
if ! systemctl is-active --quiet "$service"; then
  rollback "${service} is not active after restart."
  exit 1
fi

polling_ready=false
for _ in $(seq 1 45); do
  if journalctl -u "$service" --since "$restart_started_at" --no-pager | grep -q "Bot is starting polling"; then
    polling_ready=true
    break
  fi
  sleep 1
done
if [[ "$polling_ready" != "true" ]]; then
  rollback "${service} did not reach polling within 45 seconds."
  exit 1
fi

log "Syncing subscription snapshot to server2"
if ! ".venv/bin/python" scripts/sync_subscription_snapshot.py </dev/null; then
  rollback "Failed to sync subscription snapshot after deploy."
  exit 1
fi

log "Syncing Xray runtime users to server2"
if ! ".venv/bin/python" scripts/resync_xray_runtime.py </dev/null; then
  rollback "Failed to sync Xray runtime after deploy."
  exit 1
fi

log "Verifying exported subscription uses Reality on public 443"
token="$(
  ".venv/bin/python" - <<'PY'
import json
from pathlib import Path

snapshot = json.loads(Path("logs/subscription_snapshot.json").read_text(encoding="utf-8"))
for token, user in snapshot.get("users", {}).items():
    if isinstance(user, dict) and user.get("main_vpn_active"):
        print(token)
        break
PY
)"
if [[ -z "$token" ]]; then
  rollback "No active subscription token found for Reality 443 verification."
  exit 1
fi
subscription_body="$(mktemp)"
if ! curl -fsS "https://s2.nnqnn.tech:8444/sub/kVPN/${token}?format=raw" -o "$subscription_body"; then
  rm -f "$subscription_body"
  rollback "Failed to fetch raw subscription for verification."
  exit 1
fi
if ! ".venv/bin/python" - "$subscription_body" <<'PY'
import json
import sys
from pathlib import Path

configs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(configs, list):
    raise SystemExit("subscription body is not a profile list")
main = next((item for item in configs if isinstance(item, dict) and item.get("remarks", "").endswith("Основной VPN")), None)
if main is None:
    raise SystemExit("main VPN profile was not found")
outbounds = main.get("outbounds")
if not isinstance(outbounds, list) or not outbounds:
    raise SystemExit("main VPN profile has no outbounds")
proxy = outbounds[0]
vnext = proxy.get("settings", {}).get("vnext", [])
if not vnext:
    raise SystemExit("main outbound has no vnext")
server = vnext[0]
users = server.get("users", [])
stream = proxy.get("streamSettings", {})
reality = stream.get("realitySettings", {})
if server.get("address") != "89.125.50.96":
    raise SystemExit(f"unexpected address: {server.get('address')}")
if int(server.get("port") or 0) != 443:
    raise SystemExit(f"unexpected port: {server.get('port')}")
if stream.get("network") != "tcp":
    raise SystemExit(f"unexpected network: {stream.get('network')}")
if stream.get("security") != "reality":
    raise SystemExit(f"unexpected security: {stream.get('security')}")
if reality.get("serverName") != "www.yandex.ru":
    raise SystemExit(f"unexpected serverName: {reality.get('serverName')}")
if not users or users[0].get("flow") != "xtls-rprx-vision":
    raise SystemExit("main user flow is not xtls-rprx-vision")
remarks = {str(item.get("remarks")) for item in configs if isinstance(item, dict)}
required_suffixes = {
    "Основной VPN Reality no-flow",
    "Основной VPN XHTTP",
    "Основной VPN Hysteria2",
}
for suffix in required_suffixes:
    if not any(remark.endswith(suffix) for remark in remarks):
        raise SystemExit(f"missing subscription profile: {suffix}")
PY
then
  rm -f "$subscription_body"
  rollback "Raw subscription still does not use Reality public 443."
  exit 1
fi
rm -f "$subscription_body"

log "Deploy completed"
systemctl --no-pager --full status "$service" | sed -n '1,12p'
REMOTE_SCRIPT

log "Done"
