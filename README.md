# ai-five-hour-scheduler

**Make your AI coding limits reset when you want them to, not when you happen to start typing.**

Claude Code, Codex and Antigravity meter usage in 5-hour windows, and a window only starts with your first message. Start work at 09:00, burn through the quota by 10:30, and you are locked out until 14:00. This tool starts the windows for you at fixed times, so a fresh quota is never far away.

## The problem

```
Without it: your first message at 09:00 starts the window
  09:00 ███████████ 10:30 ·········· locked out ·········· 14:00 ██████ 17:00
        quota used up                                      next window

With it: a ping starts the windows at 06, 11, 16 and 21
  06:00 ░░░░░░ 09:00 █████ 10:30 ··· 11:00 ██████████ 16:00 ██████ 17:00
        already running   short wait   fresh window      fresh window
```

Over a 09:00 to 17:00 workday you touch three windows instead of two, and the longest you ever wait is until the next scheduled time.

## What you get

- **Every account on schedule.** Claude Code, any number of Codex accounts, and both Antigravity quota groups are pinged with one tiny prompt at the times you choose.
- **A usage report on Telegram** after every ping: 5-hour and weekly quota per account, with reset times.
- **`/usage` on demand.** Ask the bot at any time. It reads quota without sending a prompt, so checking never starts a window.
- **Alerts when something breaks.** Failed pings are retried, flagged in the report, and reported to Healthchecks.io as a dead man's switch.
- **One script to set up or move hosts.** `install.sh` is idempotent: run it on a fresh server, run it again after every update.

<img src="docs/telegram-usage.png" alt="Telegram reply to /usage: per-account 5-hour and weekly quota with bars and reset times" width="520">

## Quick start

You need an always-on Ubuntu 24.04 machine with about 1 GB RAM (a small cloud VM is plenty) and a user with sudo.

1. **Install**

   ```bash
   git clone https://github.com/Papazy/ai-five-hour-scheduler.git ~/pinger
   cd ~/pinger && ./install.sh
   ```

2. **Configure.** Put your Telegram bot token and chat id (and optionally a Healthchecks URL) in `~/.config/pinger/env`, then log in once to each CLI. The installer prints the exact commands. Re-run `./install.sh` afterwards.

3. **Test**

   ```bash
   sudo systemctl start pinger.service
   ```

   A report should arrive on Telegram. Send `/usage` to the bot to check it answers.

## Pick your own schedule

Pings default to 06:00, 11:00, 16:00 and 21:00 in `Asia/Jakarta`. Set your own in `~/.config/pinger/env` and re-run `./install.sh`:

```bash
PINGER_TZ="Europe/Berlin"
PINGER_SCHEDULE="07,12,17,22:00:00"
```

Keep the pings 5 hours apart so each one starts a new window as the last one ends.

## More

[docs/operations.md](docs/operations.md) covers how it works, every setting, moving to a new host, updating, day-to-day commands, and known limits.

## License

[MIT](LICENSE). Not affiliated with Anthropic, OpenAI or Google.
