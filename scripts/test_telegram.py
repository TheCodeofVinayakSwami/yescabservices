#!/usr/bin/env python3
"""Small script to send a test Telegram message using .env values."""
from dotenv import load_dotenv
import os
import requests
import sys


def main():
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not token or not chat:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_ADMIN_CHAT_ID in .env")
        sys.exit(1)

    text = "YesCab: test admin notification (ignore)"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        print(resp.status_code)
        print(resp.text)
    except Exception as e:
        print("Exception sending Telegram message:", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
