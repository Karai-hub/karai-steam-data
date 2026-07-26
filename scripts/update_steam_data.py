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
WISHLIST_API_URL = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"

COUNTRY_CODE = os.environ.get("STEAM_COUNTRY_CODE", "DE").strip().upper() or "DE"
LANGUAGE = os.environ.get("STEAM_LANGUAGE", "russian").strip() or "russian"
USER_AGENT = "Karai-Steam-Data-Updater/2.1"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def fetch_json(
    url: str,
    *,
    attempts: int = 4,
    timeout: int = 45,
    allow_empty: bool = False,
) -> Any:
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()

            if not raw.strip():
                if allow_empty:
                    return None
                raise RuntimeError("Steam returned an empty response.")

            return json.loads(raw.decode("utf-8-sig"))

        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            RuntimeError,
        ) as exc:
            last_error = exc

            if attempt < attempts:
                delay = 3 * attempt
                print(f"Request failed ({exc}); retrying in {delay}s...")
                time.sleep(delay)

    raise RuntimeError(str(last_error) if last_error else "Unknown request error")


def update_library(api_key: str, updated_at: str) -> None:
    params = urllib.parse.urlencode(
        {
            "key": api_key,
            "steamid": STEAM_ID,
            "include_appinfo": "true",
            "include_played_free_games": "true",
            "format": "json",
        }
    )

    payload = fetch_json(f"{LIBRARY_API_URL}?{params}")
    games = payload.get("response", {}).get("games")

    if games is None:
        raise RuntimeError(
            "Steam returned no owned games. Check API key and profile privacy."
        )

    cleaned_games = []

    for game in games:
        forever = int(game.get("playtime_forever", 0))
        recent = int(game.get("playtime_2weeks", 0))

        cleaned_games.append(
            {
                "appid": int(game["appid"]),
                "name": game.get("name", f"App {game['appid']}"),
                "playtime_forever_minutes": forever,
                "playtime_forever_hours": round(forever / 60, 1),
                "playtime_2weeks_minutes": recent,
                "playtime_2weeks_hours": round(recent / 60, 1),
            }
        )

    cleaned_games.sort(key=lambda item: item["name"].casefold())

    output = {
        "steamid": STEAM_ID,
        "updated_at_utc": updated_at,
        "game_count": len(cleaned_games),
        "total_playtime_hours": round(
            sum(item["playtime_forever_minutes"] for item in cleaned_games) / 60,
            1,
        ),
        "games": cleaned_games,
    }

    LIBRARY_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Saved {len(cleaned_games)} games to {LIBRARY_FILE}")


def get_wishlist_items() -> list[dict[str, Any]]:
    input_json = json.dumps({"steamid": STEAM_ID}, separators=(",", ":"))
    params = urllib.parse.urlencode({"input_json": input_json})
    payload = fetch_json(f"{WISHLIST_API_URL}?{params}")

    response = payload.get("response", {})
    items = response.get("items")

    if items is None:
        raise RuntimeError(
            "Steam returned no wishlist items field. "
            "Check that the wishlist is public."
        )

    if not isinstance(items, list):
        raise RuntimeError(
            f"Unexpected wishlist items type: {type(items).__name__}"
        )

    return items


def get_app_details(appid: int) -> dict[str, Any] | None:
    params = urllib.parse.urlencode(
        {
            "appids": appid,
            "cc": COUNTRY_CODE,
            "l": LANGUAGE,
        }
    )

    payload = fetch_json(
        f"{APP_DETAILS_URL}?{params}",
        attempts=3,
        timeout=45,
        allow_empty=True,
    )

    if not isinstance(payload, dict):
        return None

    app_entry = payload.get(str(appid))

    if not isinstance(app_entry, dict) or not app_entry.get("success"):
        return None

    data = app_entry.get("data")
    return data if isinstance(data, dict) else None


def clean_price(price_overview: Any) -> dict[str, Any] | None:
    if not isinstance(price_overview, dict):
        return None

    initial = price_overview.get("initial")
    final = price_overview.get("final")

    return {
        "country_code": COUNTRY_CODE,
        "currency": price_overview.get("currency"),
        "initial_price_minor_units": initial,
        "initial_price": round(initial / 100, 2) if isinstance(initial, int) else None,
        "final_price_minor_units": final,
        "final_price": round(final / 100, 2) if isinstance(final, int) else None,
        "discount_percent": int(price_overview.get("discount_percent", 0) or 0),
        "formatted_initial": price_overview.get("initial_formatted"),
        "formatted_final": price_overview.get("final_formatted"),
    }


def update_wishlist(updated_at: str) -> None:
    raw_items = get_wishlist_items()
    cleaned_items = []

    print(f"Steam wishlist contains {len(raw_items)} items.")

    for index, item in enumerate(raw_items, start=1):
        appid = int(item["appid"])
        print(f"[{index}/{len(raw_items)}] Fetching store details for app {appid}...")

        details = get_app_details(appid)

        date_added_unix = int(
            item.get("date_added")
            or item.get("date_added_timestamp")
            or 0
        )

        cleaned = {
            "appid": appid,
            "name": (
                details.get("name")
                if details
                else f"App {appid}"
            ),
            "priority": int(item.get("priority", 0) or 0),
            "date_added_utc": (
                datetime.fromtimestamp(
                    date_added_unix, timezone.utc
                ).isoformat(timespec="seconds")
                if date_added_unix
                else None
            ),
            "type": details.get("type") if details else None,
            "is_free": bool(details.get("is_free", False)) if details else None,
            "short_description": (
                details.get("short_description") if details else None
            ),
            "header_image": details.get("header_image") if details else None,
            "release_date": (
                details.get("release_date") if details else None
            ),
            "developers": details.get("developers", []) if details else [],
            "publishers": details.get("publishers", []) if details else [],
            "genres": (
                [
                    genre.get("description")
                    for genre in details.get("genres", [])
                    if isinstance(genre, dict) and genre.get("description")
                ]
                if details
                else []
            ),
            "price": (
                clean_price(details.get("price_overview"))
                if details
                else None
            ),
            "store_details_available": details is not None,
        }

        cleaned_items.append(cleaned)

        # Небольшая пауза, чтобы не долбить Steam Store слишком часто.
        time.sleep(0.7)

    cleaned_items.sort(
        key=lambda entry: (
            entry["priority"],
            entry["name"].casefold(),
        )
    )

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
