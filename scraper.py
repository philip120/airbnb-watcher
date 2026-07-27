"""Fetch Airbnb search results by parsing the JSON state embedded in the search page.

Airbnb has no public API. The search page ships its full result set inside a
<script id="data-deferred-state-0"> blob, which is far less brittle than
reverse-engineering their persisted-query GraphQL hashes.
"""

import base64
import json
import re
import time
import urllib.parse
from typing import Any, Iterator

import requests

SEARCH_URL = "https://www.airbnb.com/s/{place}/homes"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

STATE_RE = re.compile(
    r'<script id="data-deferred-state-0"[^>]*>(.*?)</script>', re.S
)
PRICE_RE = re.compile(r"[\d,.]+")


class ScrapeError(RuntimeError):
    """Airbnb returned something we could not parse."""


def _cursor(items_offset: int) -> str:
    payload = json.dumps(
        {"section_offset": 0, "items_offset": items_offset, "version": 1},
        separators=(",", ":"),
    )
    return base64.b64encode(payload.encode()).decode()


def _walk(node: Any) -> Iterator[dict]:
    """Yield every StaySearchResult node, wherever Airbnb decides to nest it."""
    if isinstance(node, dict):
        if node.get("__typename") == "StaySearchResult":
            yield node
            return
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _parse_price(structured: dict | None) -> tuple[float | None, str | None]:
    """Pull a comparable number out of Airbnb's display price.

    Returns (amount, raw_label). We deliberately prefer the *total* line so the
    filter matches what the user actually pays, not a nightly teaser rate.
    """
    if not structured:
        return None, None
    line = structured.get("primaryLine") or {}
    label = line.get("accessibilityLabel") or line.get("price")
    raw = line.get("price") or label
    if not raw:
        return None, None
    match = PRICE_RE.search(raw.replace(" ", " "))
    if not match:
        return None, label
    try:
        return float(match.group().replace(",", "")), label
    except ValueError:
        return None, label


def _listing_id(result: dict) -> str | None:
    """demandStayListing.id is base64 of 'DemandStayListing:<roomId>'."""
    encoded = (result.get("demandStayListing") or {}).get("id")
    if not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded).decode()
    except Exception:
        return None
    return decoded.split(":")[-1] or None


def _normalise(result: dict, checkin: str, checkout: str, adults: int) -> dict | None:
    listing_id = _listing_id(result)
    if not listing_id:
        return None

    amount, label = _parse_price(result.get("structuredDisplayPrice"))
    demand = result.get("demandStayListing") or {}
    coord = (demand.get("location") or {}).get("coordinate") or {}
    name = (
        (result.get("nameLocalized") or {}).get(
            "localizedStringWithTranslationPreference"
        )
        or result.get("title")
        or "Untitled listing"
    )

    query = urllib.parse.urlencode(
        {
            "check_in": checkin,
            "check_out": checkout,
            "adults": adults,
        }
    )

    return {
        "id": listing_id,
        "name": name,
        "subtitle": result.get("subtitle"),
        "price": amount,
        "price_label": label,
        "rating": result.get("avgRatingLocalized"),
        "url": f"https://www.airbnb.com/rooms/{listing_id}?{query}",
        "lat": coord.get("latitude"),
        "lng": coord.get("longitude"),
    }


def _fetch_page(session: requests.Session, place: str, params: dict) -> dict:
    response = session.get(
        SEARCH_URL.format(place=urllib.parse.quote(place)),
        params=params,
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()

    match = STATE_RE.search(response.text)
    if not match:
        raise ScrapeError(
            "No embedded state found — Airbnb likely served a bot check "
            f"(HTTP {response.status_code}, {len(response.text)} bytes)."
        )
    return json.loads(match.group(1))


def search(
    place: str,
    checkin: str,
    checkout: str,
    adults: int,
    max_price: int,
    currency: str = "EUR",
    pages: int = 3,
) -> list[dict]:
    """Return listings at or under max_price, cheapest first."""
    session = requests.Session()
    listings: dict[str, dict] = {}

    for page in range(pages):
        params = {
            "checkin": checkin,
            "checkout": checkout,
            "adults": adults,
            "price_max": max_price,
            "currency": currency,
            "source": "structured_search_input_header",
            "search_type": "pagination" if page else "search_query",
        }
        if page:
            params["cursor"] = _cursor(page * 18)

        state = _fetch_page(session, place, params)
        found = 0
        for result in _walk(state):
            found += 1
            listing = _normalise(result, checkin, checkout, adults)
            # Airbnb's price_max is advisory; enforce it ourselves.
            if listing and listing["price"] is not None:
                if listing["price"] <= max_price:
                    listings[listing["id"]] = listing

        if found == 0:
            break
        if page + 1 < pages:
            time.sleep(2)  # be a polite scraper

    return sorted(listings.values(), key=lambda item: item["price"])
