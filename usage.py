#!/usr/bin/env python3
"""Collect 5-hour and weekly usage per account and print it for the report.

Usage: usage.py [--html] CODEX_ACCOUNT...
Plain mode prints one line per account; --html prints Telegram HTML tables.

Every source is read live without sending a prompt, so nothing here can start
a 5-hour window. When a live read fails, the last known value is used instead
and marked with "*". Never fails: anything unreadable becomes n/a.
"""
import glob
import html
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

HOME = os.path.expanduser("~")
WORK = f"{HOME}/pinger/work"
AGY = f"{HOME}/.local/bin/agy"
AGY_GROUPS = {"Gemini Models": "agy-gemini", "Claude and GPT models": "agy-claude"}
CACHE = f"{HOME}/.cache/pinger/usage.json"
WARN_PCT = 80
BAR_WIDTH = 10
CODEX_HANDSHAKE = [
    {"id": 1, "method": "initialize",
     "params": {"clientInfo": {"name": "pinger", "title": "pinger", "version": "1.0"}}},
    {"method": "initialized"},
    {"id": 2, "method": "account/rateLimits/read"},
]


def to_local(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value).astimezone()
    # "Z" suffix: fromisoformat accepts it only from Python 3.11
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()   # host timezone


def fmt(value, pattern):
    try:
        dt = to_local(value)
    except (TypeError, ValueError, OSError):
        return "?"
    return dt.strftime(pattern) if dt else "?"


def usage(five_pct, five_reset, week_pct, week_reset):
    return {"five": [float(five_pct or 0), five_reset], "week": [float(week_pct or 0), week_reset]}


# --- last known values -------------------------------------------------------

def load_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(rows):
    cache = load_cache()
    now = time.time()
    for name, u in rows:
        if isinstance(u, dict) and "asof" not in u:
            cache[name] = dict(u, asof=now)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = f"{CACHE}.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, CACHE)   # atomic: the bot and ping.sh may write at the same time


def newest(*candidates):
    found = [c for c in candidates if isinstance(c, dict)]
    return max(found, key=lambda c: c.get("asof", 0)) if found else None


# --- sources -------------------------------------------------------------------

def codex_live(acc, timeout=25):
    """Ask `codex app-server` for the account's rate limits (no prompt is sent)."""
    env = dict(os.environ, CODEX_HOME=f"{HOME}/.{acc}")
    try:
        proc = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, env=env, cwd=WORK)
    except OSError:
        return None
    killer = threading.Timer(timeout, proc.kill)   # unblocks the read loop on a hang
    killer.start()
    try:
        for msg in CODEX_HANDSHAKE:
            proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("id") != 2:
                continue
            limits = (msg.get("result") or {}).get("rateLimits") or {}
            p, s = limits.get("primary"), limits.get("secondary") or {}
            if not p:
                return None
            return usage(p.get("usedPercent"), p.get("resetsAt"), s.get("usedPercent"), s.get("resetsAt"))
        return None
    except (OSError, ValueError):
        return None
    finally:
        killer.cancel()
        proc.kill()
        proc.wait()


def codex_last_ping(acc):
    """Rate limits recorded in the newest session file, i.e. at the last ping."""
    files = sorted(glob.glob(f"{HOME}/.{acc}/sessions/**/*.jsonl", recursive=True), key=os.path.getmtime)
    if not files:
        return None
    limits = None
    with open(files[-1]) as f:
        for raw in f:
            if '"rate_limits"' in raw:
                try:
                    limits = json.loads(raw)["payload"]["rate_limits"]
                except (KeyError, ValueError):
                    pass
    if not limits:
        return None
    p, s = limits.get("primary") or {}, limits.get("secondary") or {}
    return dict(usage(p.get("used_percent"), p.get("resets_at"), s.get("used_percent"), s.get("resets_at")),
                asof=os.path.getmtime(files[-1]))


def codex(acc, cache):
    return codex_live(acc) or newest(cache.get(acc), codex_last_ping(acc))


def claude(cache):
    try:
        with open(f"{HOME}/.claude/.credentials.json") as f:
            oauth = json.load(f)["claudeAiOauth"]
        # the access token is refreshed only when claude runs (each ping). Running a
        # CLI here would start a 5-hour window, so an expired token means cached data.
        if oauth.get("expiresAt") and oauth["expiresAt"] / 1000 < time.time():
            return cache.get("claude")
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={"Authorization": f"Bearer {oauth['accessToken']}", "anthropic-beta": "oauth-2025-04-20"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        five, week = data.get("five_hour") or {}, data.get("seven_day") or {}
        return usage(five.get("utilization"), five.get("resets_at"), week.get("utilization"), week.get("resets_at"))
    except Exception:
        return cache.get("claude")


def antigravity(cache):
    """Parse `agy -p /usage --output-format json` (a local command, num_turns is 0)."""
    groups = {}
    try:
        out = subprocess.run([AGY, "-p", "/usage", "--output-format", "json"], capture_output=True,
                             text=True, timeout=60, cwd=WORK).stdout
        for group in json.loads(out)["command"]["data"]["groups"]:
            windows = {}
            for bucket in group.get("buckets", []):
                key = {"5h": "five", "weekly": "week"}.get(bucket.get("window"))
                if key:
                    # agy reports the remaining fraction, convert to used %
                    windows[key] = [(1 - float(bucket["remaining_fraction"])) * 100, bucket.get("reset_time")]
            groups[group.get("name")] = windows
    except Exception:
        pass
    rows = []
    for label, name in AGY_GROUPS.items():
        g = groups.get(label, {})
        rows.append((name, g if "five" in g and "week" in g else cache.get(name)))
    return rows


def collect(accounts):
    cache = load_cache()
    with ThreadPoolExecutor(max_workers=len(accounts) + 2) as pool:
        claude_f = pool.submit(claude, cache)
        codex_f = [pool.submit(codex, acc, cache) for acc in accounts]
        agy_f = pool.submit(antigravity, cache)
        rows = [("claude", claude_f.result())]
        rows += [(acc, f.result()) for acc, f in zip(accounts, codex_f)]
        rows += agy_f.result()
    try:
        save_cache(rows)
    except OSError:
        pass
    return rows


# --- rendering -----------------------------------------------------------------

def plain(rows):
    out = []
    for name, u in rows:
        if not isinstance(u, dict):
            out.append(f"{name}: n/a")
            continue
        (fp, fr), (wp, wr) = u["five"], u["week"]
        asof = f" [as of {fmt(u['asof'], '%H:%M')}]" if "asof" in u else ""
        out.append(f"{name}: 5h {fp:.0f}% (resets {fmt(fr, '%H:%M')})"
                   f" · weekly {wp:.0f}% (resets {fmt(wr, '%d/%m %H:%M')}){asof}")
    return "\n".join(out)


def bar(pct):
    filled = min(BAR_WIDTH, max(0, round(pct / 100 * BAR_WIDTH)))
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def table(rows, window, reset_pattern):
    # bar first: the column that must line up starts at the left edge, so it
    # stays aligned even when the client font is not monospace. No header row:
    # space-padded labels drift in proportional fonts, the legend is in the title.
    lines = []
    for name, u in rows:
        if not isinstance(u, dict):
            lines.append(f"{'n/a':<{BAR_WIDTH + 5}}  {'-':<5}  {name}")
            continue
        pct, reset = u[window]
        mark = "*" if "asof" in u else ""
        flag = " !" if pct >= WARN_PCT else ""
        lines.append(f"{bar(pct)} {pct:>3.0f}%  {fmt(reset, reset_pattern)}  {name}{mark}{flag}")
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


def telegram(rows):
    legend = " <i>· used · resets · account</i>"
    parts = ["<b>5-hour window</b>" + legend, table(rows, "five", "%H:%M"),
             "<b>Weekly</b>" + legend, table(rows, "week", "%d/%m")]
    hot = [f"{name} {label} {u[w][0]:.0f}%" for name, u in rows if isinstance(u, dict)
           for w, label in (("five", "5h"), ("week", "weekly")) if u[w][0] >= WARN_PCT]
    if hot:
        parts.append("⚠️ Running low: " + html.escape(", ".join(hot)))
    old = [f"{name} {fmt(u['asof'], '%H:%M')}" for name, u in rows if isinstance(u, dict) and "asof" in u]
    if old:
        parts.append("<i>* not live, last known values as of: " + html.escape(", ".join(old)) + "</i>")
    return "\n".join(parts)


if __name__ == "__main__":
    args = sys.argv[1:]
    as_html = "--html" in args
    rows = collect([a for a in args if a != "--html"])
    print(telegram(rows) if as_html else plain(rows))
