#!/usr/bin/env python3
"""Collect 5-hour and weekly usage per account and print it for the report.

Usage: usage.py [--html] CODEX_ACCOUNT...
Plain mode prints one line per account; --html prints Telegram HTML tables.
Never fails: anything unreadable becomes n/a.
"""
import glob
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
HOME = os.path.expanduser("~")
AGY = f"{HOME}/.local/bin/agy"
AGY_GROUPS = {"Gemini Models": "agy-gemini", "Claude and GPT models": "agy-claude"}
AGY_ROW = re.compile(r"^(.+?)\s+(Weekly|Five Hour) Limit Remaining\s+(\d+(?:\.\d+)?)%\s+(\S+)\s*$")
WARN_PCT = 80
BAR_WIDTH = 10


def to_wib(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, WIB)
    return datetime.fromisoformat(value).astimezone(WIB)


def fmt(value, pattern):
    dt = to_wib(value)
    return dt.strftime(pattern) if dt else "?"


def usage(five_pct, five_reset, week_pct, week_reset):
    return {"five": (float(five_pct or 0), five_reset), "week": (float(week_pct or 0), week_reset)}


def codex(acc):
    files = sorted(glob.glob(f"{HOME}/.{acc}/sessions/**/*.jsonl", recursive=True), key=os.path.getmtime)
    limits = None
    if files:
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
    return usage(p.get("used_percent"), p.get("resets_at"), s.get("used_percent"), s.get("resets_at"))


def claude():
    try:
        with open(f"{HOME}/.claude/.credentials.json") as f:
            oauth = json.load(f)["claudeAiOauth"]
        token = oauth["accessToken"]
        # the access token is only refreshed when claude runs (each ping);
        # calling a CLI here would start a 5-hour window at the wrong time
        if oauth.get("expiresAt") and oauth["expiresAt"] / 1000 < time.time():
            return "stale"
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        five, week = data.get("five_hour") or {}, data.get("seven_day") or {}
        return usage(five.get("utilization"), five.get("resets_at"), week.get("utilization"), week.get("resets_at"))
    except Exception:
        return None


def antigravity():
    try:
        out = subprocess.run([AGY, "-p", "/usage"], capture_output=True, text=True,
                             timeout=60, cwd=f"{HOME}/pinger/work").stdout
    except Exception:
        out = ""
    groups = {}
    for raw in out.splitlines():
        m = AGY_ROW.match(raw.strip())
        if m and m.group(1) in AGY_GROUPS:
            window = "five" if m.group(2) == "Five Hour" else "week"
            # agy reports remaining %, convert to used %
            groups.setdefault(m.group(1), {})[window] = (100 - float(m.group(3)), m.group(4))
    rows = []
    for label, name in AGY_GROUPS.items():
        g = groups.get(label, {})
        rows.append((name, g if "five" in g and "week" in g else None))
    return rows


def collect(accounts):
    return [("claude", claude())] + [(acc, codex(acc)) for acc in accounts] + antigravity()


def plain(rows):
    out = []
    for name, u in rows:
        if not isinstance(u, dict):
            out.append(f"{name}: {u or 'n/a'}")
            continue
        (fp, fr), (wp, wr) = u["five"], u["week"]
        out.append(f"{name}: 5h {fp:.0f}% (resets {fmt(fr, '%H:%M')})"
                   f" · weekly {wp:.0f}% (resets {fmt(wr, '%d/%m %H:%M')})")
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
            lines.append(f"{u or 'n/a':<{BAR_WIDTH + 5}}  {'-':<5}  {name}")
            continue
        pct, reset = u[window]
        flag = " !" if pct >= WARN_PCT else ""
        lines.append(f"{bar(pct)} {pct:>3.0f}%  {fmt(reset, reset_pattern)}  {name}{flag}")
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


def telegram(rows):
    legend = " <i>· used · resets · account</i>"
    parts = ["<b>5-hour window</b>" + legend, table(rows, "five", "%H:%M"),
             "<b>Weekly</b>" + legend, table(rows, "week", "%d/%m")]
    hot = [f"{name} {label} {u[w][0]:.0f}%" for name, u in rows if isinstance(u, dict)
           for w, label in (("five", "5h"), ("week", "weekly")) if u[w][0] >= WARN_PCT]
    if hot:
        parts.append("⚠️ Running low: " + html.escape(", ".join(hot)))
    return "\n".join(parts)


if __name__ == "__main__":
    args = sys.argv[1:]
    as_html = "--html" in args
    rows = collect([a for a in args if a != "--html"])
    print(telegram(rows) if as_html else plain(rows))
