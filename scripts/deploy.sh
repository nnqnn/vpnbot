#!/usr/bin/env bash

set -Eeuo pipefail

REMOTE="${REMOTE:-vpnbot}"
BRANCH="${BRANCH:-main}"
SERVER_HOST="${SERVER_HOST:-5.129.213.120}"
SERVER_USER="${SERVER_USER:-root}"
SERVER_DIR="${SERVER_DIR:-/home/tgvpn}"
SERVER_REMOTE="${SERVER_REMOTE:-origin}"
SERVICE="${SERVICE:-tgvpn-bot.service}"
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
".venv/bin/python" -m compileall -q app scripts

ensure_server_password
auto_commit_if_needed

log "Pushing ${BRANCH} to ${REMOTE}"
git push "$REMOTE" "$BRANCH"

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

if ! git diff --quiet --ignore-submodules -- || ! git diff --cached --quiet --ignore-submodules --; then
  echo "Server tracked working tree is dirty. Refusing to deploy." >&2
  git status --short
  exit 1
fi

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

rollback() {
  local reason="$1"
  echo "$reason" >&2
  echo "Rolling back to $previous_commit" >&2
  git reset --hard "$previous_commit"
  if [[ -x ".venv/bin/python" ]]; then
    ".venv/bin/python" -m pip install -r requirements.txt || true
    ".venv/bin/python" -m compileall -q app scripts || true
  fi
  systemctl restart "$service" || true
  sleep 3
  if ! systemctl is-active --quiet "$service"; then
    systemctl status "$service" --no-pager || true
  fi
}

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
".venv/bin/python" -m compileall -q app scripts

log "Restarting ${service}"
if ! systemctl restart "$service"; then
  rollback "Failed to restart ${service}."
  exit 1
fi

sleep 3
if ! systemctl is-active --quiet "$service"; then
  rollback "${service} is not active after restart."
  exit 1
fi

log "Deploy completed"
systemctl --no-pager --full status "$service" | sed -n '1,12p'
REMOTE_SCRIPT

log "Done"
