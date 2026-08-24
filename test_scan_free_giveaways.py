import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if SCRIPTS_DIR.is_dir():
    sys.path.insert(0, str(SCRIPTS_DIR))

import scan_free_giveaways as hunter


class DlcParentResolutionTests(unittest.TestCase):
    def test_catalog_resolved_owned_dlc_is_not_recommended(self):
        source = {
            "id": 1,
            "title": "Foo - Story DLC",
            "type": "DLC",
            "platforms": "PC, Steam",
            "description": "A story expansion for Foo. The base game Foo is required.",
            "instructions": "Download this DLC directly via Steam.",
            "end_date": "N/A",
            "status": "Active",
        }
        base_metadata = {
            "appid": 100,
            "checked": True,
            "reachable": True,
            "verification": "not_free_in_ru",
            "steam_type": "game",
            "steam_name": "Foo",
            "dlc": [200],
            "fullgame": None,
        }
        dlc_match = {
            "appid": 200,
            "name": "Foo - Story DLC",
            "matched_product_role": "candidate_product",
        }
        dlc_metadata = {
            "appid": 200,
            "checked": True,
            "reachable": True,
            "verification": "strong_keep_forever_candidate",
            "steam_type": "dlc",
            "steam_name": "Foo - Story DLC",
            "fullgame": {"appid": 100, "name": "Foo"},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_file = root / "library.json"
            owned_dlc_file = root / "owned_dlc.json"
            taste_file = root / "taste_profile.json"
            history_file = root / "giveaway_history.json"
            giveaways_file = root / "giveaways.json"
            matches_file = root / "giveaway_matches.json"

            library_file.write_text(
                json.dumps({"games": [{"appid": 100, "name": "Foo"}]}),
                encoding="utf-8",
            )
            owned_dlc_file.write_text(
                json.dumps({"items": [{"appid": 200, "owned": True}]}),
                encoding="utf-8",
            )
            taste_file.write_text("{}", encoding="utf-8")

            with patch.multiple(
                hunter,
                LIBRARY_FILE=library_file,
                OWNED_DLC_FILE=owned_dlc_file,
                TASTE_FILE=taste_file,
                HISTORY_FILE=history_file,
                GIVEAWAYS_FILE=giveaways_file,
                MATCHES_FILE=matches_file,
            ), patch.object(hunter, "http_json", return_value=[source]), patch.object(
                hunter,
                "resolve_steam_match",
                return_value={"appid": 100, "name": "Foo"},
            ), patch.object(
                hunter, "inspect_steam_ru", return_value=base_metadata
            ), patch.object(
                hunter,
                "resolve_dlc_from_base_catalog",
                return_value=(dlc_match, dlc_metadata),
            ), patch.object(hunter.time, "sleep", return_value=None):
                hunter.main()

            matches = json.loads(matches_file.read_text(encoding="utf-8"))
            giveaways = json.loads(giveaways_file.read_text(encoding="utf-8"))

        self.assertEqual(0, matches["match_count"])
        self.assertEqual([], matches["items"])
        self.assertEqual(
            {"owned": True, "reason": "appid_match", "appid": 200},
            giveaways["items"][0]["ownership"],
        )

    def test_catalog_rejects_dlc_whose_fullgame_is_a_different_base(self):
        candidate = {
            "title": "DAVE THE DIVER - Godzilla Content Pack",
            "type": "DLC",
            "description": "Godzilla content for DAVE THE DIVER.",
        }
        dave_base_metadata = {"steam_name": "DAVE THE DIVER", "dlc": [4099830]}
        wrong_dlc_metadata = {
            "steam_type": "dlc",
            "steam_name": "Magicraft - DAVE THE DIVER Content Pack",
            "fullgame": {"appid": 2103140, "name": "Magicraft"},
        }

        with patch.object(hunter, "inspect_steam_ru", return_value=wrong_dlc_metadata), patch.object(
            hunter.time, "sleep", return_value=None
        ):
            match, metadata = hunter.resolve_dlc_from_base_catalog(
                candidate, dave_base_metadata
            )

        self.assertIsNone(match)
        self.assertIsNone(metadata)

    def test_catalog_rejects_matching_parent_name_with_wrong_appid(self):
        candidate = {
            "title": "Foo - Story DLC",
            "type": "DLC",
            "description": "Story content for Foo.",
        }
        foo_base_metadata = {"appid": 100, "steam_name": "Foo", "dlc": [300]}
        wrong_parent_metadata = {
            "appid": 300,
            "steam_type": "dlc",
            "steam_name": "Foo - Story DLC",
            "fullgame": {"appid": 200, "name": "Foo"},
        }

        with patch.object(hunter, "inspect_steam_ru", return_value=wrong_parent_metadata), patch.object(
            hunter.time, "sleep", return_value=None
        ):
            match, metadata = hunter.resolve_dlc_from_base_catalog(
                candidate, foo_base_metadata
            )

        self.assertIsNone(match)
        self.assertIsNone(metadata)

    def test_ownership_gate_rejects_a_mismatched_fullgame_relation(self):
        candidate = {
            "title": "DAVE THE DIVER - Godzilla Content Pack",
            "type": "DLC",
            "steam_ru": {
                "steam_type": "dlc",
                "fullgame": {"appid": 2103140, "name": "Magicraft"},
            },
        }

        eligible = hunter.apply_dlc_ownership_gate(candidate, {2103140})

        self.assertFalse(eligible)
        self.assertIsNone(candidate["dlc_gate"]["eligible"])
        self.assertEqual("base_game_name_mismatch", candidate["dlc_gate"]["reason"])

    def test_ownership_gate_rejects_matching_parent_name_with_wrong_appid(self):
        candidate = {
            "title": "Foo - Story DLC",
            "type": "DLC",
            "base_game": {"appid": 100, "name": "Foo"},
            "steam_match": {"appid": 300, "matched_product_role": "candidate_product"},
            "steam_ru": {
                "appid": 300,
                "steam_type": "dlc",
                "fullgame": {"appid": 200, "name": "Foo"},
            },
        }

        eligible = hunter.apply_dlc_ownership_gate(candidate, {100, 200})

        self.assertFalse(eligible)
        self.assertIsNone(candidate["dlc_gate"]["eligible"])
        self.assertEqual("base_game_appid_mismatch", candidate["dlc_gate"]["reason"])

    def test_owned_base_without_resolved_dlc_stays_unresolved(self):
        candidate = {
            "title": "Foo - Missing Story DLC",
            "type": "DLC",
            "content_kind": "gameplay_dlc",
            "delivery": "external_steam_key",
            "key_region_status": "unknown_no_explicit_ru_block_seen",
            "base_game": {"appid": 100, "name": "Foo", "owned": True},
            "steam_match": {"appid": 100, "matched_product_role": "base_game_only"},
            "steam_ru": {
                "checked": False,
                "verification": "needs_review",
                "reason": "base_game_found_but_dlc_appid_unresolved",
            },
            "description": "Unlock story progression.",
        }

        eligible = hunter.apply_dlc_ownership_gate(candidate, {100})
        score, positives, negatives, concepts = hunter.taste_score(
            candidate,
            {"signals": {"very_strong_positive": ["meaningful_system_progression"]}},
        )
        band = hunter.recommendation_band(candidate, score)

        self.assertFalse(eligible)
        self.assertIsNone(candidate["dlc_gate"]["eligible"])
        self.assertEqual("dlc_product_unresolved", candidate["dlc_gate"]["reason"])
        self.assertEqual((0, [], [], []), (score, positives, negatives, concepts))
        self.assertEqual("needs_review", band)


class DlcTasteGateTests(unittest.TestCase):
    def setUp(self):
        self.taste = {
            "signals": {"very_strong_positive": ["meaningful_system_progression"]},
            "genre_preferences": {},
        }

    def test_cosmetic_unlock_does_not_count_as_progression(self):
        candidate = {
            "title": "EVERSPACE 2 Decal DLC",
            "type": "DLC",
            "description": "Unlock the Alienware decal for your ships.",
            "content_kind": "cosmetic_or_promo_dlc",
            "dlc_gate": {"eligible": True, "reason": "base_game_owned"},
            "steam_ru": {},
        }

        score, positives, negatives, concepts = hunter.taste_score(
            candidate, self.taste
        )

        self.assertEqual(0, score)
        self.assertNotIn("meaningful_system_progression", positives)
        self.assertEqual([], concepts)

    def test_unresolved_base_blocks_taste_scoring_before_band_selection(self):
        candidate = {
            "title": "Unknown Story Expansion",
            "type": "DLC",
            "description": "Unlock upgrades through meaningful progression.",
            "content_kind": "gameplay_dlc",
            "dlc_gate": {"eligible": None, "reason": "base_game_unresolved"},
            "steam_ru": {},
        }

        score, positives, negatives, concepts = hunter.taste_score(
            candidate, self.taste
        )

        self.assertEqual(0, score)
        self.assertEqual([], positives)
        self.assertEqual([], negatives)
        self.assertEqual([], concepts)

    def test_unresolved_dlc_cannot_rank_above_needs_review(self):
        candidate = {
            "type": "DLC",
            "delivery": "external_steam_key",
            "key_region_status": "unknown_no_explicit_ru_block_seen",
            "content_kind": "cosmetic_or_promo_dlc",
            "dlc_gate": {"eligible": None, "reason": "base_game_unresolved"},
            "steam_ru": {"verification": "needs_review"},
        }

        self.assertEqual("needs_review", hunter.recommendation_band(candidate, 99))

    def test_cosmetic_content_pack_is_not_promoted_by_unlock_wording(self):
        source = {
            "title": "Foo Cosmetic Content Pack",
            "description": "Unlock a decal for your ship.",
            "instructions": "Claim the DLC.",
            "type": "DLC",
            "platforms": "PC, Steam",
        }

        kind = hunter.classify_content(source)
        candidate = hunter.normalize_gamerpower(source)
        candidate["dlc_gate"] = {"eligible": True, "reason": "base_game_owned"}
        candidate["steam_ru"] = {}
        score, positives, _, concepts = hunter.taste_score(candidate, self.taste)

        self.assertEqual("cosmetic_or_promo_dlc", kind)
        self.assertEqual(0, score)
        self.assertNotIn("meaningful_system_progression", positives)
        self.assertEqual([], concepts)

    def test_classic_cosmetic_does_not_match_gameplay_class_marker(self):
        source = {
            "title": "Foo Classic Skin Pack",
            "description": "Unlock a classic cosmetic skin for your ship.",
            "instructions": "Claim the DLC.",
            "type": "DLC",
            "platforms": "PC, Steam",
        }

        kind = hunter.classify_content(source)
        candidate = hunter.normalize_gamerpower(source)
        candidate["dlc_gate"] = {"eligible": True, "reason": "base_game_owned"}
        candidate["steam_ru"] = {}
        score, positives, _, concepts = hunter.taste_score(candidate, self.taste)

        self.assertEqual("cosmetic_or_promo_dlc", kind)
        self.assertEqual(0, score)
        self.assertNotIn("meaningful_system_progression", positives)
        self.assertEqual([], concepts)


class SourceIntegrityTests(unittest.TestCase):
    def test_http_json_classifies_gamerpower_http_failure(self):
        error = urllib.error.HTTPError(
            hunter.GAMERPOWER_URL, 429, "rate limited", {}, io.BytesIO()
        )
        with patch.object(hunter.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(hunter.SourceRequestError) as caught:
                hunter.http_json(hunter.GAMERPOWER_URL)

        self.assertEqual("gamerpower", caught.exception.source)
        self.assertEqual("rate_limited", caught.exception.category)

    def test_http_json_classifies_steam_malformed_json(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"not-json"
        with patch.object(hunter.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(hunter.SourceRequestError) as caught:
                hunter.http_json(hunter.STEAM_SEARCH_URL)

        self.assertEqual("steam", caught.exception.source)
        self.assertEqual("malformed_response", caught.exception.category)

    def test_steam_search_failure_is_not_returned_as_no_match(self):
        with patch.object(
            hunter, "steam_search", side_effect=RuntimeError("Steam Search 429")
        ):
            with self.assertRaisesRegex(RuntimeError, "Steam Search 429"):
                hunter.best_steam_match_for_queries(["Foo", "Foo Game"])

    def test_malformed_gamerpower_schema_fails_before_writing_empty_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_paths = {
                "LIBRARY_FILE": root / "library.json",
                "OWNED_DLC_FILE": root / "owned_dlc.json",
                "TASTE_FILE": root / "taste_profile.json",
                "HISTORY_FILE": root / "giveaway_history.json",
            }
            output_paths = {
                "GIVEAWAYS_FILE": root / "giveaways.json",
                "MATCHES_FILE": root / "giveaway_matches.json",
            }

            with patch.multiple(hunter, **input_paths, **output_paths), patch.object(
                hunter, "http_json", return_value={"unexpected": "schema"}
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "GamerPower.*expected a list"
                ):
                    hunter.main()

            self.assertFalse(output_paths["GIVEAWAYS_FILE"].exists())
            self.assertFalse(output_paths["MATCHES_FILE"].exists())

    def test_steam_appdetails_failure_marks_partial_results_degraded(self):
        source = {
            "id": 99,
            "title": "Foo Adventure",
            "type": "Game",
            "platforms": "PC, Steam",
            "description": "Explore a story-rich world.",
            "instructions": "Claim the game.",
            "end_date": "N/A",
            "status": "Active",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_paths = {
                "LIBRARY_FILE": root / "library.json",
                "OWNED_DLC_FILE": root / "owned_dlc.json",
                "TASTE_FILE": root / "taste_profile.json",
                "HISTORY_FILE": root / "giveaway_history.json",
            }
            output_paths = {
                "GIVEAWAYS_FILE": root / "giveaways.json",
                "MATCHES_FILE": root / "giveaway_matches.json",
            }

            with patch.multiple(hunter, **input_paths, **output_paths), patch.object(
                hunter, "http_json", return_value=[source]
            ), patch.object(
                hunter,
                "resolve_steam_match",
                return_value={"appid": 123, "name": "Foo Adventure"},
            ), patch.object(
                hunter,
                "inspect_steam_ru",
                side_effect=hunter.SourceRequestError(
                    "steam", "rate_limited", "Steam request failed with HTTP 429."
                ),
            ), patch.object(hunter.time, "sleep", return_value=None):
                hunter.main()

            giveaways = json.loads(
                output_paths["GIVEAWAYS_FILE"].read_text(encoding="utf-8")
            )
            matches = json.loads(
                output_paths["MATCHES_FILE"].read_text(encoding="utf-8")
            )

        for payload in (giveaways, matches):
            self.assertTrue(payload["run_degraded"])
            self.assertEqual(
                [{
                    "source": "steam",
                    "operation": "appdetails",
                    "category": "rate_limited",
                    "message": "Steam request failed with HTTP 429.",
                    "appid": 123,
                }],
                payload["source_errors"],
            )


class NonDlcRegressionTests(unittest.TestCase):
    def test_regular_game_scoring_is_unchanged_by_dlc_gate(self):
        candidate = {
            "title": "Story Explorer",
            "type": "Game",
            "description": "Explore a world full of lore.",
            "content_kind": "game_or_other",
            "steam_ru": {},
        }
        taste = {"signals": {"very_strong_positive": ["exploration_and_lore"]}}

        score, positives, negatives, concepts = hunter.taste_score(candidate, taste)

        self.assertEqual(5, score)
        self.assertEqual(["exploration_and_lore"], positives)
        self.assertEqual([], negatives)
        self.assertEqual("exploration_and_lore", concepts[0]["concept"])


if __name__ == "__main__":
    unittest.main()
