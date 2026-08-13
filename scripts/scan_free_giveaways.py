#!/usr/bin/env python3
"""
Steam giveaway hunter v0.5 for Karai.

Main changes:
- Better DLC resolver: several title variants + Steam app type decides whether a
  match is the DLC itself or only the base game.
- Steam metadata is requested in English for stable taste scoring while cc=RU
  still keeps regional price/availability checks in the RU store.
- Taste scoring is driven by the structure of taste_profile.json instead of a
  tiny fixed genre list.
- Produces plain-language reasons for each recommendation.
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

USER_AGENT = "KaraiSteamHunter/0.5"
REQUEST_DELAY_SECONDS = 0.35

HARD_REJECT_PHRASES = (
    "free weekend", "weekend trial", "play for free", "free trial",
    "trial version", "demo", "playtest", "beta", "closed beta", "open beta",
)

COSMETIC_OR_PROMO_PHRASES = (
    "skin", "skins", "weapon skin", "weapon skins", "avatar", "wallpaper",
    "soundtrack", "artbook", "art book", "booster", "currency", "coins",
    "gems", "decal", "emote", "emoji", "profile background", "profile item",
    "camo", "camouflage", "spray", "helmet", "hat", "headgear", "dice pack",
    "dice set", "soldier items", "vehicle bundle", "welcome bundle",
    "certificate", "rare camo", "cosmetic pack", "cosmetics",
)

SERVICE_REWARD_PHRASES = (
    "starter kit", "starter pack", "beginner's starter kit", "beginners starter kit",
    "premium points", "premium currency", "premium account", "premium days",
    "7-day", "7 day", "30-day", "30 day", "temporary buff", "temporary buffs",
    "buff set", "buff sets", "emblem code", "emblem", "redeem code",
    "account reward", "in-game items", "boost your early progress",
    "credits", "gold", "tokens", "reward points", "arp required",
)

GAMEPLAY_DLC_HINTS = (
    "expansion", "story dlc", "campaign", "quest", "mission", "missions",
    "chapter", "scenario", "content pack", "new area", "new region",
    "new map", "new maps", "character pack", "class", "faction",
)

# These are not the taste profile itself. They map profile concepts to text signals
# commonly available from Steam/GamerPower. We only enable concept groups that
# actually exist in taste_profile.json.
CONCEPT_PATTERNS = {
    "story_choices_and_consequences": (
        "choices matter", "multiple endings", "choice", "choices", "consequence",
        "consequences", "branching", "interactive fiction",
    ),
    "exploration_and_lore": (
        "exploration", "explore", "open world", "lore", "discover", "discovery",
        "atmospheric", "story rich",
    ),
    "meaningful_system_progression": (
        "progression", "upgrade", "upgrades", "unlock", "unlocks", "research",
        "technology", "development",
    ),
    "automation_and_logistics": (
        "automation", "automate", "factory", "production", "logistics", "conveyor",
        "drone", "drones", "supply chain", "resource extraction",
    ),
    "city_building_and_colony_management": (
        "city builder", "city-building", "colony", "colony sim", "settlement",
        "town builder", "city-building", "management",
    ),
    "base_building": (
        "base building", "base-building", "build your base", "building",
    ),
    "survival_with_exploration": (
        "survival", "survival crafting", "open world survival",
    ),
    "collectibles_with_clear_tracking": (
        "collectibles", "collection", "collect", "completion",
    ),
    "multiple_meaningful_routes_or_endings": (
        "multiple endings", "branching narrative", "multiple routes",
    ),
    "stealth_with_multiple_approaches": (
        "stealth", "multiple approaches", "non-lethal", "nonlethal",
    ),
    "work_simulator_with_progression_and_finish": (
        "job simulator", "work simulator", "career", "business simulation",
    ),
    "achievements": (
        "steam achievements", "achievements",
    ),
    "crafting_that_unlocks_new_capabilities": (
        "crafting", "craft", "blueprints",
    ),
    "management_and_resource_systems": (
        "management", "resource management", "economy", "resources",
    ),
    "text_heavy_storytelling": (
        "visual novel", "text-based", "story rich", "narrative",
    ),
    "cozy_farming_with_development": (
        "farming sim", "farming", "life sim", "cozy",
    ),
    "pure_stat_growth": (
        "incremental", "stat grinding", "grind",
    ),
    "repetitive_grind": (
        "grind", "grinding", "endless grind",
    ),
    "long_runback_after_failure": (
        "souls-like", "soulslike",
    ),
    "arena_or_competitive_shooter": (
        "competitive", "pvp", "arena shooter", "hero shooter",
    ),
    "power_fantasy_shooter_without_other_hooks": (
        "boomer shooter", "arena fps",
    ),
    "looter_shooter": (
        "looter shooter", "loot shooter",
    ),
    "soulslike_pattern_memorization": (
        "souls-like", "soulslike",
    ),
    "instant_fail_stealth_as_whole_game": (
        "instant fail stealth",
    ),
    "hidden_collectibles_without_tracking": (
        "hidden collectibles",
    ),
    "endless_routine_without_new_progress": (
        "idle", "clicker", "incremental",
    ),
}

RATING_WEIGHTS = {
    "very_strong_positive": 5,
    "strong_positive": 3,
    "positive": 2,
    "conditional_positive": 1,
    "neutral": 0,
    "negative": -3,
    "strong_negative": -5,
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
    t = re.sub(r"\s*Giveaway\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*\((?:Steam|PC)\)\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*\(Steam\)\s*Key(?:s)?\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*Steam\s+Key(?:s)?\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*Key(?:s)?\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*\((?:Steam|PC)\)\s*$", "", t, flags=re.I)
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

    if (
        "download this dlc directly via steam" in instructions
        or "download this pack directly via steam" in instructions
        or "download the dlc on steam" in instructions
    ):
        return "direct_steam_store"

    return "direct_or_unknown"


def classify_content(item):
    blob = text_blob(item)
    item_type = str(item.get("type", "")).lower()

    if item_type != "dlc":
        return "game_or_other"

    gameplay_hit = any(p in blob for p in GAMEPLAY_DLC_HINTS)
    cosmetic_hit = any(p in blob for p in COSMETIC_OR_PROMO_PHRASES)
    service_hit = any(p in blob for p in SERVICE_REWARD_PHRASES)

    if service_hit and not gameplay_hit:
        return "service_or_account_reward"
    if cosmetic_hit and not gameplay_hit:
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

    kind = classify_content(item)

    if kind == "service_or_account_reward":
        return True, "service_or_account_reward"

    if kind == "cosmetic_or_promo_dlc":
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


def normalize_tokens(value):
    return set(re.findall(r"[a-z0-9]+", str(value).casefold()))


def title_similarity(a, b):
    ta = normalize_tokens(a)
    tb = normalize_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def steam_search(title):
    payload = http_json(STEAM_SEARCH_URL, {"term": title, "l": "english", "cc": "RU"})
    return payload.get("items", []) if isinstance(payload, dict) else []


def title_variants(title):
    variants = [title]

    stripped = re.sub(r"\s+DLC\s*$", "", title, flags=re.I).strip()
    if stripped and stripped not in variants:
        variants.append(stripped)

    no_pack_word = re.sub(r"\s+Content\s+Pack\s*$", "", title, flags=re.I).strip()
    if no_pack_word and no_pack_word not in variants:
        variants.append(no_pack_word)

    punctuation_soft = re.sub(r"\s*[-:]\s*", " ", title)
    punctuation_soft = re.sub(r"\s+", " ", punctuation_soft).strip()
    if punctuation_soft and punctuation_soft not in variants:
        variants.append(punctuation_soft)

    return variants


def best_steam_match_for_queries(queries):
    candidates = []

    for query in queries:
        try:
            items = steam_search(query)
        except Exception:
            continue

        for item in items[:10]:
            name = str(item.get("name", ""))
            score = max(title_similarity(q, name) for q in queries)
            candidates.append((score, query, item))

        time.sleep(REQUEST_DELAY_SECONDS)

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    score, query, item = candidates[0]

    if score < 0.45:
        return None

    return {
        "appid": item.get("id"),
        "name": item.get("name"),
        "search_match_score": round(score, 3),
        "query_used": query,
    }


def base_game_title_from_dlc(title):
    if " - " in title:
        return title.split(" - ", 1)[0].strip()
    if ":" in title:
        return title.split(":", 1)[0].strip()
    return None


def resolve_steam_match(candidate):
    title = candidate["title"]
    direct_queries = title_variants(title)

    direct = best_steam_match_for_queries(direct_queries)
    if direct:
        direct["lookup_mode"] = "candidate_title_variants"
        return direct

    if str(candidate.get("type", "")).lower() == "dlc":
        base = base_game_title_from_dlc(title)
        if base and base.casefold() != title.casefold():
            fallback = best_steam_match_for_queries([base])
            if fallback:
                fallback["lookup_mode"] = "base_game_fallback"
                fallback["base_game_query"] = base
                return fallback

    return None


def inspect_steam_ru(appid):
    # English metadata for stable scoring; cc=RU preserves RU storefront price/availability.
    payload = http_json(
        STEAM_APPDETAILS_URL,
        {"appids": appid, "cc": "RU", "l": "english"},
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
        "dlc": data.get("dlc", []),
    }


def key_region_status(candidate):
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


def profile_enabled_signals(taste):
    signals = taste.get("signals") or {}
    enabled = {}

    for bucket, names in signals.items():
        weight = RATING_WEIGHTS.get(bucket, 0)
        if isinstance(names, list):
            for name in names:
                enabled[str(name)] = weight

    return enabled


def genre_preference_signals(taste):
    prefs = taste.get("genre_preferences") or {}
    result = {}

    # Genre-profile keys are mapped to common Steam wording.
    aliases = {
        "narrative_choices": ("choices matter", "story rich", "interactive fiction", "visual novel"),
        "dark_and_morally_heavy": ("dark", "psychological", "dystopian"),
        "survival": ("survival",),
        "base_building": ("base building", "building"),
        "crafting": ("crafting",),
        "sandbox": ("sandbox", "open world"),
        "automation_and_production": ("automation", "factory", "production"),
        "work_simulators": ("job simulator", "simulation"),
        "management": ("management",),
        "city_building_and_colonies": ("city builder", "colony sim", "city-building"),
        "detective_and_deduction": ("detective", "investigation", "mystery"),
        "shooters": ("shooter", "fps", "third-person shooter"),
        "soulslike": ("souls-like", "soulslike"),
        "stealth": ("stealth",),
        "roguelike_and_roguelite": ("roguelike", "roguelite"),
        "visual_novels_and_text_games": ("visual novel", "text-based"),
        "cozy_farming_and_daily_life": ("farming sim", "life sim", "cozy"),
        "metroidvania": ("metroidvania",),
        "co_op": ("co-op", "cooperative"),
    }

    for pref_name, pref in prefs.items():
        if not isinstance(pref, dict):
            continue
        rating = str(pref.get("rating", "neutral"))
        weight = RATING_WEIGHTS.get(rating, 0)
        for alias in aliases.get(pref_name, ()):
            result[alias] = (weight, pref_name)

    return result


def taste_score(candidate, taste):
    steam = candidate.get("steam_ru") or {}
    blob = " ".join([
        str(candidate.get("title", "")),
        str(candidate.get("description", "")),
        str(steam.get("short_description", "")),
        " ".join(steam.get("genres", []) or []),
        " ".join(steam.get("categories", []) or []),
    ]).lower()

    score = 0
    positives = []
    negatives = []
    matched_concepts = []

    enabled_signals = profile_enabled_signals(taste)

    for concept, concept_weight in enabled_signals.items():
        patterns = CONCEPT_PATTERNS.get(concept, ())
        hits = [p for p in patterns if p in blob]
        if not hits:
            continue

        # One concept scores once even if several synonyms appear.
        score += concept_weight
        matched_concepts.append({
            "concept": concept,
            "weight": concept_weight,
            "evidence": hits[:3],
        })

        if concept_weight > 0:
            positives.append(concept)
        elif concept_weight < 0:
            negatives.append(concept)

    for alias, (weight, pref_name) in genre_preference_signals(taste).items():
        if alias not in blob or weight == 0:
            continue

        # Genre preference is a modifier; weaker than primary concept signal.
        modifier = 1 if weight > 0 else -2
        if abs(weight) >= 3:
            modifier = 2 if weight > 0 else -3
        if abs(weight) >= 5:
            modifier = 3 if weight > 0 else -4

        score += modifier
        marker = f"genre:{pref_name}"

        if modifier > 0 and marker not in positives:
            positives.append(marker)
        if modifier < 0 and marker not in negatives:
            negatives.append(marker)

    # Steam achievements are explicitly a strong positive in the profile.
    categories = [str(x).lower() for x in steam.get("categories", []) or []]
    if any("steam achievements" in x for x in categories):
        score += 2
        positives.append("steam_achievements")

    return score, positives, negatives, matched_concepts


def explain_match(candidate, score, positives, negatives):
    bits = []

    if candidate.get("delivery") == "external_steam_key":
        bits.append("external Steam key; store price does not invalidate the giveaway")

    if candidate.get("content_kind") == "gameplay_dlc":
        bits.append("gameplay DLC rather than promo/account reward")

    if positives:
        bits.append("positive fit: " + ", ".join(positives[:4]))

    if negatives:
        bits.append("negative fit: " + ", ".join(negatives[:3]))

    if not positives and not negatives:
        bits.append("Steam/GamerPower metadata does not expose enough taste signals yet")

    return "; ".join(bits)


def recommendation_band(candidate, score):
    delivery = candidate.get("delivery")
    verification = (candidate.get("steam_ru") or {}).get("verification")
    key_region = candidate.get("key_region_status")

    if delivery == "external_steam_key":
        if key_region == "ru_blocked_explicit":
            return "skip"
        if verification == "reject_non_full_product":
            return "skip"
        if score >= 6:
            return "likely_match"
        if score >= 2:
            return "conditional"
        if score <= -4:
            return "low_priority"
        return "needs_review"

    if verification in {"reject_f2p", "reject_non_full_product", "not_free_in_ru"}:
        return "skip"

    if verification == "strong_keep_forever_candidate":
        if score >= 6:
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
            candidate["ownership"] = {
                "owned": True,
                "reason": "library_title_match",
            }
            continue

        match = resolve_steam_match(candidate)
        candidate["steam_match"] = match

        if not match or not match.get("appid"):
            candidate["steam_ru"] = {
                "checked": False,
                "verification": "needs_review",
                "reason": "no_confident_steam_appid_match",
            }

        else:
            appid = int(match["appid"])

            time.sleep(REQUEST_DELAY_SECONDS)
            try:
                inspected = inspect_steam_ru(appid)
            except Exception as exc:
                inspected = {
                    "checked": True,
                    "reachable": False,
                    "verification": "unknown",
                    "reason": f"steam_request_error:{type(exc).__name__}",
                }

            steam_type = inspected.get("steam_type")
            source_type = str(candidate.get("type", "")).lower()

            # v0.5: Steam's actual app type decides the product role.
            if source_type == "dlc" and steam_type == "game":
                candidate["base_game"] = {
                    "appid": appid,
                    "name": inspected.get("steam_name") or match.get("name"),
                    "owned": appid in owned_game_appids,
                }
                candidate["steam_ru"] = {
                    "checked": False,
                    "verification": "needs_review",
                    "reason": "base_game_found_but_dlc_appid_unresolved",
                    "base_game_store_metadata": inspected,
                }
                match["matched_product_role"] = "base_game_only"

            else:
                match["matched_product_role"] = "candidate_product"
                candidate["steam_ru"] = inspected

                if appid in owned_game_appids or appid in owned_dlc_appids:
                    candidate["ownership"] = {
                        "owned": True,
                        "reason": "appid_match",
                        "appid": appid,
                    }
                    continue

        score, positives, negatives, concepts = taste_score(candidate, taste)
        band = recommendation_band(candidate, score)

        candidate["taste"] = {
            "score": score,
            "positive_signals": positives,
            "negative_signals": negatives,
            "matched_concepts": concepts,
            "band": band,
            "explanation": explain_match(candidate, score, positives, negatives),
        }

        key = f"gamerpower:{candidate.get('source_id')}"
        old = history["items"].get(key, {})

        history["items"][key] = {
            "title": candidate["title"],
            "first_seen": old.get("first_seen", now),
            "last_seen": now,
            "last_band": band,
            "last_score": score,
            "last_verification": (candidate.get("steam_ru") or {}).get("verification"),
            "last_end_date": candidate.get("end_date"),
            "delivery": candidate.get("delivery"),
            "key_region_status": candidate.get("key_region_status"),
            "content_kind": candidate.get("content_kind"),
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
        "schema_version": 5,
        "source": "GamerPower",
        "source_attribution": "Data discovery by GamerPower.com",
        "updated_at_utc": now,
        "candidate_count": len(normalized),
        "items": normalized,
    })

    save_json(MATCHES_FILE, {
        "schema_version": 5,
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

    history["schema_version"] = 5
    history["updated_at_utc"] = now
    save_json(HISTORY_FILE, history)

    print(f"GamerPower candidates: {len(normalized)}")
    print(f"Filtered candidates: {len(matches)}")

    for item in matches[:20]:
        taste_info = item.get("taste") or {}
        verification = (item.get("steam_ru") or {}).get("verification")
        print(
            f"- [{taste_info.get('band')}] score={taste_info.get('score')} "
            f"[{item.get('content_kind')}] [{item.get('delivery')}] "
            f"[{verification}] {item.get('title')}"
        )


if __name__ == "__main__":
    main()
