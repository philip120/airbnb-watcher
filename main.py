"""Entry point: scrape → diff against last run → notify → publish dashboard."""

import json
import os
import pathlib
import sys
from datetime import datetime, timezone

import notify
import scraper

ROOT = pathlib.Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
DOCS_DATA = ROOT / "docs" / "listings.json"


def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text())
    # Env overrides let you retune the watch without editing the repo.
    if os.environ.get("MAX_PRICE"):
        config["max_price"] = int(os.environ["MAX_PRICE"])
    return config


def load_seen() -> dict[str, float]:
    """Map of listing id -> the price we last told you about."""
    if not STATE_PATH.exists():
        return {}
    try:
        return dict(json.loads(STATE_PATH.read_text()).get("seen", {}))
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return {}


def save_seen(seen: dict[str, float]) -> None:
    STATE_PATH.write_text(
        json.dumps(
            {
                "seen": dict(sorted(seen.items())),
                "updated": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )


def publish(listings: list[dict], config: dict) -> None:
    DOCS_DATA.parent.mkdir(parents=True, exist_ok=True)
    DOCS_DATA.write_text(
        json.dumps(
            {
                "updated": datetime.now(timezone.utc).isoformat(),
                "config": config,
                "listings": listings,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def main() -> int:
    config = load_config()
    first_run = not STATE_PATH.exists()

    try:
        listings = scraper.search(
            place=config["place"],
            checkin=config["checkin"],
            checkout=config["checkout"],
            adults=config["adults"],
            max_price=config["max_price"],
            currency=config.get("currency", "EUR"),
            pages=config.get("pages", 3),
        )
    except scraper.ScrapeError as exc:
        # A bot check is expected occasionally; fail soft so the cron keeps running.
        print(f"::warning::Scrape blocked — {exc}")
        return 0

    print(f"Found {len(listings)} listings under €{config['max_price']}")
    publish(listings, config)

    seen = load_seen()
    drop = config.get("price_drop_pct", 10) / 100

    fresh = []
    for item in listings:
        previous = seen.get(item["id"])
        if previous is None:
            fresh.append(item)
        elif item["price"] <= previous * (1 - drop):
            # Already notified, but it gotten meaningfully cheaper — worth a ping.
            fresh.append({**item, "was": previous})

    current = {item["id"]: item["price"] for item in listings}

    # The first run would otherwise dump the entire city into your chat.
    if first_run and not os.environ.get("NOTIFY_ON_FIRST_RUN"):
        save_seen(current)
        print(f"First run: seeded {len(fresh)} listings, notification suppressed.")
        return 0

    if not fresh:
        save_seen({**seen, **current})
        print("Nothing new or cheaper since last run — no notification sent.")
        return 0

    print(f"{len(fresh)} new/cheaper listing(s) — sending WhatsApp")
    try:
        notify.send(notify.format_listings(fresh, config))
    except notify.NotifyError as exc:
        # Do NOT record these as seen, or a transient WhatsApp outage would
        # silently bury listings you were never told about.
        print(f"::error::{exc}")
        return 1

    save_seen({**seen, **current})
    print("WhatsApp sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
