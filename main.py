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


def load_seen() -> dict[str, dict]:
    """Map of listing id -> {price, misses}.

    `misses` counts consecutive runs the listing was absent from results. A
    listing that vanishes (booked) and later returns is a cancellation — the
    single most valuable alert when a city is sold out.
    """
    if not STATE_PATH.exists():
        return {}
    try:
        raw = json.loads(STATE_PATH.read_text()).get("seen", {})
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return {}

    seen = {}
    for listing_id, value in dict(raw).items():
        # Tolerate the older `id -> price` format.
        if isinstance(value, dict):
            seen[listing_id] = {
                "price": value.get("price"),
                "misses": int(value.get("misses", 0)),
            }
        else:
            seen[listing_id] = {"price": value, "misses": 0}
    return seen


def save_seen(seen: dict[str, dict]) -> None:
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
    # Airbnb rotates results, so a single absent run is noise, not a booking.
    # Requiring two consecutive misses filters that out.
    absent_runs = config.get("reappear_after_misses", 2)
    live = {item["id"] for item in listings}

    fresh = []
    for item in listings:
        previous = seen.get(item["id"])
        if previous is None:
            fresh.append(item)
        elif previous["misses"] >= absent_runs:
            fresh.append({**item, "reappeared": True})
        elif previous["price"] and item["price"] <= previous["price"] * (1 - drop):
            # Already notified, but it's gotten meaningfully cheaper — worth a ping.
            fresh.append({**item, "was": previous["price"]})

    current = {item["id"]: {"price": item["price"], "misses": 0} for item in listings}
    # Carry forward listings that dropped out, incrementing their absence.
    for listing_id, previous in seen.items():
        if listing_id not in live:
            current[listing_id] = {
                "price": previous["price"],
                "misses": previous["misses"] + 1,
            }

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
