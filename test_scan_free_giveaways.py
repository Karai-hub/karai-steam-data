import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if SCRIPTS_DIR.is_dir():
    sys.path.insert(0, str(SCRIPTS_DIR))

import scan_free_giveaways as hunter


class DlcParentResolutionTests(unittest.TestCase):
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
