#!/usr/bin/env python3
"""Asciilands market price watcher.

Fetches /v1/public/market for each realm, finds the cheapest offer for every
item in config.json, and sends an alert (Discord and/or ntfy) when it is at or
below your threshold. Remembers what it already alerted so you aren't spammed.

Usage:
    python watch.py                       # normal run
    python watch.py --file sample.json    # test against a saved response
    python watch.py --dry-run             # print alerts instead of sending
"""
import argparse
import fnmatch
import json
import os
import sys
import urllib.request

API = {
    "eu": "https://api.asciilands.io/v1/public/market",
    "na": "https://api-na.asciilands.io/v1/public/market",
}
STATE_FILE = os.environ.get("STATE_FILE", "state.json")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "asciilands-price-watch/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def cheapest(data, pattern, min_qty):
    """Cheapest matching offer across all regions, or None.

    pattern supports * wildcards, e.g. "tool1_*_steel_sword_*".
    """
    best = None
    for region in data.get("regions", []):
        for o in region.get("offers", []):
            if not fnmatch.fnmatch(o["item_id"], pattern):
                continue
            if o["quantity"] < min_qty:
                continue
            if best is None or o["price_each"] < best["price"]:
                best = {
                    "price": o["price_each"],
                    "quantity": o["quantity"],
                    "region": region["zone_name"],
                    "stall": o["stall"],
                }
    return best


def send_discord(url, text):
    body = json.dumps({"content": text}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", "User-Agent": "asciilands-price-watch/1.0"}
    )
    urllib.request.urlopen(req, timeout=30).read()


def send_ntfy(topic, text):
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=text.encode(),
        headers={"Title": "Asciilands price alert", "Tags": "moneybag"},
    )
    urllib.request.urlopen(req, timeout=30).read()


def notify(text, dry_run):
    print(text)
    if dry_run:
        return
    discord = os.environ.get("DISCORD_WEBHOOK_URL")
    ntfy = os.environ.get("NTFY_TOPIC")
    if not discord and not ntfy:
        print("(no DISCORD_WEBHOOK_URL or NTFY_TOPIC set; alert only printed)")
    if discord:
        send_discord(discord, text)
    if ntfy:
        send_ntfy(ntfy, text)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--file", help="read this saved JSON instead of calling the API")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    state = load_state()
    new_state = {}
    errors = 0

    for realm in config.get("realms", ["eu", "na"]):
        try:
            if args.file:
                with open(args.file) as f:
                    data = json.load(f)
            else:
                data = fetch(API[realm])
        except Exception as e:  # network hiccup: keep old state, try next run
            print(f"[{realm}] fetch failed: {e}", file=sys.stderr)
            errors += 1
            for k, v in state.items():
                if k.startswith(realm + ":"):
                    new_state[k] = v
            continue

        for rule in config["watch"]:
            pattern = rule["item"]
            max_price = rule["max_price"]
            min_qty = rule.get("min_quantity", 1)
            key = f"{realm}:{pattern}"

            found = cheapest(data, pattern, min_qty)
            if not found or found["price"] > max_price:
                continue  # not below threshold; state resets so it can alert again later

            last = state.get(key)
            new_state[key] = found["price"]
            # Alert on first hit, or if the price has dropped further since the last alert.
            if last is None or found["price"] < last:
                s = found["stall"]
                notify(
                    f"[{realm.upper()}] {pattern}: {found['price']}g each "
                    f"(x{found['quantity']}) in {found['region']} "
                    f"@ stall ({s['x']:.0f}, {s['z']:.0f}). Your limit: {max_price}g.",
                    args.dry_run,
                )

    if not args.dry_run:
        with open(STATE_FILE, "w") as f:
            json.dump(new_state, f)

    return 1 if errors == len(config.get("realms", ["eu", "na"])) else 0


if __name__ == "__main__":
    sys.exit(main())
