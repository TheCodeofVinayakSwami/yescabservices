#!/usr/bin/env python3
"""Rotate the Razorpay webhook secret and update .env.

This script will:
 - list webhooks via Razorpay API
 - find the webhook matching APP_BASE_URL + /api/webhook/razorpay (or the first webhook)
 - generate a new secret
 - update the webhook's secret via Razorpay API
 - write the new secret into the project's `.env` file (backup created)

The new secret will NOT be printed to stdout.
"""
from dotenv import load_dotenv
import os
import sys
import requests
import json
import secrets


def fail(msg, code=1):
    print(msg)
    sys.exit(code)


def main():
    load_dotenv()
    key = os.getenv("RZP_KEY_ID")
    secret = os.getenv("RZP_KEY_SECRET")
    app_base = os.getenv("APP_BASE_URL")

    if not key or not secret:
        fail("Missing RZP_KEY_ID or RZP_KEY_SECRET in environment/.env")

    webhook_url = None
    if app_base:
        webhook_url = app_base.rstrip("/") + "/api/webhook/razorpay"

    # List webhooks
    r = requests.get("https://api.razorpay.com/v1/webhooks", auth=(key, secret), timeout=15)
    if r.status_code != 200:
        fail(f"Failed to list webhooks: {r.status_code} {r.text[:300]}")

    data = r.json()
    items = data.get("items") or data.get("items", [])
    if not items:
        fail("No webhooks found in Razorpay account")

    # Find matching webhook by URL, otherwise pick first
    target = None
    if webhook_url:
        for it in items:
            if it.get("url") == webhook_url:
                target = it
                break
    if not target:
        target = items[0]

    webhook_id = target.get("id")
    if not webhook_id:
        fail("Could not determine webhook id to update")

    # Generate a new random secret
    new_secret = secrets.token_urlsafe(32)

    # Update webhook - Razorpay expects url, events etc when updating via PUT
    update_url = f"https://api.razorpay.com/v1/webhooks/{webhook_id}"
    # Build events list from the target item's events mapping
    events_map = target.get("events") or {}
    events_list = [k for k, v in events_map.items() if v]
    if not events_list:
        # fallback to at least payment.captured
        events_list = ["payment.captured"]

    payload = {
        "url": target.get("url"),
        "secret": new_secret,
        "active": bool(target.get("active", True)),
        "events": events_list,
    }
    headers = {"Content-Type": "application/json"}
    resp = requests.put(update_url, auth=(key, secret), data=json.dumps(payload), headers=headers, timeout=15)

    # If PUT not allowed or failed, try PATCH with same payload
    if resp.status_code >= 400:
        # Try PATCH with JSON
        resp2 = requests.patch(update_url, auth=(key, secret), data=json.dumps(payload), headers=headers, timeout=15)
        if resp2.status_code < 400:
            ok_resp = resp2
        else:
            # Try form-encoded PUT (Razorpay may expect form fields or events[] array)
            form_tuples = [
                ("url", target.get("url")),
                ("secret", new_secret),
                ("active", str(bool(target.get("active", True))).lower()),
            ]
            for ev in events_list:
                form_tuples.append(("events[]", ev))
            try:
                resp3 = requests.put(update_url, auth=(key, secret), data=form_tuples, timeout=15)
            except Exception as e:
                print("Form-encoded PUT failed:", e)
                print("Previous responses:")
                print(resp.status_code, resp.text)
                print(resp2.status_code, resp2.text)
                sys.exit(2)

            if resp3.status_code >= 400:
                # Could not update
                print("Failed to update webhook secret via API. Responses:")
                print(resp.status_code, resp.text)
                print(resp2.status_code, resp2.text)
                print(resp3.status_code, resp3.text)
                sys.exit(2)
            ok_resp = resp3
    else:
        ok_resp = resp

    # Write new secret into .env (backup first)
    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        # create .env
        open(env_path, "w").close()

    # Backup
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        lines = []

    # Create backup file
    try:
        with open(env_path + ".backup", "w", encoding="utf-8") as bf:
            bf.writelines(lines)
    except Exception:
        # non-fatal
        pass

    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith("RZP_WEBHOOK_SECRET="):
            lines[i] = f"RZP_WEBHOOK_SECRET={new_secret}\n"
            found = True
            break
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        lines.append(f"RZP_WEBHOOK_SECRET={new_secret}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print("Webhook secret rotated and .env updated (secret not shown). Webhook id:", webhook_id)


if __name__ == "__main__":
    main()
