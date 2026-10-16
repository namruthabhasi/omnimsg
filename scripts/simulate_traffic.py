"""
Traffic Simulator Script:
Builds and sends realistic payloads for WhatsApp, Instagram, Messenger, and Telegram.
Computes HMAC-SHA256 signatures for Meta endpoints and secret token headers for Telegram.

Usage:
    python scripts/simulate_traffic.py --channel all --count 3
    python scripts/simulate_traffic.py --channel whatsapp --count 5
    python scripts/simulate_traffic.py --channel telegram --url http://localhost:8000
"""

import argparse
import hashlib
import hmac
import json
import os
import random
import sys
import time
import uuid

import httpx

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings

SAMPLE_CUSTOMERS = [
    {"id": "16315550101", "name": "Emily Watson", "username": "emilyw"},
    {"id": "16315550102", "name": "David Miller", "username": "davidm"},
    {"id": "16315550103", "name": "Sophia Chen", "username": "sophiac"},
    {"id": "16315550104", "name": "Marcus Johnson", "username": "marcusj"},
    {"id": "16315550105", "name": "Aisha Patel", "username": "aishap"},
]

SAMPLE_MESSAGES = [
    "Hello! Do you offer international shipping?",
    "Can you help me track order #98214?",
    "What is your return policy for open items?",
    "Do you have a discount code for first-time buyers?",
    "I'd like to speak with a human support representative.",
    "Are your products cruelty-free and certified?",
    "How long does express delivery usually take?",
]


def sign_meta_payload(payload_bytes: bytes, secret: str) -> str:
    """Computes X-Hub-Signature-256 for Meta payloads."""
    digest = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_whatsapp_payload(customer: dict, text: str) -> dict:
    msg_id = f"wamid.{uuid.uuid4().hex}"
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "100234567890123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550001111",
                                "phone_number_id": "100000000000001",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": customer["name"]},
                                    "wa_id": customer["id"],
                                }
                            ],
                            "messages": [
                                {
                                    "from": customer["id"],
                                    "id": msg_id,
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def build_meta_messaging_payload(object_type: str, customer: dict, text: str) -> dict:
    mid = f"m_{uuid.uuid4().hex[:16]}"
    return {
        "object": object_type,
        "entry": [
            {
                "id": "10987654321",
                "time": int(time.time() * 1000),
                "messaging": [
                    {
                        "sender": {"id": customer["id"]},
                        "recipient": {"id": "10987654321"},
                        "timestamp": int(time.time() * 1000),
                        "message": {
                            "mid": mid,
                            "text": text,
                            "is_echo": False,
                        },
                    }
                ],
            }
        ],
    }


def build_telegram_payload(customer: dict, text: str) -> dict:
    first_name, *rest = customer["name"].split()
    last_name = rest[0] if rest else ""
    return {
        "update_id": random.randint(100000, 999999),
        "message": {
            "message_id": random.randint(100, 9999),
            "date": int(time.time()),
            "chat": {
                "id": int(customer["id"][-6:]),
                "first_name": first_name,
                "last_name": last_name,
                "username": customer["username"],
                "type": "private",
            },
            "from": {
                "id": int(customer["id"][-6:]),
                "is_bot": False,
                "first_name": first_name,
                "last_name": last_name,
                "username": customer["username"],
            },
            "text": text,
        },
    }


def simulate_channel(
    channel: str,
    base_url: str,
    meta_secret: str,
    tg_secret: str,
) -> bool:
    customer = random.choice(SAMPLE_CUSTOMERS)
    text = random.choice(SAMPLE_MESSAGES)

    url = f"{base_url}/api/v1/webhooks/{channel}"
    headers = {"Content-Type": "application/json"}

    if channel == "whatsapp":
        payload = build_whatsapp_payload(customer, text)
        body = json.dumps(payload).encode("utf-8")
        headers["X-Hub-Signature-256"] = sign_meta_payload(body, meta_secret)
    elif channel == "instagram":
        payload = build_meta_messaging_payload("instagram", customer, text)
        body = json.dumps(payload).encode("utf-8")
        headers["X-Hub-Signature-256"] = sign_meta_payload(body, meta_secret)
    elif channel == "messenger":
        payload = build_meta_messaging_payload("page", customer, text)
        body = json.dumps(payload).encode("utf-8")
        headers["X-Hub-Signature-256"] = sign_meta_payload(body, meta_secret)
    elif channel == "telegram":
        payload = build_telegram_payload(customer, text)
        body = json.dumps(payload).encode("utf-8")
        headers["X-Telegram-Bot-Api-Secret-Token"] = tg_secret
    else:
        print(f"Unknown channel: {channel}")
        return False

    try:
        resp = httpx.post(url, content=body, headers=headers, timeout=10.0)
        status_code = resp.status_code
        print(f"[{channel.upper():10}] Status {status_code} | {customer['name']}: '{text}'")
        return status_code == 200
    except Exception as exc:
        print(f"[{channel.upper():10}] Request failed: {exc}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Multi-Channel Webhook Traffic Simulator")
    parser.add_argument(
        "--channel",
        choices=["all", "whatsapp", "instagram", "messenger", "telegram"],
        default="all",
        help="Target channel to simulate (default: all)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of messages to send per channel (default: 1)",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Base server URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--meta-secret",
        default=settings.META_APP_SECRET or "simulator_meta_secret_key",
        help="Meta App Secret for HMAC signing",
    )
    parser.add_argument(
        "--telegram-secret",
        default=settings.TELEGRAM_WEBHOOK_SECRET or "simulator_tg_secret_key",
        help="Telegram Secret Token",
    )

    args = parser.parse_args()

    channels = (
        ["whatsapp", "instagram", "messenger", "telegram"]
        if args.channel == "all"
        else [args.channel]
    )

    print(f"Simulating {args.count} message(s) per channel across: {', '.join(channels)}")
    print(f"Target URL: {args.url}\n" + "─" * 60)

    for i in range(args.count):
        for ch in channels:
            simulate_channel(
                channel=ch,
                base_url=args.url.rstrip("/"),
                meta_secret=args.meta_secret,
                tg_secret=args.telegram_secret,
            )
            time.sleep(0.1)


if __name__ == "__main__":
    main()
