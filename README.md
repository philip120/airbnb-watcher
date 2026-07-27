# Airbnb Watcher — Riga

Checks Airbnb every 30 minutes for stays matching your criteria and sends a
WhatsApp message when something **new or newly-cheaper** shows up.

- **Runs on** GitHub Actions (the cron scheduler — GitHub Pages can't run code)
- **Publishes to** GitHub Pages (a live dashboard of current matches)
- **Notifies via** CallMeBot → WhatsApp
- **Cost** €0

Current watch (`config.json`): **Riga**, 29 → 30 July 2026, 4 guests, under **€300 total**.

---

## Setup

### 1. Push to GitHub

```bash
gh repo create airbnb-watcher --private --source=. --push
```

### 2. Get your CallMeBot key (~2 min)

1. Save **+34 644 51 95 23** to your phone as a contact.
2. WhatsApp it exactly: `I allow callmebot to send me messages`
3. It replies with your personal API key.

### 3. Add the secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `WHATSAPP_PHONE` | your number with country code, e.g. `+37120123456` |
| `CALLMEBOT_APIKEY` | the key CallMeBot sent you |

### 4. Turn on Pages

Repo → **Settings → Pages → Source: GitHub Actions**.

### 5. Kick it off

Repo → **Actions → Airbnb watcher → Run workflow**.

The **first run stays silent** — it records what's already listed so you don't
get the whole city in one message. Tick *"Send WhatsApp even if this is the
first run"* if you want that initial dump anyway.

Your dashboard lands at `https://<you>.github.io/airbnb-watcher/`.

---

## Tuning

Edit `config.json`:

| Key | Meaning |
|---|---|
| `place` | Airbnb URL slug, e.g. `Riga--Latvia`, `Tallinn--Estonia` |
| `checkin` / `checkout` | `YYYY-MM-DD` |
| `adults` | guest count |
| `max_price` | **total** for the whole stay, not per night |
| `pages` | pages to scan (18 listings each) |
| `price_drop_pct` | re-notify when a known listing falls this much (default 10%) |
| `reappear_after_misses` | runs a listing must be absent before its return counts as a cancellation (default 2) |
| `center` | `[lat, lng]` to search around — currently Vecrīga (Old Town) |
| `radius_km` | how far from `center` to accept (default 3) |
| `exclude_keywords` | drop listings whose title contains any of these (campsites, vans) |

`center` + `radius_km` do double duty: they constrain Airbnb's search to that
map box *and* re-check each result's true distance afterwards, because Airbnb
returns listings outside the box it was given. Without the box, Airbnb ranks
across the whole municipality and buries central flats under cheap beach
campsites 6 km out.

Change the cadence via the `cron` line in `.github/workflows/watch.yml`.

---

## How it works

```
watch.yml (cron)
  └─ main.py
       ├─ scraper.py  → parses the JSON Airbnb embeds in its own search page
       ├─ diff vs state.json  → new, ≥10% cheaper, or freed-up listings
       ├─ notify.py   → CallMeBot → WhatsApp
       └─ docs/listings.json → committed → Pages dashboard
```

`state.json` is committed back after each run — that's the memory of what
you've already been told about.

You get pinged in three cases, and never for the same thing twice:

| Trigger | Message |
|---|---|
| Listing never seen before | `€89 ⭐4.75` |
| Known listing drops ≥10% | `€180 → €120 📉` |
| Listing was booked, then freed up | `€95 🔓 FREED UP` |

The third is the one that matters when a city is sold out. A listing that
disappears for 2+ consecutive runs and then returns is a **cancellation**. The
2-run threshold exists because Airbnb rotates its results — a listing missing
from a single run is search noise, not a booking.

---

## Caveats worth knowing

- **Airbnb has no public API.** This parses the JSON embedded in their search
  page. It works today; if Airbnb changes their markup it will need a fix. The
  workflow fails soft (logs a warning, exits 0) rather than spamming you with
  red builds.
- **Datacenter IPs get bot-checked.** GitHub runners are Azure IPs and Airbnb
  sometimes serves a challenge instead of results. Expect occasional skipped
  runs. If it becomes constant, route through a scraper API with residential
  proxies (Apify/ScraperAPI) — only `scraper._fetch_page` needs changing.
- **Scraping is against Airbnb's ToS.** This is low-volume personal use, but
  that's the tradeoff you're making.
- **GitHub cron is best-effort** — `*/30` often means 30–60 minutes in practice.
- **CallMeBot is a free hobby service** with no delivery guarantee. `notify.py`
  retries 3×; if all fail, the run fails loudly and those listings are *not*
  marked as seen, so the next run retries them.
