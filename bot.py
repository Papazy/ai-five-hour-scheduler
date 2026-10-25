#!/usr/bin/env python3
"""Telegram command listener for the pinger.

Long-polls getUpdates (outbound HTTPS only, no open port) and answers
commands from TG_CHAT_ID. Messages from any other chat are ignored.
"""
import html
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ["TG_BOT_TOKEN"]
CHAT_ID = int(os.environ["TG_CHAT_ID"])
ACCOUNTS = os.environ.get("CODEX_ACCOUNTS", "codex1 codex2 codex3").split()
API = f"https://api.telegram.org/bot{TOKEN}"
USAGE = os.path.expanduser("~/pinger/usage.py")
HELP = "Commands:\n/usage - current quota per account"


def call(method, http_timeout=15, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"{API}/{method}", data=data, timeout=http_timeout) as resp:
        return json.load(resp)


def send(text, as_html=False):
    try:
        if as_html:
            return call("sendMessage", chat_id=CHAT_ID, text=text, parse_mode="HTML")
        return call("sendMessage", chat_id=CHAT_ID, text=text)
    except urllib.error.HTTPError:
        if as_html:  # Telegram rejected the HTML, send it as plain text
            return send(re.sub(r"<[^>]+>", "", text))
        raise


def usage_report():
    try:
        out = subprocess.run(["python3", USAGE, "--html", *ACCOUNTS],
                             capture_output=True, text=True, timeout=120)
        body = out.stdout.strip() or "▲ <b>usage.py returned nothing:</b> " + html.escape(out.stderr[-300:])
    except Exception as exc:
        body = "▲ <b>usage.py failed:</b> " + html.escape(str(exc))
    return f"▤ <b>Usage · {time.strftime('%H:%M %Z')}</b>\n{body}"


def handle(message):
    if message.get("chat", {}).get("id") != CHAT_ID:
        return
    words = (message.get("text") or "").split()
    command = words[0].split("@")[0].lower() if words else ""
    if command == "/usage":
        call("sendChatAction", chat_id=CHAT_ID, action="typing")
        send(usage_report(), as_html=True)
    elif command in ("/start", "/help"):
        send(HELP)


def main():
    call("setMyCommands", commands=json.dumps([{"command": "usage", "description": "Current quota per account"}]))
    # drop commands that arrived while the bot was down
    pending = call("getUpdates", offset=-1).get("result", [])
    offset = pending[-1]["update_id"] + 1 if pending else None
    while True:
        params = {"timeout": 50, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            params["offset"] = offset
        try:
            updates = call("getUpdates", http_timeout=70, **params).get("result", [])
        except Exception as exc:
            print(f"getUpdates failed: {exc}", flush=True)
            time.sleep(10)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            try:
                handle(update.get("message") or {})
            except Exception as exc:
                print(f"handling update {update['update_id']} failed: {exc}", flush=True)


if __name__ == "__main__":
    main()
