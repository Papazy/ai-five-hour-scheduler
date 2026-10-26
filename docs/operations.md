# Operations

Everything you need once the pinger runs: what each file does, moving to a new host, updating, every setting, and the known limits. For the overview and the quick start, see the [README](../README.md).

```
pinger.timer ──> ping.sh ──> claude / codex (per CODEX_HOME) / agy (per model group)
                    ├──> Healthchecks.io   (dead man's switch)
                    └──> usage.py ──> Telegram report
pinger-bot.service ──> bot.py ──> /usage ──> usage.py ──> Telegram reply
```

All traffic is outbound HTTPS. SSH is reachable only through Tailscale; ufw denies every other inbound connection.

## Layout

| Path | Purpose |
| --- | --- |
| `ping.sh` | Pings every target with timeout and retry, reports to Healthchecks and Telegram, exits 1 if anything failed |
| `usage.py` | Reads quota live without sending a prompt: `codex app-server` (`account/rateLimits/read`), `agy -p /usage --output-format json`, Claude OAuth usage endpoint. Falls back to the last known value, marked `*`. `--html` renders the Telegram tables |
| `bot.py` | Long-polling Telegram listener for `/usage`. Only answers `TG_CHAT_ID` |
| `systemd/` | Unit templates. `install.sh` fills in user, home, schedule and timezone |
| `install.sh` | Idempotent bootstrap for a fresh Ubuntu 24.04 host |
| `env.example` | Variables expected in `~/.config/pinger/env` |

Secrets never live in the repo. They stay in `~/.config/pinger/env` (mode 600) and in each CLI's own credential store.

Clone the repo to `~/pinger` on the host, whatever the repo is called: the scripts, the units and `install.sh` use that path.

## Fresh host, in detail

Requirements: Ubuntu 24.04, about 1 GB RAM (install.sh adds 2 GB swap), a user with sudo.

`install.sh` installs Node.js 22, Claude Code, the Codex CLI, the Antigravity CLI and Tailscale, renders the systemd units and enables the timer. Then it prints the manual steps:

- `sudo tailscale up --ssh`, disable key expiry for the machine, then re-run `./install.sh` so ufw is enabled.
- Fill `~/.config/pinger/env` (or copy it from the old host, see below), set your timezone and schedule there if the defaults don't fit, then re-run `./install.sh` to start the bot.
- Log in once per CLI from `~/pinger/work`: `claude` → `/login`; `CODEX_HOME=~/.codexN codex login --device-auth` for every account (open each link in a private window); `~/.local/bin/agy`.

Test:

```bash
sudo systemctl start pinger.service
journalctl -u pinger.service -n 30 --no-pager   # every target "ok", report arrives on Telegram
```

Then send `/usage` to the bot.

## Moving to a new host

Do this while the old host is still running.

1. Prepare the new host with the steps above, up to and including the CLI logins.
2. Copy the env file through your Mac (both hosts on the tailnet):

   ```bash
   scp -3 pinger:.config/pinger/env NEWHOST:.config/pinger/env
   ssh NEWHOST 'chmod 600 ~/.config/pinger/env'
   ```

3. Cut over. A bot token allows only one `getUpdates` poller, so stop the old bot first:

   ```bash
   ssh pinger   'sudo systemctl disable --now pinger-bot pinger.timer'
   ssh NEWHOST  'cd ~/pinger && ./install.sh && sudo systemctl start pinger.service'
   ```

4. Check the Telegram report and `/usage`, then delete the old VM. The Healthchecks URL stays the same.

## Adopting this repo on an existing host

```bash
sudo systemctl stop pinger.timer pinger-bot
mv ~/pinger ~/pinger.old
git clone https://github.com/Papazy/ai-five-hour-scheduler.git ~/pinger
cd ~/pinger && ./install.sh
rm -rf ~/pinger.old    # once the test run is green
```

## Updating

```bash
cd ~/pinger && git pull && ./install.sh
```

`install.sh` re-renders the units, reloads systemd, and restarts the timer and the bot. The timer picks up the new `ping.sh` on its next run.

## Configuration

Everything is set in `~/.config/pinger/env`. Keep the quotes: the file is read by systemd and also sourced by bash for manual tests. Re-run `./install.sh` after changing the schedule or timezone.

| Variable | Default | Meaning |
| --- | --- | --- |
| `HC_URL` | empty | Healthchecks.io ping URL. Optional |
| `TG_BOT_TOKEN`, `TG_CHAT_ID` | empty | Telegram bot from @BotFather and your chat id. Optional, but needed for the report and `/usage` |
| `PINGER_TZ` | `Asia/Jakarta` | Host timezone; also used for the times in reports |
| `PINGER_SCHEDULE` | `06,11,16,21:00:00` | Time part of a systemd [`OnCalendar`](https://www.freedesktop.org/software/systemd/man/latest/systemd.time.html) expression |
| `CODEX_ACCOUNTS` | `codex1 codex2 codex3` | One `CODEX_HOME=~/.<name>` per account |
| `AGY_MODELS` | `agy-gemini:gemini-3.8-flash-low agy-claude:gpt-oss-120b-medium` | `label:model`, one light model per Antigravity quota group |

Spacing pings 5 hours apart means every window starts on schedule. Keep the Healthchecks cron schedule and timezone in sync with these values.

Adding a Codex account means logging in with `CODEX_HOME=~/.codexN` and adding `codexN` to `CODEX_ACCOUNTS`. Each extra target adds up to 630 s to the worst-case run time, so raise `TimeoutStartSec` in `systemd/pinger.service` if you add many.

## Commands

| Task | Command |
| --- | --- |
| Next run | `systemctl list-timers pinger.timer` |
| Last run | `journalctl -u pinger.service -n 50 --no-pager` |
| Bot log | `journalctl -u pinger-bot -f` |
| Run now | `sudo systemctl start pinger.service` (starts the 5-hour windows now) |
| Update CLIs | `sudo npm install -g @anthropic-ai/claude-code@latest @openai/codex@latest` and re-run the agy installer |
| Claude token expiry | `python3 -c 'import json,os,datetime;e=json.load(open(os.path.expanduser("~/.claude/.credentials.json")))["claudeAiOauth"]["expiresAt"];print(datetime.datetime.fromtimestamp(e/1000))'` |

## Known limits

- Claude usage comes from an undocumented endpoint and needs the full `/login` credentials, not a `setup-token`.
- Nothing in `usage.py` sends a prompt, so `/usage` never starts a 5-hour window. The cost: Claude's access token (about 8 hours) is refreshed only by the scheduled ping, so with the default schedule Claude shows its last known value, marked `*`, between 05:00 and 06:00.
- When a live read fails, the row shows the last known value from `~/.cache/pinger/usage.json` (or, for Codex, the newest session file), marked `*` with its time.
- When a provider resets everyone's limits mid-window (OpenAI does this for Codex now and then), the window scheduled by the last ping is gone. The next window starts with your next message, so the following ping may land inside it. The schedule realigns on its own once that window ends.
- Telegram on some Android clients does not render code blocks in a monospace font, so the report keeps the bar column first and has no header row.
