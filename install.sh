#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 24.04 host for the pinger. Safe to re-run.
# Run as the login user (not root) from the repo checkout at ~/pinger.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HOME/.config/pinger/env"
ME="$(id -un)"

log() { printf '\n==> %s\n' "$*"; }
# env_get KEY DEFAULT: value from the env file (quotes stripped), or DEFAULT
env_get() {
  local v
  v="$(grep -oP "^$1=\"?\K[^\"]*" "$ENV_FILE" 2>/dev/null | tail -n 1 || true)"
  printf '%s' "${v:-$2}"
}
warn() { printf 'WARN: %s\n' "$*" >&2; }

[ "$(id -u)" -ne 0 ] || { echo "Run as your normal user, not root." >&2; exit 1; }
[ "$REPO" = "$HOME/pinger" ] || { echo "Clone the repo to $HOME/pinger (found $REPO)." >&2; exit 1; }

log "Directories and env file"
mkdir -p "$REPO/work" "$(dirname "$ENV_FILE")"
chmod 700 "$(dirname "$ENV_FILE")"
if [ ! -f "$ENV_FILE" ]; then
  cp "$REPO/env.example" "$ENV_FILE"
  echo "created $ENV_FILE from env.example: fill it in"
fi
chmod 600 "$ENV_FILE"
chmod +x "$REPO/ping.sh" "$REPO/usage.py" "$REPO/bot.py"

for acc in $(env_get CODEX_ACCOUNTS "codex1 codex2 codex3"); do
  mkdir -p "$HOME/.$acc"
done

log "System packages"
sudo apt-get update -q
sudo apt-get install -yq curl git python3 unattended-upgrades bubblewrap ufw

log "Timezone and schedule"
TZ_NAME="$(env_get PINGER_TZ Asia/Jakarta)"
SCHEDULE="$(env_get PINGER_SCHEDULE 06,11,16,21:00:00)"
systemd-analyze calendar "*-*-* $SCHEDULE $TZ_NAME" >/dev/null \
  || { echo "Invalid PINGER_SCHEDULE/PINGER_TZ: '*-*-* $SCHEDULE $TZ_NAME'" >&2; exit 1; }
sudo timedatectl set-timezone "$TZ_NAME"

log "Swap"
if swapon --show --noheadings | grep -q .; then
  echo "swap already active"
else
  sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

log "Node.js 22"
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 22 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -yq nodejs
fi

log "Claude Code and Codex CLI"
sudo npm install -g @anthropic-ai/claude-code @openai/codex

log "Antigravity CLI"
if [ ! -x "$HOME/.local/bin/agy" ]; then
  curl -fsSL https://antigravity.google/cli/install.sh | bash
fi
grep -q '.local/bin' "$HOME/.bashrc" || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"

log "Tailscale"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh

log "systemd units"
for unit in pinger.service pinger.timer pinger-bot.service; do
  sed -e "s|@USER@|$ME|g" -e "s|@HOME@|$HOME|g" \
      -e "s|@SCHEDULE@|$SCHEDULE|g" -e "s|@TZ@|$TZ_NAME|g" "$REPO/systemd/$unit" \
    | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now pinger.timer
sudo systemctl restart pinger.timer   # pick up a changed schedule

if grep -q '^TG_BOT_TOKEN=.\+' "$ENV_FILE" && grep -q '^TG_CHAT_ID=.\+' "$ENV_FILE"; then
  sudo systemctl enable pinger-bot.service
  sudo systemctl restart pinger-bot.service
else
  warn "TG_BOT_TOKEN/TG_CHAT_ID empty: pinger-bot not started. Re-run install.sh after filling $ENV_FILE."
fi

log "Firewall"
if ip link show tailscale0 >/dev/null 2>&1; then
  sudo ufw default deny incoming
  sudo ufw default allow outgoing
  sudo ufw allow in on tailscale0
  sudo ufw --force enable
else
  warn "tailscale0 is not up: ufw skipped so SSH over the public IP keeps working. Re-run after 'sudo tailscale up --ssh'."
fi

cat <<EOF

Done. Remaining manual steps:
  1. sudo tailscale up --ssh     then Disable key expiry in the admin console and re-run ./install.sh
  2. Fill $ENV_FILE (or copy it from the old host)
  3. Log in once per CLI, from $REPO/work:
       claude                                            /login, then /exit
       CODEX_HOME=~/.codex1 codex login --device-auth    repeat for each account
       ~/.local/bin/agy                                  log in, then exit
  4. Test: sudo systemctl start pinger.service && journalctl -u pinger.service -n 30 --no-pager
EOF
