#!/usr/bin/env python3
"""
Steam giveaway hunter v0.3 for Karai.

Focus:
- smarter title cleanup for key giveaways;
- stronger promo/cosmetic DLC rejection;
- distinguish DLC product match from base-game fallback;
- external Steam keys are validated as giveaways, not by Steam price;
- conservative RU/key-region handling.
"""

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LIBRARY_FILE = ROOT / "library.json"
OWNED_DLC_FILE = ROOT / "owned_dlc.json"
TASTE_FILE = ROOT / "taste_profile.json"

GIVEAWAYS_FILE = ROOT / "giveaways.json"
MATCHES_FILE = ROOT / "giveaway_matches.json"
HISTORY_FILE = ROOT / "giveaway_history.json"

GAMERPOWER_URL = "https://www.gamerpower.com/api/giveaways?platform=steam"
STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"

USER_AGENT = "KaraiSteamHunter/0.3"
REQUEST_DELAY_SECONDS = 0.35

HARD_REJECT_PHRASES = (
    "free weekend", "weekend trial", "play for free", "free trial",
    "trial version", "demo", "playtest", "beta", "closed beta", "open beta",
)

COSMETIC_OR_PROMO_PHRASES = (
    "skin", "skins", "weapon skin", "weapon skins", "avatar", "wallpaper",
    "soundtrack", "artbook", "art book", "booster", "currency", "coins",
    "gems", "premium account", "premium days", "decal", "emote", "emoji",
    "profile background", "profile item", "camo", "camouflage", "spray",
    "helmet", "hat", "headgear", "dice pack", "dice set", "soldier items",
    "vehicle bundle", "welcome bundle", "starter bundle", "starter pack",
    "certificate", "rare camo", "cosmetic pack", "cosmetics",
)

GAMEPLAY_DLC_HINTS = (
    "expansion", "story dlc", "campaign", "quest", "mission", "missions",
    "chapter", "scenario", "content pack", "new area", "new region",
    "new map", "new maps", "character pack", "class", "faction",
)

POSITIVE_TAG_WORDS = {
    "story": 2, "choices": 3, "choice": 3, "narrative": 2,
    "exploration": 3, "survival": 2, "base building": 3,
    "automation": 4, "management": 2, "city builder": 4,
    "colony": 4, "stealth": 3, "simulation": 1, "simulator": 1,
    "crafting": 1, "collect": 1, "adventure": 1, "visual novel": 2,
}

NEGATIVE_TAG_WORDS = {
    "pvp": -5, "competitive": -4, "battle royale": -6,
    "looter shooter": -6, "arena shooter": -5,
    "soulslike": -3, "precision platformer": -3, "idle": -2,
}


def utc_now():
    return datetime.now(timezone.utc)


def utc_now_iso():
    return utc_now().replace(microsecond=0).isoformat()


def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path, payload):
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def http_json(url, params=None, timeout=25):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def clean_title(title):
    t = str(title).strip()

    # Remove giveaway wrappers first.
    t = re.sub(r"\s*Giveaway\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*\((?:Steam|PC)\)\s*Giveaway\s*$", "", t, flags=re.I)

    # Normalize several key suffix forms:
    # "Dwarven Realms (Steam) Key"
    # "Dwarven Realms Steam Key"
    # "Dwarven Realms (Steam) Key Giveaway"
    t = re.sub(r"\s*\(Steam\)\s*Key(?:s)?\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*Steam\s+Key(?:s)?\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*Key(?:s)?\s*$", "", t, flags=re.I)

    return re.sub(r"\s+", " ", t).strip(" -")


def text_blob(item):
    return " ".join(
        str(item.get(k, "")) for k in
        ("title", "description", "instructions", "type", "platforms")
    ).lower()


def parse_gamerpower_date(value):
    if not value or str(value).upper() == "N/A":
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def is_expired(item):
    end = parse_gamerpower_date(item.get("end_date"))
    return bool(end and end < utc_now())


def classify_delivery(item):
    blob = text_blob(item)
    instructions = str(item.get("instructions", "")).lower()

    if (
        "steam key" in blob
        or "activate a product on steam" in instructions
        or "unlock your key" in instructions
        or "claim a key" in instructions
    ):
        return "external_steam_key"

    if "download this dlc directly via steam" in instructions:
        return "direct_steam_store"

    return "direct_or_unknown"


def classify_content(item):
    blob = text_blob(item)
    item_type = str(item.get("type", "")).lower()

    if item_type != "dlc":
        return "game_or_other"

    gameplay_hit = any(p in blob for p in GAMEPLAY_DLC_HINTS)
    promo_hit = any(p in blob for p in COSMETIC_OR_PROMO_PHRASES)

    if promo_hit and not gameplay_hit:
        return "cosmetic_or_promo_dlc"
    if gameplay_hit:
        return "gameplay_dlc"
    return "unknown_dlc"


def is_obvious_reject(item):
    blob = text_blob(item)

    if is_expired(item):
        return True, "expired_by_local_clock"

    for phrase in HARD_REJECT_PHRASES:
        if phrase in blob:
            return True, f"hard_reject:{phrase}"

    if str(item.get("type", "")).lower() == "beta":
        return True, "hard_reject:beta"

    if classify_content(item) == "cosmetic_or_promo_dlc":
        return True, "cosmetic_or_promo_dlc"

    return False, None


def normalize_gamerpower(item):
    return {
        "source": "GamerPower",
        "source_id": item.get("id"),
        "title": clean_title(item.get("title", "")),
        "source_title": item.get("title"),
        "type": str(item.get("type", "")),
        "platforms": item.get("platforms"),
        "description": item.get("description"),
        "instructions": item.get("instructions"),
        "worth": item.get("worth"),
        "published_date": item.get("published_date"),
        "end_date": item.get("end_date"),
        "status": item.get("status"),
        "delivery": classify_delivery(item),
        "content_kind": classify_content(item),
        "gamerpower_url": item.get("gamerpower_url")
            or item.get("open_giveaway_url")
            or item.get("open_giveaway"),
    }


def extract_owned_games(library):
    appids, names = set(), set()
    for game in library.get("games", []):
        try:
            appids.add(int(game["appid"]))
        except Exception:
            pass
        name = str(game.get("name", "")).strip().casefold()
        if name:
            names.add(name)
    return appids, names


def collect_owned_dlc_appids(data):
    found = set()

    def walk(value):
        if isinstance(value, dict):
            owned = value.get("owned") is True or str(value.get("status", "")).lower() == "owned"
            if owned:
                for key in ("appid", "app_id", "dlc_appid", "id"):
                    if key in value:
                        try:
                            found.add(int(value[key]))
                            break
                        except Exception:
                            pass
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    return found


def title_similarity(a, b):
    ta = set(re.findall(r"[a-zа-я0-9]+", str(a).casefold()))
    tb = set(re.findall(r"[a-zа-я0-9]+", str(b).casefold()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def steam_search(title):
    payload = http_json(STEAM_SEARCH_URL, {"term": title, "l": "russian", "cc": "RU"})
    return payload.get("items", []) if isinstance(payload, dict) else []


def best_steam_match(title):
    items = steam_search(title)
    ranked = []
    for item in items[:10]:
        ranked.append((title_similarity(title, item.get("name", "")), item))
    if not ranked:
        return None
    ranked.sort(key=lambda x: x[0], reverse=True)
    score, item = ranked[0]
    if score < 0.45:
        return None
    return {
        "appid": item.get("id"),
        "name": item.get("name"),
        "search_match_score": round(score, 3),
    }


def base_game_title_from_dlc(title):
    if " - " in title:
        return title.split(" - ", 1)[0].strip()
    if ":" in title:
        return title.split(":", 1)[0].strip()
    return None


def resolve_steam_match(candidate):
    title = candidate["title"]

    try:
        direct = best_steam_match(title)
        if direct:
            direct["lookup_mode"] = "direct_title"
            direct["matched_product_role"] = "candidate_product"
            return direct
    except Exception:
        pass

    # Important v0.3 rule:
    # base-game fallback is metadata only. It must NOT be treated as the DLC app itself.
    if str(candidate.get("type", "")).lower() == "dlc":
        base = base_game_title_from_dlc(title)
        if base and base.casefold() != title.casefold():
            try:
                fallback = best_steam_match(base)
                if fallback:
                    fallback["lookup_mode"] = "base_game_fallback"
                    fallback["matched_product_role"] = "base_game_only"
                    fallback["base_game_query"] = base
                    return fallback
            except Exception:
                pass

    return None


def inspect_steam_ru(appid):
    payload = http_json(
        STEAM_APPDETAILS_URL,
        {"appids": appid, "cc": "RU", "l": "russian"},
    )
    wrapper = payload.get(str(appid)) if isinstance(payload, dict) else None

    if not wrapper or not wrapper.get("success"):
        return {
            "checked": True,
            "reachable": False,
            "verification": "unknown",
            "reason": "appdetails_unavailable_for_ru",
        }

    data = wrapper.get("data") or {}
    app_type = str(data.get("type", "")).lower()
    is_free = bool(data.get("is_free"))
    price = data.get("price_overview") or {}
    initial = price.get("initial")
    final = price.get("final")
    discount = price.get("discount_percent")

    direct_keep_forever = (
        app_type in {"game", "dlc"}
        and isinstance(initial, int)
        and isinstance(final, int)
        and initial > 0
        and final == 0
        and discount == 100
    )

    if direct_keep_forever:
        verification = "strong_keep_forever_candidate"
        reason = "paid_app_at_100_percent_discount_in_ru"
    elif is_free:
        verification = "reject_f2p"
        reason = "steam_marks_app_as_permanently_free"
    elif app_type in {"demo", "beta", "mod"}:
        verification = "reject_non_full_product"
        reason = f"steam_type:{app_type}"
    elif price:
        verification = "not_free_in_ru"
        reason = f"ru_final_price:{final}"
    else:
        verification = "needs_review"
        reason = "no_ru_price_overview_or_nonstandard_offer"

    return {
        "checked": True,
        "reachable": True,
        "verification": verification,
        "reason": reason,
        "steam_type": app_type,
        "steam_name": data.get("name"),
        "is_free": is_free,
        "price_overview": price,
        "developers": data.get("developers", []),
        "publishers": data.get("publishers", []),
        "genres": [g.get("description") for g in data.get("genres", []) if isinstance(g, dict)],
        "categories": [c.get("description") for c in data.get("categories", []) if isinstance(c, dict)],
        "short_description": data.get("short_description"),
    }


def key_region_status(candidate):
    """
    Conservative key-region inference.
    We only claim 'no explicit RU block seen' when the giveaway text contains no
    obvious region restriction. We do NOT claim a key is guaranteed RU-valid.
    """
    blob = text_blob(candidate)

    ru_block_phrases = (
        "not available in russia",
        "not available in russian federation",
        "russia excluded",
        "excluding russia",
        "except russia",
        "ru excluded",
        "region locked: ru",
    )
    global_phrases = (
        "worldwide",
        "global key",
        "global steam key",
        "available worldwide",
    )

    if any(p in blob for p in ru_block_phrases):
        return "ru_blocked_explicit"

    if any(p in blob for p in global_phrases):
        return "global_claim_text_seen"

    return "unknown_no_explicit_ru_block_seen"


def taste_score(candidate):
    steam = candidate.get("steam_ru") or {}
    blob = " ".join([
        str(candidate.get("title", "")),
        str(candidate.get("description", "")),
        str(steam.get("short_description", "")),
        " ".join(steam.get("genres", []) or []),
        " ".join(steam.get("categories", []) or []),
    ]).lower()

    score, positives, negatives = 0, [], []

    for phrase, points in POSITIVE_TAG_WORDS.items():
        if phrase in blob:
            score += points
            positives.append(phrase)

    for phrase, points in NEGATIVE_TAG_WORDS.items():
        if phrase in blob:
            score += points
            negatives.append(phrase)

    return score, positives, negatives


def recommendation_band(candidate, score):
    delivery = candidate.get("delivery")
    verification = (candidate.get("steam_ru") or {}).get("verification")
    key_region = candidate.get("key_region_status")

    if delivery == "external_steam_key":
        if key_region == "ru_blocked_explicit":
            return "skip"
        if verification == "reject_non_full_product":
            return "skip"
        if score >= 5:
            return "likely_match"
        if score >= 2:
            return "conditional"
        if score <= -4:
            return "low_priority"
        return "needs_review"

    if verification in {"reject_f2p", "reject_non_full_product", "not_free_in_ru"}:
        return "skip"

    if verification == "strong_keep_forever_candidate":
        if score >= 5:
            return "must_claim"
        if score >= 2:
            return "likely_match"
        if score <= -4:
            return "low_priority"
        return "conditional"

    return "needs_review"


def main():
    library = load_json(LIBRARY_FILE, {})
    owned_dlc_data = load_json(OWNED_DLC_FILE, {})
    taste = load_json(TASTE_FILE, {})

    owned_game_appids, owned_game_names = extract_owned_games(library)
    owned_dlc_appids = collect_owned_dlc_appids(owned_dlc_data)

    raw = http_json(GAMERPOWER_URL)
    if not isinstance(raw, list):
        raw = []

    history = load_json(HISTORY_FILE, {"schema_version": 1, "items": {}})
    history.setdefault("schema_version", 1)
    history.setdefault("items", {})

    normalized, matches = [], []
    now = utc_now_iso()

    for source_item in raw:
        if not isinstance(source_item, dict):
            continue

        candidate = normalize_gamerpower(source_item)
        candidate["key_region_status"] = (
            key_region_status(source_item)
            if candidate["delivery"] == "external_steam_key"
            else None
        )

        rejected, reason = is_obvious_reject(source_item)
        candidate["pre_filter"] = {"rejected": rejected, "reason": reason}
        normalized.append(candidate)

        if rejected:
            continue

        if candidate["title"].casefold() in owned_game_names:
            candidate["ownership"] = {"owned": True, "reason": "library_title_match"}
            continue

        time.sleep(REQUEST_DELAY_SECONDS)
        match = resolve_steam_match(candidate)
        candidate["steam_match"] = match

        # Ownership & RU check depend on whether the match is the candidate product
        # or only the base game.
        if match and match.get("appid") and match.get("matched_product_role") == "candidate_product":
            appid = int(match["appid"])

            if appid in owned_game_appids or appid in owned_dlc_appids:
                candidate["ownership"] = {
                    "owned": True,
                    "reason": "appid_match",
                    "appid": appid,
                }
                continue

            time.sleep(REQUEST_DELAY_SECONDS)
            try:
                candidate["steam_ru"] = inspect_steam_ru(appid)
            except Exception as exc:
                candidate["steam_ru"] = {
                    "checked": True,
                    "reachable": False,
                    "verification": "unknown",
                    "reason": f"steam_request_error:{type(exc).__name__}",
                }

        elif match and match.get("matched_product_role") == "base_game_only":
            base_appid = int(match["appid"])
            candidate["base_game"] = {
                "appid": base_appid,
                "name": match.get("name"),
                "owned": base_appid in owned_game_appids,
            }
            candidate["steam_ru"] = {
                "checked": False,
                "verification": "needs_review",
                "reason": "base_game_found_but_dlc_appid_unresolved",
            }

        else:
            candidate["steam_ru"] = {
                "checked": False,
                "verification": "needs_review",
                "reason": "no_confident_steam_appid_match",
            }

        score, positives, negatives = taste_score(candidate)
        band = recommendation_band(candidate, score)

        candidate["taste"] = {
            "score": score,
            "positive_signals": positives,
            "negative_signals": negatives,
            "band": band,
        }

        key = f"gamerpower:{candidate.get('source_id')}"
        old = history["items"].get(key, {})

        history["items"][key] = {
            "title": candidate["title"],
            "first_seen": old.get("first_seen", now),
            "last_seen": now,
            "last_band": band,
            "last_verification": (candidate.get("steam_ru") or {}).get("verification"),
            "last_end_date": candidate.get("end_date"),
            "delivery": candidate.get("delivery"),
            "key_region_status": candidate.get("key_region_status"),
        }

        matches.append(candidate)

    band_order = {
        "must_claim": 0,
        "likely_match": 1,
        "conditional": 2,
        "family_only": 3,
        "needs_review": 4,
        "low_priority": 5,
        "skip": 6,
    }

    matches.sort(
        key=lambda item: (
            band_order.get((item.get("taste") or {}).get("band"), 99),
            -int((item.get("taste") or {}).get("score", 0)),
            str(item.get("end_date") or "9999"),
        )
    )

    save_json(GIVEAWAYS_FILE, {
        "schema_version": 3,
        "source": "GamerPower",
        "source_attribution": "Data discovery by GamerPower.com",
        "updated_at_utc": now,
        "candidate_count": len(normalized),
        "items": normalized,
    })

    save_json(MATCHES_FILE, {
        "schema_version": 3,
        "updated_at_utc": now,
        "region": (taste.get("hard_filters") or {}).get("region", "RU"),
        "match_count": len(matches),
        "bands": {
            band: sum(
                1 for item in matches
                if (item.get("taste") or {}).get("band") == band
            )
            for band in band_order
        },
        "items": matches,
    })

    history["schema_version"] = 3
    history["updated_at_utc"] = now
    save_json(HISTORY_FILE, history)

    print(f"GamerPower candidates: {len(normalized)}")
    print(f"Filtered candidates: {len(matches)}")
    for item in matches[:20]:
        band = (item.get("taste") or {}).get("band")
        verification = (item.get("steam_ru") or {}).get("verification")
        delivery = item.get("delivery")
        print(f"- [{band}] [{delivery}] [{verification}] {item.get('title')}")


if __name__ == "__main__":
    main()
