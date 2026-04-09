#!/usr/bin/env bash

set -u
set -o pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

TARGET_TG=""
MAX_USERS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tg)
      TARGET_TG="${2:-}"
      shift 2
      ;;
    --max-users)
      MAX_USERS="${2:-0}"
      shift 2
      ;;
    -h|--help)
      cat <<'HELP'
Usage:
  scripts/debug_vpn_incident.sh [--tg <telegram_id>] [--max-users <N>]

Examples:
  scripts/debug_vpn_incident.sh
  scripts/debug_vpn_incident.sh --tg 2045084149
  scripts/debug_vpn_incident.sh --max-users 30
HELP
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f ".env" ]]; then
  echo ".env not found in $ROOT_DIR" >&2
  exit 1
fi

env_get() {
  local key="$1"
  local default="${2:-}"
  local line
  line="$(rg -N "^${key}=" ".env" | tail -n1 || true)"
  if [[ -z "$line" ]]; then
    echo "$default"
    return
  fi
  echo "${line#*=}"
}

print_section() {
  echo
  echo "==== $1 ===="
}

now_utc() {
  date -u +"%Y-%m-%d %H:%M:%S UTC"
}

run_checked() {
  local title="$1"
  shift
  echo "--- $title"
  if ! "$@"; then
    echo "Command failed: $*" >&2
    return 1
  fi
}

XRAY_API_SERVER="$(env_get "XRAY_API_SERVER" "127.0.0.1:10085")"
XRAY_API_TIMEOUT_SECONDS="$(env_get "XRAY_API_TIMEOUT_SECONDS" "5")"
XRAY_INBOUND_TAG="$(env_get "XRAY_INBOUND_TAG" "vless-in")"
XRAY_CONFIG_PATH="$(env_get "XRAY_CONFIG_PATH" "/usr/local/etc/xray/config.json")"
MAX_DEVICES="$(env_get "MAX_DEVICES" "4")"

print_section "Context"
echo "Time:        $(now_utc)"
echo "Project dir: $ROOT_DIR"
echo "Xray API:    $XRAY_API_SERVER (timeout=${XRAY_API_TIMEOUT_SECONDS}s)"
echo "Inbound tag: $XRAY_INBOUND_TAG"
echo "Config path: $XRAY_CONFIG_PATH"
echo "Max devices: $MAX_DEVICES"
[[ -n "$TARGET_TG" ]] && echo "Target TG:   $TARGET_TG"

print_section "Systemd Status"
run_checked "xray is-active" systemctl is-active xray || true
run_checked "tgvpn-bot is-active" systemctl is-active tgvpn-bot || true

print_section "Xray API Smoke Check"
run_checked "xray api inboundusercount" \
  xray api inboundusercount \
  --server="$XRAY_API_SERVER" \
  --timeout="$XRAY_API_TIMEOUT_SECONDS" \
  -tag="$XRAY_INBOUND_TAG" \
  --json || true

run_checked "xray api statsquery user>>>" \
  xray api statsquery \
  --server="$XRAY_API_SERVER" \
  --timeout="$XRAY_API_TIMEOUT_SECONDS" \
  --pattern="user>>>" \
  --json || true

print_section "Inbound Config Snapshot"
if [[ -f "$XRAY_CONFIG_PATH" ]]; then
  jq -r --arg TAG "$XRAY_INBOUND_TAG" '
    .inbounds[]? | select(.tag==$TAG) |
    {
      tag: .tag,
      protocol: .protocol,
      port: .port,
      network: .streamSettings.network,
      security: .streamSettings.security,
      flow: (.settings.clients[0].flow // "n/a")
    }' "$XRAY_CONFIG_PATH" 2>/dev/null || echo "Cannot parse inbound snapshot with jq"
else
  echo "Config file not found: $XRAY_CONFIG_PATH"
fi

print_section "Database Access"
COMPOSE_MODE=""
if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_MODE="docker-compose"
elif docker compose version >/dev/null 2>&1; then
  COMPOSE_MODE="docker compose"
fi

compose_psql() {
  local query="$1"
  if [[ "$COMPOSE_MODE" == "docker-compose" ]]; then
    docker-compose exec -T db psql -U vpn -d vpn_bot -At -F $'\t' -c "$query"
  else
    docker compose exec -T db psql -U vpn -d vpn_bot -At -F $'\t' -c "$query"
  fi
}

if [[ -z "$COMPOSE_MODE" ]]; then
  echo "docker-compose / docker compose not found. DB checks skipped."
  exit 0
fi
echo "Compose mode: $COMPOSE_MODE"

if [[ -n "$TARGET_TG" ]]; then
  USER_QUERY="
    SELECT telegram_id, 'user-' || telegram_id || '@vpn.local', uuid::text, status::text, vpn_enabled::text, device_limit_blocked::text, expiration_date::text
    FROM users
    WHERE telegram_id = ${TARGET_TG}
    ORDER BY telegram_id
  "
else
  USER_QUERY="
    SELECT telegram_id, 'user-' || telegram_id || '@vpn.local', uuid::text, status::text, vpn_enabled::text, device_limit_blocked::text, expiration_date::text
    FROM users
    WHERE status='active' AND device_limit_blocked=false AND expiration_date > now()
    ORDER BY telegram_id
  "
fi

USER_ROWS_FILE="$(mktemp)"
if ! compose_psql "$USER_QUERY" > "$USER_ROWS_FILE"; then
  echo "Failed to query users from DB" >&2
  rm -f "$USER_ROWS_FILE"
  exit 1
fi

TOTAL_USERS="$(wc -l < "$USER_ROWS_FILE" | tr -d ' ')"
echo "Users selected for diagnostics: $TOTAL_USERS"
if [[ "$TOTAL_USERS" -eq 0 ]]; then
  rm -f "$USER_ROWS_FILE"
  echo "No users matched query."
  exit 0
fi

if [[ "$MAX_USERS" -gt 0 ]]; then
  head -n "$MAX_USERS" "$USER_ROWS_FILE" > "${USER_ROWS_FILE}.limited"
  mv "${USER_ROWS_FILE}.limited" "$USER_ROWS_FILE"
  TOTAL_USERS="$(wc -l < "$USER_ROWS_FILE" | tr -d ' ')"
  echo "Limited to first $TOTAL_USERS users (--max-users)."
fi

print_section "Per-user Runtime Diagnostics"
printf "%-11s %-34s %-7s %-10s %-10s %-10s\n" "TG_ID" "EMAIL" "DB_VPN" "RUNTIME" "TRAFFIC" "ONLINE_IP"
printf "%-11s %-34s %-7s %-10s %-10s %-10s\n" "-----------" "----------------------------------" "-------" "----------" "----------" "----------"

runtime_missing=0
runtime_error=0
traffic_yes=0
online_over=0

while IFS=$'\t' read -r tg email uuid status db_vpn blocked expiration; do
  inbound_out="$(xray api inbounduser --server="$XRAY_API_SERVER" --timeout="$XRAY_API_TIMEOUT_SECONDS" -tag="$XRAY_INBOUND_TAG" -email="$email" --json 2>&1)"
  inbound_code=$?
  if [[ "$inbound_code" -ne 0 ]]; then
    runtime_state="api_error"
    runtime_error=$((runtime_error + 1))
  elif printf "%s" "$inbound_out" | rg -q "$email"; then
    runtime_state="present"
  else
    runtime_state="missing"
    runtime_missing=$((runtime_missing + 1))
  fi

  stats_out="$(xray api statsquery --server="$XRAY_API_SERVER" --timeout="$XRAY_API_TIMEOUT_SECONDS" --pattern="user>>>$email>>>traffic>>>" --json 2>/dev/null || true)"
  stat_len="$(printf "%s" "$stats_out" | jq -r '.stat | length // 0' 2>/dev/null || echo 0)"
  if [[ "$stat_len" -gt 0 ]]; then
    traffic_state="yes"
    traffic_yes=$((traffic_yes + 1))
  else
    traffic_state="no"
  fi

  online_out="$(xray api statsonlineiplist --server="$XRAY_API_SERVER" --timeout="$XRAY_API_TIMEOUT_SECONDS" -email="$email" --json 2>&1 || true)"
  if printf "%s" "$online_out" | rg -qi "not found"; then
    online_count=0
  else
    online_count="$(printf "%s" "$online_out" | jq -r '
      if .ips == null then 0
      elif (.ips|type) == "object" then (.ips|length)
      elif (.ips|type) == "array" then (.ips|length)
      else 0
      end
    ' 2>/dev/null || echo -1)"
  fi
  if [[ "$online_count" -gt "$MAX_DEVICES" ]]; then
    online_over=$((online_over + 1))
  fi

  printf "%-11s %-34s %-7s %-10s %-10s %-10s\n" "$tg" "$email" "$db_vpn" "$runtime_state" "$traffic_state" "$online_count"
done < "$USER_ROWS_FILE"

print_section "Summary"
echo "Users checked:                 $TOTAL_USERS"
echo "Runtime missing users:         $runtime_missing"
echo "Runtime API errors:            $runtime_error"
echo "Users with non-empty traffic:  $traffic_yes"
echo "Users over device limit:       $online_over"

print_section "Recent Logs (xray)"
journalctl -u xray -n 120 --no-pager | rg -i "error|failed|rejected|invalid|not found|warning|started|stopped" || true

print_section "Recent Logs (tgvpn-bot)"
journalctl -u tgvpn-bot -n 200 --no-pager | rg -i "xray|sync|failed|error|exception|runtime" || true

rm -f "$USER_ROWS_FILE"

echo
echo "Done."
