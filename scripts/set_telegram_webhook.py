"""
Setup helper CLI to configure or inspect Telegram Bot Webhook.

Usage:
    python scripts/set_telegram_webhook.py --info
    python scripts/set_telegram_webhook.py --url https://your-domain.com/api/v1/webhooks/telegram --secret your_secret_token
"""

import argparse
import os
import sys

import httpx

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure Telegram Bot Webhook")
    parser.add_argument("--url", help="Public webhook URL to receive Telegram updates")
    parser.add_argument(
        "--secret",
        default=settings.TELEGRAM_WEBHOOK_SECRET,
        help="Secret token for X-Telegram-Bot-Api-Secret-Token verification",
    )
    parser.add_argument(
        "--token",
        default=settings.TELEGRAM_BOT_TOKEN,
        help="Telegram Bot Token (default: read from TELEGRAM_BOT_TOKEN env)",
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Query getWebhookInfo to check current webhook status",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete current webhook configuration",
    )

    args = parser.parse_args()

    if not args.token:
        print("Error: Telegram bot token must be provided via --token or TELEGRAM_BOT_TOKEN in .env")
        sys.exit(1)

    base_url = f"https://api.telegram.org/bot{args.token}"

    if args.info:
        resp = httpx.get(f"{base_url}/getWebhookInfo", timeout=10.0)
        print("Telegram getWebhookInfo response:")
        print(resp.json())
        return

    if args.delete:
        resp = httpx.post(f"{base_url}/deleteWebhook", timeout=10.0)
        print("Telegram deleteWebhook response:")
        print(resp.json())
        return

    if not args.url:
        print("Error: --url is required when setting webhook (or use --info / --delete)")
        sys.exit(1)

    payload = {
        "url": args.url,
        "allowed_updates": ["message"],
    }
    if args.secret:
        payload["secret_token"] = args.secret

    resp = httpx.post(f"{base_url}/setWebhook", json=payload, timeout=10.0)
    print("Telegram setWebhook response:")
    print(resp.json())


if __name__ == "__main__":
    main()
