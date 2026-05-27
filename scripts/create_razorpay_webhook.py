#!/usr/bin/env python3
"""Create a Razorpay webhook pointing to the app's webhook endpoint.

This script reads `RZP_KEY_ID`, `RZP_KEY_SECRET`, `APP_BASE_URL`, and
`RZP_WEBHOOK_SECRET` from `.env` and calls Razorpay API to register a webhook
for `payment.captured` events.
"""
from dotenv import load_dotenv
import os
import sys
import requests
import json


def main():
    load_dotenv()
    key_id = os.getenv("RZP_KEY_ID")
    key_secret = os.getenv("RZP_KEY_SECRET")
    app_base = os.getenv("APP_BASE_URL")
    webhook_secret = os.getenv("RZP_WEBHOOK_SECRET")

    if not key_id or not key_secret:
        print("Missing RZP_KEY_ID or RZP_KEY_SECRET in .env")
        sys.exit(1)
    if not app_base:
        print("Missing APP_BASE_URL in .env")
        sys.exit(1)

    webhook_url = app_base.rstrip("/") + "/api/webhook/razorpay"

    if webhook_secret and webhook_secret.startswith("http"):
        print("Warning: RZP_WEBHOOK_SECRET looks like a URL. Consider using a random secret string.")

    payload = {
        "url": webhook_url,
        "secret": webhook_secret or "",
        "active": True,
        "events": ["payment.captured"]
    }

    api_url = "https://api.razorpay.com/v1/webhooks"
    headers = {"Content-Type": "application/json"}
    try:
        print("Creating webhook (JSON)...")
        resp = requests.post(api_url, auth=(key_id, key_secret), data=json.dumps(payload), headers=headers, timeout=15)
    except Exception as e:
        print("Request failed:", e)
        sys.exit(2)

    # If JSON attempt failed, try a form-encoded fallback
    if not resp.ok:
        print("JSON create failed, status:", resp.status_code)
        try:
            print("Trying form-encoded fallback...")
            # Razorpay expects events[] style for form posts
            form = {
                "url": webhook_url,
                "secret": webhook_secret or "",
                "active": "true",
            }
            # Add events[] entries
            for i, ev in enumerate(["payment.captured"]):
                form[f"events[{i}]"] = ev
            resp2 = requests.post(api_url, auth=(key_id, key_secret), data=form, timeout=15)
            print(resp2.status_code)
            try:
                print(json.dumps(resp2.json(), indent=2))
            except Exception:
                print(resp2.text)
            if resp2.ok:
                print("Webhook created successfully (form-encoded).")
                return
            else:
                print("Form-encoded attempt failed.")
        except Exception as e:
            print("Fallback failed:", e)
        print("Failed to create webhook. See response above.")
        return

    print(resp.status_code)
    try:
        print(json.dumps(resp.json(), indent=2))
    except Exception:
        print(resp.text)

    if resp.ok:
        print("Webhook created successfully.")
    else:
        print("Failed to create webhook. See response above.")


if __name__ == "__main__":
    main()
