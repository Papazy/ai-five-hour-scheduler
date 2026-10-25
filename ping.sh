#!/usr/bin/env bash
# Ping Claude Code, every Codex account and every Antigravity model group
# so their 5-hour usage windows start on schedule, then report the result.
set -uo pipefail
cd "$HOME/pinger/work"

PROMPT="Reply with OK only."
# overridable from ~/.config/pinger/env
CODEX_ACCOUNTS="${CODEX_ACCOUNTS:-codex1 codex2 codex3}"   # one CODEX_HOME per account: ~/.codexN
AGY="$HOME/.local/bin/agy"
# one light model per Antigravity quota group, as name:model
AGY_MODELS="${AGY_MODELS:-agy-gemini:gemini-3.8-flash-low agy-claude:gpt-oss-120b-medium}"

ping_one() {
  local name="$1"; shift
  local out
  for attempt in 1 2 3; do
    if out=$(timeout 180 "$@" 2>&1); then
      echo "$name ok (attempt $attempt)"
      return 0
    fi
    echo "$name failed (attempt $attempt):"
    echo "$out" | tail -n 5
    sleep 30
  done
  return 1
}

failed=""
ping_one claude claude -p "$PROMPT" --model haiku || failed="$failed claude"

for acc in $CODEX_ACCOUNTS; do
  ping_one "$acc" env CODEX_HOME="$HOME/.$acc" codex exec --skip-git-repo-check "$PROMPT" \
    || failed="$failed $acc"
done

for entry in $AGY_MODELS; do
  name="${entry%%:*}"
  model="${entry#*:}"
  ping_one "$name" "$AGY" -p "$PROMPT" --model "$model" || failed="$failed $name"
done

if [ -n "${HC_URL:-}" ]; then
  if [ -z "$failed" ]; then
    curl -fsS -m 10 --retry 3 "$HC_URL" >/dev/null
  else
    curl -fsS -m 10 --retry 3 --data-raw "failed:$failed" "$HC_URL/fail" >/dev/null
  fi
fi

if [ -n "${TG_BOT_TOKEN:-}" ] && [ -n "${TG_CHAT_ID:-}" ]; then
  now=$(date '+%H:%M %Z')
  if [ -z "$failed" ]; then
    head="<b>Ping · $now</b> · all OK"
  else
    head="<b>Ping · $now</b> · <b>FAILED:</b>$failed"
  fi
  if ! usage=$(PYTHONIOENCODING=utf-8 python3 "$HOME/pinger/usage.py" --html $CODEX_ACCOUNTS 2>&1); then
    echo "usage.py failed:"
    echo "$usage" | tail -n 5
    usage="<b>usage.py failed</b>, see journal"
  fi
  msg="$head

$usage"
  tg() {
    curl -fsS -m 10 --retry 3 "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT_ID}" "$@" >/dev/null
  }
  # fall back to plain text if Telegram rejects the HTML
  tg --data-urlencode "parse_mode=HTML" --data-urlencode "text=${msg}" \
    || tg --data-urlencode "text=$(printf '%s' "$msg" | sed 's/<[^>]*>//g')"
fi

[ -z "$failed" ] || exit 1