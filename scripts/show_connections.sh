bash <<'EOF'
cd /home/tgvpn || exit 1

SRV=$(awk -F= '/^XRAY_API_SERVER=/{print $2}' .env)
TO=$(awk -F= '/^XRAY_API_TIMEOUT_SECONDS=/{print $2}' .env)
LIMIT=$(awk -F= '/^MAX_DEVICES=/{print $2}' .env)
[ -z "$LIMIT" ] && LIMIT=4

while true; do
  clear
  date
  echo "limit = $LIMIT"
  printf "%-11s %-34s %-10s %s\n" "TG_ID" "EMAIL" "ONLINE_IPS" "STATE"
  printf "%-11s %-34s %-10s %s\n" "-----------" "----------------------------------" "----------" "------"

  docker-compose exec -T db psql -U vpn -d vpn_bot -At -F $'\t' -c "
    SELECT telegram_id, 'user-' || telegram_id || '@vpn.local'
    FROM users
    WHERE status='active'
      AND device_limit_blocked=false
      AND expiration_date > now()
    ORDER BY telegram_id;
  " | while IFS=$'\t' read -r tg email; do
    out=$(xray api statsonlineiplist --server="$SRV" --timeout="$TO" -email="$email" --json 2>/dev/null || true)
    cnt=$(printf "%s" "$out" | jq -r '
      if .ips == null then 0
      elif (.ips|type) == "object" then (.ips|length)
      elif (.ips|type) == "array" then (.ips|length)
      else 0 end
    ' 2>/dev/null)
    [ -z "$cnt" ] && cnt=0

    state="OK"
    if [ "$cnt" -gt "$LIMIT" ]; then
      state="OVER_LIMIT"
    fi

    printf "%-11s %-34s %-10s %s\n" "$tg" "$email" "$cnt" "$state"
  done | sort -k3,3nr

  sleep 2
done
EOF