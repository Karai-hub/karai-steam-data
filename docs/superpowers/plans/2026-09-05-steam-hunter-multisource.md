# Steam Hunter Multi-Source Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the existing Steam giveaway scanner from GamerPower-only discovery to GamerPower, direct Steam specials, and FreeGameFindings RSS with conservative deduplication and independently reported source health.

**Architecture:** Add a standard-library-only source adapter module that returns normalized source observations and source status. Keep the mature Steam verification, ownership, DLC, taste, history, and snapshot logic in `scan_free_giveaways.py`; feed it canonical offers produced by a separate conservative merge module. Persist last-good source batches so one failed provider cannot erase otherwise usable results.

**Tech Stack:** Python 3.12 standard library, `unittest`, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-09-05-steam-hunter-web-design.md`

## Global Constraints

- Platform is exactly `steam`; profile is exactly `karai` in this release.
- Preserve all existing RU, ownership, DLC, F2P, free-weekend, scoring, retry, degradation, history, and 24-hour heartbeat behavior.
- Use no new credential or API key.
- Use only the Python standard library.
- Treat Steam search and Reddit RSS as discovery, never as proof of keep-forever or RU availability.
- Do not merge on fuzzy title alone when product role or delivery evidence conflicts.
- Never replace a last-good provider batch with malformed data.
- Do not emit existing history as newly discovered during migration.
- End every task with the full relevant test suite passing and an independent commit.

---

## Planned File Structure

- Create `scripts/hunter_sources.py`: source batch contract, request helpers, GamerPower/Steam/FreeGameFindings adapters, provider-specific parsing and validation.
- Create `scripts/hunter_merge.py`: canonical identity, conservative deduplication, provenance aggregation, possible-duplicate diagnostics.
- Create `test_hunter_sources.py`: source adapter fixtures and request/error tests.
- Create `test_hunter_merge.py`: canonical identity and deduplication tests.
- Modify `scripts/scan_free_giveaways.py`: orchestration over canonical offers, compatibility wrappers, history-key migration, output schema 0.8.0, last-good source cache.
- Modify `test_scan_free_giveaways.py`: pipeline, migration, cache, and schema regression coverage.
- Create `source_snapshots.json`: last-good observations per discovery source.
- Create `source_status.json`: independent current source health and freshness.
- Modify `giveaways.json`, `giveaway_matches.json`, and `giveaway_history.json`: schema 0.8.0 canonical output.
- Modify `.github/workflows/scan-steam-giveaways.yml`: run all hunter tests and commit every generated data file.

---

### Task 1: Introduce the Source Batch Contract and GamerPower Adapter

**Files:**
- Create: `scripts/hunter_sources.py`
- Create: `test_hunter_sources.py`
- Modify: `scripts/scan_free_giveaways.py`

**Interfaces:**
- Produces: `SourceBatch(source: str, attribution: str, observed_at: str, items: list[dict], diagnostics: list[dict])`
- Produces: `collect_gamerpower(fetch_json: Callable[[str], object], observed_at: str) -> SourceBatch`
- Preserves: `scan_free_giveaways.SourceRequestError`, `http_json()`, and `normalize_gamerpower()` imports used by existing tests.

- [ ] **Step 1: Write the failing GamerPower adapter contract tests**

```python
class GamerPowerAdapterTests(unittest.TestCase):
    def test_collect_gamerpower_returns_source_batch(self):
        raw = [{
            "id": 7,
            "title": "Foo (Steam) Giveaway",
            "type": "Game",
            "platforms": "PC, Steam",
            "description": "Claim Foo.",
            "instructions": "Activate on Steam.",
            "open_giveaway_url": "https://example.test/foo",
            "published_date": "2026-09-05 08:00:00",
            "end_date": "2026-09-06 08:00:00",
            "status": "Active",
        }]
        batch = sources.collect_gamerpower(lambda _: raw, NOW)
        self.assertEqual("gamerpower", batch.source)
        self.assertEqual(1, len(batch.items))
        self.assertEqual("gamerpower:7", batch.items[0]["observation_id"])
        self.assertEqual("steam", batch.items[0]["platform"])

    def test_collect_gamerpower_rejects_non_object_items(self):
        with self.assertRaises(sources.SourceRequestError) as caught:
            sources.collect_gamerpower(lambda _: ["broken"], NOW)
        self.assertEqual("malformed_response", caught.exception.category)
```

- [ ] **Step 2: Run the focused tests and verify the missing module failure**

Run: `python -m unittest -v test_hunter_sources.GamerPowerAdapterTests`

Expected: FAIL because `hunter_sources` does not exist.

- [ ] **Step 3: Implement the batch contract and GamerPower adapter**

```python
@dataclass(frozen=True)
class SourceBatch:
    source: str
    attribution: str
    observed_at: str
    items: list[dict]
    diagnostics: list[dict] = field(default_factory=list)


def collect_gamerpower(fetch_json, observed_at):
    raw = fetch_json(GAMERPOWER_URL)
    require_object_list("gamerpower", raw)
    return SourceBatch(
        source="gamerpower",
        attribution="Data discovery by GamerPower.com",
        observed_at=observed_at,
        items=[normalize_gamerpower_observation(item) for item in raw],
    )
```

Each observation must include `observation_id`, `source`, `source_id`,
`source_url`, `claim_url`, `source_title`, `title`, `type`,
`platforms`, `platform`, `description`, `instructions`, `worth`,
`published_date`, `end_date`, `status`, and `raw`.

Move the generic request exception into `hunter_sources.py` and import/re-export
it from `scan_free_giveaways.py` so all existing exception assertions remain
valid.

- [ ] **Step 4: Route the current main path through `collect_gamerpower`**

```python
batch = collect_gamerpower(http_json, now)
for observation in batch.items:
    candidate = normalize_observation(observation)
```

`normalize_observation()` in the existing scanner must produce the same
GamerPower candidate fields as `normalize_gamerpower()`; keep
`normalize_gamerpower()` as a compatibility wrapper.

- [ ] **Step 5: Run focused and legacy tests**

Run: `python -m unittest -v test_hunter_sources.py test_scan_free_giveaways.py test_workflow_concurrency.py`

Expected: all tests PASS and the serialized GamerPower-only fixtures retain
their existing recommendation results.

- [ ] **Step 6: Commit the isolated adapter boundary**

```bash
git add scripts/hunter_sources.py scripts/scan_free_giveaways.py test_hunter_sources.py
git commit -m "Refactor GamerPower behind source adapter"
```

---

### Task 2: Add Direct Steam Free-Special Discovery

**Files:**
- Modify: `scripts/hunter_sources.py`
- Modify: `test_hunter_sources.py`

**Interfaces:**
- Consumes: `SourceBatch` and `SourceRequestError` from Task 1.
- Produces: `collect_steam_specials(fetch_json: Callable[[str], object], observed_at: str) -> SourceBatch`
- Produces: `parse_steam_results_html(html: str) -> list[dict]`

- [ ] **Step 1: Add a failing stored Steam search fixture test**

```python
STEAM_RESULTS = {
    "success": 1,
    "total_count": 1,
    "start": 0,
    "results_html": """
      <a href="https://store.steampowered.com/app/123/Foo/"
         data-ds-appid="123" class="search_result_row">
        <span class="title">Foo</span>
        <div class="discount_pct">-100%</div>
        <div class="discount_final_price">Бесплатно</div>
      </a>
    """,
}

def test_collect_steam_specials_extracts_direct_app(self):
    batch = sources.collect_steam_specials(lambda _: STEAM_RESULTS, NOW)
    item = batch.items[0]
    self.assertEqual("steam_specials:app:123", item["observation_id"])
    self.assertEqual(123, item["steam_appid"])
    self.assertEqual("direct_steam_store", item["delivery"])
```

Also add explicit tests for an empty successful page, `success != 1`, a
non-string `results_html`, and a result without an app/package ID.

- [ ] **Step 2: Run the Steam adapter tests and verify they fail**

Run: `python -m unittest -v test_hunter_sources.SteamSpecialsAdapterTests`

Expected: FAIL because `collect_steam_specials` is undefined.

- [ ] **Step 3: Implement first-party Steam discovery**

```python
STEAM_SPECIALS_URL = (
    "https://store.steampowered.com/search/results/"
    "?query=&start=0&count=100&dynamic_data=&sort_by=_ASC"
    "&specials=1&maxprice=free&category1=998&cc=RU&l=russian&infinite=1"
)

def collect_steam_specials(fetch_json, observed_at):
    payload = fetch_json(STEAM_SPECIALS_URL)
    require_mapping("steam_specials", payload)
    if payload.get("success") != 1 or not isinstance(payload.get("results_html"), str):
        raise SourceRequestError(
            "steam_specials", "malformed_response",
            "Steam specials returned malformed response."
        )
    return SourceBatch(
        "steam_specials",
        "Direct discovery from store.steampowered.com",
        observed_at,
        parse_steam_results_html(payload["results_html"]),
    )
```

Use `html.parser.HTMLParser`, not regex alone, to collect result anchors,
titles, discount text, and price text. Retain only rows with exact `-100%` or a
free final-price label. Missing IDs are ignored with a parse diagnostic rather
than invented from title.

- [ ] **Step 4: Run the full source test file**

Run: `python -m unittest -v test_hunter_sources.py`

Expected: all source adapter tests PASS, including a real empty-results shape.

- [ ] **Step 5: Commit direct Steam discovery**

```bash
git add scripts/hunter_sources.py test_hunter_sources.py
git commit -m "Add direct Steam giveaway discovery"
```

---

### Task 3: Add FreeGameFindings Steam RSS Discovery

**Files:**
- Modify: `scripts/hunter_sources.py`
- Modify: `test_hunter_sources.py`

**Interfaces:**
- Consumes: `SourceBatch` and `SourceRequestError`.
- Produces: `collect_freegamefindings(fetch_text: Callable[[str], str], observed_at: str) -> SourceBatch`
- Produces: `parse_freegamefindings_feed(xml_text: str) -> list[dict]`

- [ ] **Step 1: Write failing Atom-feed tests**

```python
FGF_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>t3_example</id>
    <title>[Steam] Foo</title>
    <updated>2026-09-05T08:00:00+00:00</updated>
    <link href="https://www.reddit.com/r/FreeGameFindings/comments/example/foo/"/>
    <content type="html">&lt;a href="https://store.steampowered.com/app/123/Foo/"&gt;Steam&lt;/a&gt;</content>
  </entry>
</feed>"""

def test_collect_freegamefindings_extracts_steam_post(self):
    batch = sources.collect_freegamefindings(lambda _: FGF_ATOM, NOW)
    item = batch.items[0]
    self.assertEqual("freegamefindings:t3_example", item["observation_id"])
    self.assertEqual(123, item["steam_appid"])
    self.assertEqual("https://store.steampowered.com/app/123/Foo/", item["claim_url"])
```

Add cases for malformed XML, a non-Steam entry, `[PSA]`, an explicitly
`[Expired]` title, and HTML containing both Reddit and Steam links.

- [ ] **Step 2: Run the RSS adapter tests and verify they fail**

Run: `python -m unittest -v test_hunter_sources.FreeGameFindingsAdapterTests`

Expected: FAIL because the RSS functions are undefined.

- [ ] **Step 3: Implement Atom parsing and Steam filtering**

```python
FREEGAMEFINDINGS_URL = (
    "https://www.reddit.com/r/FreeGameFindings/search.rss"
    "?q=title%3ASteam&restrict_sr=1&sort=new"
)

def collect_freegamefindings(fetch_text, observed_at):
    try:
        items = parse_freegamefindings_feed(fetch_text(FREEGAMEFINDINGS_URL))
    except ElementTree.ParseError as exc:
        raise SourceRequestError(
            "freegamefindings", "malformed_response",
            "FreeGameFindings returned malformed Atom XML."
        ) from exc
    return SourceBatch(
        "freegamefindings",
        "Community discovery by r/FreeGameFindings",
        observed_at,
        items,
    )
```

Parse with `xml.etree.ElementTree` and `html.parser.HTMLParser`. Prefer the
first Steam app/package link as `claim_url`; retain the Reddit permalink as
`source_url`. RSS observations use advisory status and never set verified
keep-forever or RU fields.

- [ ] **Step 4: Run all adapter tests**

Run: `python -m unittest -v test_hunter_sources.py`

Expected: all tests PASS.

- [ ] **Step 5: Commit community discovery**

```bash
git add scripts/hunter_sources.py test_hunter_sources.py
git commit -m "Add FreeGameFindings Steam feed"
```

---

### Task 4: Canonicalize and Conservatively Merge Observations

**Files:**
- Create: `scripts/hunter_merge.py`
- Create: `test_hunter_merge.py`

**Interfaces:**
- Consumes: normalized observation dictionaries from every Task 1–3 adapter.
- Produces: `exact_identity(observation: dict) -> str | None`
- Produces: `normalized_offer_signature(observation: dict) -> str`
- Produces: `merge_observations(observations: list[dict]) -> list[dict]`
- Produces canonical fields `canonical_id`, `platform`, `sources`,
  `possible_duplicates`, `steam_appid`, `steam_packageid`, and best
  source-derived display/claim fields.

- [ ] **Step 1: Write failing identity and merge tests**

```python
def test_same_steam_app_from_two_sources_becomes_one_offer(self):
    merged = merge.merge_observations([
        observation("gamerpower", "7", steam_appid=123),
        observation("steam_specials", "app:123", steam_appid=123),
    ])
    self.assertEqual(1, len(merged))
    self.assertEqual("steam:app:123", merged[0]["canonical_id"])
    self.assertEqual(
        ["gamerpower", "steam_specials"],
        [source["source"] for source in merged[0]["sources"]],
    )

def test_same_title_game_and_dlc_do_not_merge(self):
    merged = merge.merge_observations([
        observation("gamerpower", "7", title="Foo", type="Game"),
        observation("freegamefindings", "x", title="Foo", type="DLC"),
    ])
    self.assertEqual(2, len(merged))

def test_compatible_offer_with_different_source_urls_merges_by_signature(self):
    merged = merge.merge_observations([
        observation("gamerpower", "7", title="Foo", type="Game",
                    claim_url="https://promo.example/foo"),
        observation("freegamefindings", "x", title="Foo", type="Game",
                    claim_url="https://store.steampowered.com/app/123/Foo/"),
    ])
    self.assertEqual(1, len(merged))
```

Add exact-package, canonical-claim-URL, compatible-title fallback,
incompatible-delivery, conflicting-app-ID, stable ordering, and
`possible_duplicates` tests.

- [ ] **Step 2: Run the merge tests and verify the missing module failure**

Run: `python -m unittest -v test_hunter_merge.py`

Expected: FAIL because `hunter_merge` does not exist.

- [ ] **Step 3: Implement identity priority**

```python
def exact_identity(item):
    if item.get("steam_appid"):
        return f"steam:app:{int(item['steam_appid'])}"
    if item.get("steam_packageid"):
        return f"steam:package:{int(item['steam_packageid'])}"
    claim_url = canonicalize_claim_url(item.get("claim_url"))
    if claim_url:
        return f"url:{sha256(claim_url.encode()).hexdigest()[:20]}"
    return None
```

`normalized_offer_signature()` must include normalized title, product role,
and delivery class. A compatible-title merge may bridge different source URLs,
but it must be refused when both observations expose conflicting app/package
IDs or incompatible product/delivery roles. It must not combine game and DLC or
direct-store and non-Steam in-game rewards solely because titles resemble each
other.

- [ ] **Step 4: Implement provenance-preserving merge**

```python
def merge_observations(observations):
    groups, by_exact, by_signature = [], {}, {}
    for item in observations:
        exact = exact_identity(item)
        signature = normalized_offer_signature(item)
        group = compatible_group(item, exact, signature, by_exact, by_signature)
        if group is None:
            group = new_canonical_offer(exact or provisional_id(item), item)
            groups.append(group)
        merge_source(group, item)
        index_group(group, exact, signature, by_exact, by_signature)
    annotate_possible_duplicates(groups)
    return sorted(groups, key=lambda item: item["canonical_id"])
```

Select canonical claim URL in this order: direct Steam app/package URL, verified
external giveaway URL, source URL. Preserve every source observation under
`sources`; never discard conflicting source fields.

- [ ] **Step 5: Run merge and adapter tests**

Run: `python -m unittest -v test_hunter_merge.py test_hunter_sources.py`

Expected: all tests PASS.

- [ ] **Step 6: Commit canonical merging**

```bash
git add scripts/hunter_merge.py test_hunter_merge.py
git commit -m "Merge duplicate giveaway observations"
```

---

### Task 5: Persist Last-Good Source Batches and Independent Health

**Files:**
- Modify: `scripts/scan_free_giveaways.py`
- Modify: `test_scan_free_giveaways.py`
- Create: `source_snapshots.json` through the scanner
- Create: `source_status.json` through the scanner

**Interfaces:**
- Consumes: three adapter callables and `SourceBatch`.
- Produces: `collect_source_batches(adapters, previous_snapshots, now) -> tuple[list[dict], dict, dict]`
- Produces status values `success`, `partial`, `stale`, and `failed`.

- [ ] **Step 1: Write failing source-isolation tests**

```python
def test_failed_reddit_uses_last_good_batch_without_blocking_steam(self):
    previous = {
        "freegamefindings": {
            "last_success_at": "2026-09-05T04:00:00+00:00",
            "items": [{"observation_id": "freegamefindings:old", "title": "Old"}],
        }
    }
    adapters = [
        lambda: SourceBatch("steam_specials", "Steam", NOW, [
            {"observation_id": "steam_specials:app:123", "title": "Foo"}
        ]),
        lambda: (_ for _ in ()).throw(
            SourceRequestError("freegamefindings", "timeout", "timed out")
        ),
    ]
    items, snapshots, statuses = hunter.collect_source_batches(
        adapters, previous, NOW
    )
    self.assertEqual({"Foo", "Old"}, {item["title"] for item in items})
    self.assertEqual("success", statuses["steam_specials"]["status"])
    self.assertEqual("stale", statuses["freegamefindings"]["status"])
```

Add tests for malformed success not overwriting cache, first-ever failure with
no cache producing `failed`, and all providers failing without erasing
previous canonical output.

- [ ] **Step 2: Run the focused pipeline tests and verify they fail**

Run: `python -m unittest -v test_scan_free_giveaways.SourceBatchPipelineTests`

Expected: FAIL because `collect_source_batches` is undefined.

- [ ] **Step 3: Implement isolated collection and cache updates**

```python
def collect_source_batches(adapters, previous_snapshots, now):
    observations, snapshots, statuses = [], deepcopy(previous_snapshots)
    for adapter in adapters:
        try:
            batch = adapter()
        except SourceRequestError as exc:
            observations.extend(use_cached_source(exc.source, snapshots, statuses, exc))
            continue
        snapshots[batch.source] = {
            "attribution": batch.attribution,
            "last_success_at": now,
            "items": batch.items,
        }
        statuses[batch.source] = {
            "status": "partial" if batch.diagnostics else "success",
            "last_attempt_at": now,
            "last_success_at": now,
            "diagnostics": batch.diagnostics,
            "error": None,
        }
        observations.extend(batch.items)
    return observations, snapshots, statuses
```

If every provider fails and none has cached observations, raise before writing
canonical output. If at least one fresh or cached batch exists, continue.
`run_degraded` is true when any source status is `partial`, `stale`, or
`failed`.

- [ ] **Step 4: Wire the three adapters into `main()`**

```python
adapters = [
    lambda: collect_gamerpower(http_json, now),
    lambda: collect_steam_specials(http_json, now),
    lambda: collect_freegamefindings(http_text, now),
]
observations, snapshots, source_status = collect_source_batches(
    adapters,
    load_json(SOURCE_SNAPSHOTS_FILE, {}),
    now,
)
canonical = merge_observations(observations)
```

Add `http_text()` with the same bounded retry/error classification as
`http_json()`. Keep source names intact in errors.

- [ ] **Step 5: Save caches only after canonical processing succeeds**

Write `source_snapshots.json` and `source_status.json` through
`save_snapshot()`. A parse/merge/serialization exception must leave every
previous file unchanged.

- [ ] **Step 6: Run pipeline and regression tests**

Run: `python -m unittest -v test_scan_free_giveaways.py test_hunter_sources.py test_hunter_merge.py`

Expected: all tests PASS.

- [ ] **Step 7: Commit source isolation**

```bash
git add scripts/scan_free_giveaways.py test_scan_free_giveaways.py
git commit -m "Isolate giveaway source failures"
```

Generated live JSON is deliberately not committed until the controlled live
run in Task 8.

---

### Task 6: Integrate Canonical Offers Without Weakening Ranking

**Files:**
- Modify: `scripts/scan_free_giveaways.py`
- Modify: `test_scan_free_giveaways.py`

**Interfaces:**
- Consumes: canonical offers from `merge_observations()`.
- Produces: `candidate_from_canonical(offer: dict) -> dict`.
- Preserves: existing `resolve_steam_match()`, `inspect_steam_ru()`,
  `apply_dlc_ownership_gate()`, `taste_score()`, and
  `recommendation_band()`.

- [ ] **Step 1: Write failing cross-source ranking regressions**

```python
def test_direct_steam_offer_uses_known_appid_without_title_search(self):
    offer = canonical_offer(steam_appid=123, title="Foo")
    with patch.object(hunter, "steam_search") as search, patch.object(
        hunter, "inspect_steam_ru", return_value=FREE_TO_KEEP_METADATA
    ):
        candidate = hunter.evaluate_offer(offer, EMPTY_OWNERSHIP, TASTE)
    search.assert_not_called()
    self.assertEqual(123, candidate["steam_match"]["appid"])

def test_reddit_only_offer_cannot_become_actionable_without_verification(self):
    offer = canonical_offer(source="freegamefindings", title="Foo")
    candidate = hunter.evaluate_offer(offer, EMPTY_OWNERSHIP, TASTE)
    self.assertIn(candidate["taste"]["band"], {"needs_review", "skip"})
```

Add tests for a GamerPower+Steam duplicate retaining current taste score,
already-owned direct Steam offers, unresolved Reddit keys, and DLC parent gates.

- [ ] **Step 2: Run the focused integration tests and verify they fail**

Run: `python -m unittest -v test_scan_free_giveaways.CanonicalOfferIntegrationTests`

Expected: FAIL because canonical evaluation is not implemented.

- [ ] **Step 3: Extract one-offer evaluation from `main()`**

```python
def evaluate_offer(offer, ownership, taste, source_errors):
    candidate = candidate_from_canonical(offer)
    apply_prefilter(candidate)
    apply_ownership(candidate, ownership)
    resolve_and_verify_steam(candidate, source_errors)
    apply_dlc_gate(candidate, ownership)
    apply_taste(candidate, taste)
    return candidate
```

Extraction must move existing branches with minimal semantic change. Known
`steam_appid` skips title search but still calls `inspect_steam_ru()`.
Advisory-only observations cannot set verification directly.

- [ ] **Step 4: Process canonical offers in the main loop**

Use `evaluate_offer()` for every canonical offer, update history for every
pre-filtered and owned item, and preserve current band sort order.

- [ ] **Step 5: Run the complete Python suite**

Run: `python -m unittest -v test_scan_free_giveaways.py test_hunter_sources.py test_hunter_merge.py test_update_steam_data.py test_workflow_concurrency.py`

Expected: all tests PASS with no change to existing fixture bands.

- [ ] **Step 6: Commit canonical evaluation**

```bash
git add scripts/scan_free_giveaways.py test_scan_free_giveaways.py
git commit -m "Evaluate canonical giveaway offers"
```

---

### Task 7: Migrate History and Publish Schema 0.8.0

**Files:**
- Modify: `scripts/scan_free_giveaways.py`
- Modify: `test_scan_free_giveaways.py`
- Modify: `giveaways.json`
- Modify: `giveaway_matches.json`
- Modify: `giveaway_history.json`
- Create: `source_snapshots.json`
- Create: `source_status.json`

**Interfaces:**
- Produces: `history_key(candidate: dict) -> str`.
- Produces: `migrate_history(history: dict, offers: list[dict]) -> dict`.
- Produces schema version exactly `0.8.0`.

- [ ] **Step 1: Write failing migration and schema tests**

```python
def test_gamerpower_history_moves_to_canonical_id_without_resetting_first_seen(self):
    history = {"items": {
        "gamerpower:7": {
            "title": "Foo",
            "first_seen": "2026-08-01T00:00:00+00:00",
            "last_seen": "2026-09-01T00:00:00+00:00",
            "last_band": "likely_match",
        }
    }}
    offer = canonical_offer(
        canonical_id="steam:app:123",
        sources=[{"source": "gamerpower", "source_id": "7"}],
    )
    migrated = hunter.migrate_history(history, [offer])
    self.assertNotIn("gamerpower:7", migrated["items"])
    self.assertEqual(
        "2026-08-01T00:00:00+00:00",
        migrated["items"]["steam:app:123"]["first_seen"],
    )
```

Add assertions that all public payloads contain `schema_version: "0.8.0"`,
`platform: "steam"`, profile output contains `profile_id: "karai"`, and
source health includes all three provider names.

- [ ] **Step 2: Run migration tests and verify they fail**

Run: `python -m unittest -v test_scan_free_giveaways.HistoryMigrationTests test_scan_free_giveaways.OutputSchemaTests`

Expected: FAIL on missing migration and version 0.7.0.

- [ ] **Step 3: Implement canonical history keys**

```python
def history_key(candidate):
    return candidate["canonical_id"]

def migrate_history(history, offers):
    aliases = legacy_history_aliases(offers)
    migrated = deepcopy(history)
    for legacy_key, canonical_id in aliases.items():
        if legacy_key not in migrated["items"]:
            continue
        migrated["items"][canonical_id] = merge_history_records(
            migrated["items"].get(canonical_id),
            migrated["items"].pop(legacy_key),
        )
    return migrated
```

`merge_history_records()` keeps the earliest `first_seen`, latest
`last_seen`, and canonical record’s current state fields.

- [ ] **Step 4: Publish the 0.8.0 payloads**

```python
giveaways_payload = {
    "schema_version": "0.8.0",
    "platform": "steam",
    "updated_at_utc": now,
    "run_degraded": run_degraded,
    "source_status": source_status,
    "candidate_count": len(canonical),
    "items": canonical,
}

matches_payload = {
    "schema_version": "0.8.0",
    "platform": "steam",
    "profile_id": "karai",
    "region": region,
    "updated_at_utc": now,
    "run_degraded": run_degraded,
    "source_status": source_status,
    "match_count": len(matches),
    "bands": band_counts(matches),
    "items": matches,
}
```

Keep `source_errors` for compatibility while adding structured
`source_status`. Apply the 24-hour heartbeat to all generated snapshots.

- [ ] **Step 5: Run the complete Python suite and syntax checks**

Run: `python -m unittest -v test_scan_free_giveaways.py test_hunter_sources.py test_hunter_merge.py test_update_steam_data.py test_workflow_concurrency.py`

Run: `python -m py_compile scripts/*.py test_*.py`

Expected: both commands exit 0.

- [ ] **Step 6: Commit schema and migration**

```bash
git add scripts/scan_free_giveaways.py test_scan_free_giveaways.py
git commit -m "Migrate giveaway output to schema 0.8"
```

Generated JSON remains unstaged until Task 8 confirms a real scan.

---

### Task 8: Update Automation and Perform Controlled Live Acceptance

**Files:**
- Modify: `.github/workflows/scan-steam-giveaways.yml`
- Modify: `test_workflow_concurrency.py`
- Update from live scan: `giveaways.json`
- Update from live scan: `giveaway_matches.json`
- Update from live scan: `giveaway_history.json`
- Create from live scan: `source_snapshots.json`
- Create from live scan: `source_status.json`

**Interfaces:**
- Consumes: every Phase 1 module and generated output.
- Produces: scheduled three-source scans with atomic repository snapshots.

- [ ] **Step 1: Write a failing workflow coverage test**

```python
def test_scan_workflow_runs_all_hunter_tests_and_commits_source_state(self):
    text = SCAN_WORKFLOW.read_text(encoding="utf-8")
    self.assertIn(
        "python -m unittest -v test_scan_free_giveaways.py "
        "test_hunter_sources.py test_hunter_merge.py",
        text,
    )
    self.assertIn("source_snapshots.json source_status.json", text)
```

- [ ] **Step 2: Run the workflow test and verify it fails**

Run: `python -m unittest -v test_workflow_concurrency.py`

Expected: FAIL because the workflow still runs one test file and stages three
data files.

- [ ] **Step 3: Update the workflow**

```yaml
- name: Run hunter regression tests
  run: >-
    python -m unittest -v
    test_scan_free_giveaways.py
    test_hunter_sources.py
    test_hunter_merge.py
    test_workflow_concurrency.py

- name: Commit giveaway data
  run: |
    git config user.name "steam-hunter-bot"
    git config user.email "steam-hunter-bot@users.noreply.github.com"
    git add giveaways.json giveaway_matches.json giveaway_history.json source_snapshots.json source_status.json
    git diff --cached --quiet || git commit -m "Update Steam giveaway scan"
    git push
```

- [ ] **Step 4: Run all local verification**

Run: `python -m unittest -v`

Run: `python -m py_compile scripts/*.py test_*.py`

Expected: all tests PASS and syntax command exits 0.

- [ ] **Step 5: Commit code and workflow before the live scan**

```bash
git add .github/workflows/scan-steam-giveaways.yml test_workflow_concurrency.py
git commit -m "Run multi-source hunter in Actions"
```

- [ ] **Step 6: Push the verified commits to `main`**

Verify the remote head immediately before the atomic push. If an automated data
commit advanced `main`, rebase the feature commits onto that head and rerun
`python -m unittest -v` before pushing.

- [ ] **Step 7: Request the one physical Phase 1 action**

Ask Karai to run `Scan Steam giveaways` once from GitHub Actions. Do not start
Phase 2 until this run completes.

- [ ] **Step 8: Inspect the live workflow and committed payload**

Verify:

- the workflow completed successfully;
- GamerPower, Steam specials, and FreeGameFindings each show `success` or an
  honest isolated failure;
- no source error produced a false empty result;
- duplicate app/package offers have one canonical card and multiple sources;
- existing history retained earliest `first_seen`;
- no unexpected band changes occurred for existing GamerPower offers; and
- the next no-change run respects the 24-hour heartbeat.

- [ ] **Step 9: Commit only evidence-backed live-data corrections**

If the live run reveals a reproducible parser or contract defect, add a failing
fixture test, implement the minimal correction, rerun the full suite, and commit
that correction separately. Product-tuning differences remain documented for
later evaluation.

---

## Phase 1 Completion Gate

Phase 1 is complete only when:

- every local test and syntax check passes;
- the code and workflow are on `main`;
- one live Actions scan is green;
- all three sources are represented in source status;
- existing ranking behavior has not regressed;
- cross-source duplicates merge conservatively with provenance; and
- generated 0.8.0 files are committed without a false history or alert storm.

After this gate, create a separate implementation plan for the public web
application against the observed 0.8.0 contract. The alerts plan follows only
after the web URL and data-provider behavior are stable.
