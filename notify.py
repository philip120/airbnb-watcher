"""Send WhatsApp messages via CallMeBot.

Setup (one time, ~2 minutes):
  1. Save +34 644 51 95 23 to your phone contacts as "CallMeBot".
  2. WhatsApp it exactly: I allow callmebot to send me messages
  3. It replies with your personal API key.
"""

import os
import time
import urllib.parse

import requests

ENDPOINT = "https://api.callmebot.com/whatsapp.php"


class NotifyError(RuntimeError):
    pass


def send(text: str) -> None:
    phone = os.environ.get("WHATSAPP_PHONE", "").strip()
    apikey = os.environ.get("CALLMEBOT_APIKEY", "").strip()

    if not phone or not apikey:
        raise NotifyError(
            "WHATSAPP_PHONE and CALLMEBOT_APIKEY must be set "
            "(GitHub repo → Settings → Secrets and variables → Actions)."
        )

    params = {
        "phone": phone if phone.startswith("+") else f"+{phone}",
        "text": text,
        "apikey": apikey,
    }

    # CallMeBot is flaky under load; a couple of retries makes it dependable.
    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(
                f"{ENDPOINT}?{urllib.parse.urlencode(params)}", timeout=30
            )
            if response.ok:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        except requests.RequestException as exc:
            last_error = str(exc)
        if attempt < 2:
            time.sleep(5)

    raise NotifyError(f"WhatsApp send failed after 3 attempts — {last_error}")


def format_listings(listings: list[dict], config: dict) -> str:
    """CallMeBot delivers plain text, so keep it short and linkable."""
    count = len(listings)
    head = (
        f"🏠 {count} new Airbnb {'match' if count == 1 else 'matches'} in "
        f"{config['place_label']}\n"
        f"{config['checkin']} → {config['checkout']} · "
        f"{config['adults']} guests · under €{config['max_price']}"
        + (f" · within {config['radius_km']}km of centre\n" if config.get("radius_km") else "\n")
    )

    body = []
    for listing in listings[:8]:
        rating = f" ⭐{listing['rating']}" if listing.get("rating") else ""
        was = listing.get("was")
        if was:
            price = f"€{was:.0f} → €{listing['price']:.0f} 📉"
        elif listing.get("reappeared"):
            price = f"€{listing['price']:.0f} 🔓 FREED UP"
        else:
            price = f"€{listing['price']:.0f}"
        distance = listing.get("distance_km")
        where = f" · {distance}km from centre" if distance is not None else ""
        body.append(
            f"\n{price}{rating}{where}\n{listing['name'][:60]}\n{listing['url']}"
        )

    tail = f"\n\n+{count - 8} more" if count > 8 else ""
    return head + "".join(body) + tail
