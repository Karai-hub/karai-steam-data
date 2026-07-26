#!/usr/bin/env python3
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STEAM_ID = "76561198256278066"
LIBRARY_FILE = Path("library.json")
WISHLIST_FILE = Path("wishlist.json")
LIBRARY_API_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
WISHLIST_API_URL = f"https://store.steampowered.com/wishlist/profiles/{STEAM_ID}/wishlistdata/"
COUNTRY_CODE = os.environ.get("STEAM_COUNTRY_CODE", "DE").strip().upper() or "DE"
LANGUAGE = os.environ.get("STEAM_LANGUAGE", "russian").strip() or "russian"
USER_AGENT = "Karai-Steam-Data-Updater/2.0"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def fetch_json(url: str, *, attempts: int = 4, timeout: int = 45) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Referer": f"https://store.steampowered.com/wishlist/profiles/{STEAM_ID}/",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts:
                delay = 3 * attempt
                print(f"Request failed ({exc}); retrying in {delay}s...")
                time.sleep(delay)
    raise RuntimeError(str(last_error) if last_error else "Unknown request error")


def update_library(api_key: str, updated_at: str) -> None:
    params = urllib.parse.urlencode({
        "key": api_key,
        "steamid": STEAM_ID,
        "include_appinfo": "true",
        "include_played_free_games": "true",
        "format": "json",
    })
    payload = fetch_json(f"{LIBRARY_API_URL}?{params}")
    games = payload.get("response", {}).get("games")
    if games is None:
        raise RuntimeError("Steam returned no owned games. Check API key and profile privacy.")

    cleaned_games = []
    for game in games:
        forever = int(game.get("playtime_forever", 0))
        recent = int(game.get("playtime_2weeks", 0))
        cleaned_games.append({
            "appid": int(game["appid"]),
            "name": game.get("name", f"App {game['appid']}"),
            "playtime_forever_minutes": forever,
            "playtime_forever_hours": round(forever / 60, 1),
            "playtime_2weeks_minutes": recent,
            "playtime_2weeks_hours": round(recent / 60, 1),
        })

    cleaned_games.sort(key=lambda item: item["name"].casefold())
    output = {
        "steamid": STEAM_ID,
        "updated_at_utc": updated_at,
        "game_count": len(cleaned_games),
        "total_playtime_hours": round(
            sum(item["playtime_forever_minutes"] for item in cleaned_games) / 60, 1
        ),
        "games": cleaned_games,
    }
    LIBRARY_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(cleaned_games)} games to {LIBRARY_FILE}")


def select_lowest_package(subs: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [
        sub for sub in subs
        if isinstance(sub, dict) and isinstance(sub.get("price"), int)
    ]
    return min(valid, key=lambda sub: sub["price"]) if valid else None


def update_wishlist(updated_at: str) -> None:
    all_items: dict[str, dict[str, Any]] = {}

    for page in range(100):
        params = urllib.parse.urlencode({
            "p": page,
            "cc": COUNTRY_CODE,
            "l": LANGUAGE,
        })
        payload = fetch_json(f"{WISHLIST_API_URL}?{params}")
        if not payload:
            break
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Unexpected wishlist response on page {page}: {type(payload).__name__}"
            )
        all_items.update(payload)
        print(f"Fetched wishlist page {page}: {len(payload)} items")
        time.sleep(1)
    else:
        raise RuntimeError("Wishlist pagination exceeded 100 pages; refusing to save incomplete data.")

    cleaned_items = []
    for appid_text, item in all_items.items():
        if not isinstance(item, dict):
            continue

        appid = int(appid_text)
        package = select_lowest_package(item.get("subs", []))
        added_unix = int(item.get("added", 0) or 0)
        cleaned = {
            "appid": appid,
            "name": item.get("name", f"App {appid}"),
            "priority": int(item.get("priority", 0) or 0),
            "rank": int(item.get("rank", 0) or 0),
            "date_added_utc": (
                datetime.fromtimestamp(added_unix, timezone.utc).isoformat(timespec="seconds")
                if added_unix else None
            ),
            "release_date": item.get("release_date") or item.get("release_string"),
            "type": item.get("type"),
            "is_free_game": bool(item.get("is_free_game", False)),
            "review_score": item.get("review_score"),
            "review_description": item.get("review_desc"),
            "reviews_total": item.get("reviews_total"),
            "capsule_image": item.get("capsule"),
            "tags": item.get("tags", []),
            "price": None,
        }

        if package is not None:
            final_price_minor_units = int(package.get("price", 0))
            cleaned["price"] = {
                "country_code": COUNTRY_CODE,
                "package_id": package.get("id"),
                "final_price_minor_units": final_price_minor_units,
                "final_price": round(final_price_minor_units / 100, 2),
                "discount_percent": int(package.get("discount_pct", 0) or 0),
            }

        cleaned_items.append(cleaned)

    cleaned_items.sort(key=lambda item: (
        item["priority"], item["rank"], item["name"].casefold()
    ))

    output = {
        "steamid": STEAM_ID,
        "updated_at_utc": updated_at,
        "country_code": COUNTRY_CODE,
        "language": LANGUAGE,
        "wishlist_count": len(cleaned_items),
        "items": cleaned_items,
    }
    WISHLIST_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(cleaned_items)} wishlist items to {WISHLIST_FILE}")


def main() -> None:
    api_key = os.environ.get("STEAM_API_KEY", "").strip()
    if not api_key:
        fail("GitHub secret STEAM_API_KEY is missing or empty.")

    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        update_library(api_key, updated_at)
        update_wishlist(updated_at)
    except Exception as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
