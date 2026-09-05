# Steam Hunter Multi-Source Web Design

Date: 2026-09-05
Status: Approved in conversation; pending written-spec review

## Goal

Turn the existing Karai Steam giveaway scanner into the first usable part of an
eventual multi-platform “hunter base”:

1. discover Steam keep-forever offers from three complementary sources;
2. merge and verify them without weakening the existing RU, ownership, DLC, and
   taste safeguards;
3. publish a public, mobile-first web interface for the Karai profile; and
4. expose a stable alert feed that ChatGPT can monitor without scraping the UI.

The first release is single-profile and read-only. Multi-profile testing,
authentication, self-service Steam connection, and non-Steam hunters are
explicit follow-up projects.

## Current State

The repository currently contains:

- one scheduled Steam library updater;
- one scheduled giveaway scanner using GamerPower as its discovery source;
- Steam RU verification, ownership checks, DLC gates, taste scoring, history,
  per-run degradation reporting, retries, and snapshot heartbeat suppression;
- JSON output committed to the repository; and
- no user-facing application.

The existing verified rules remain authoritative. This project expands
discovery and presentation; it does not casually retune recommendation logic.

## Scope

### Included

- GamerPower, direct Steam discovery, and FreeGameFindings Steam RSS;
- source adapters and a shared normalized candidate contract;
- conservative cross-source deduplication with full provenance;
- schema-versioned web and alert outputs;
- a public React + TypeScript web application that works on phone and desktop;
- source-health and data-freshness display;
- automated tests, build verification, responsive visual QA, public deployment,
  and activation of ChatGPT monitoring after the feed is live.

### Excluded

- user accounts, authentication, permissions, or private profiles;
- an in-site profile switcher;
- arbitrary users connecting their own Steam accounts;
- Epic, GOG, or other storefronts;
- automatic claiming or key redemption;
- persistent “claimed”, “ignored”, or preference edits from the browser;
- scraping SteamDB;
- IsThereAnyDeal integration.

## Architecture Options Considered

### 1. Minimal static HTML

Fastest initial delivery and no build system, but stateful components,
responsive filtering, future profiles, and eventual authentication would force
a substantial rewrite.

### 2. Static React application with a data-provider boundary — selected

The first version reads public versioned JSON. UI components depend on a small
data-provider interface rather than GitHub paths directly. A later project can
replace the static provider with authenticated API calls without replacing the
page structure and components.

### 3. Full multi-user service now

This would add a database, server, authentication, secret management, account
isolation, and support burden before the ranking quality has been validated on
more than one profile. It is deliberately deferred.

## Data Flow

1. Each source adapter fetches and validates its own response.
2. The adapter emits normalized discovery records without applying taste rules.
3. The aggregator merges only records that are confidently the same offer.
4. Existing Steam matching, RU verification, ownership checks, DLC gates, and
   taste scoring run on canonical offers.
5. Storage writes schema-validated snapshots, history, source status, web data,
   and alert events.
6. The static web application reads the published web data.
7. ChatGPT monitors the alert feed by stable event ID; it does not parse rendered
   HTML.

## Source Adapters

Every adapter has one responsibility: return validated normalized discovery
records plus a source-run status. It must not write files or call another
adapter.

### GamerPower

Keep the existing API and attribution. Move GamerPower-specific schema
validation and normalization behind the common adapter boundary without
changing current behavior.

### Direct Steam discovery

Use the first-party Steam store search surface for specials that currently
appear free. Treat it only as discovery because it may contain permanent F2P,
free weekends, packages, and stale price presentation. Every result must pass
the existing app/package inspection and keep-forever verification before it can
rank as actionable.

Reference:
https://partner.steamgames.com/doc/marketing/discounts/freetokeep

### FreeGameFindings

Consume the community’s documented Steam-filtered RSS feed. Preserve the Reddit
post as source provenance and prefer a linked Steam or giveaway URL as the
claim URL. RSS claims remain advisory until independently verified; a Reddit
post alone cannot establish RU availability or keep-forever status.

Reference:
https://www.reddit.com/r/FreeGameFindings/wiki/rssfeed/

## Normalized Discovery Contract

Each adapter emits these logical fields:

- source and source-local ID;
- source URL and claim URL;
- source title, normalized title, and description;
- platform (Steam for this project);
- content kind (game, gameplay DLC, cosmetic/promotional DLC, or unknown);
- delivery type (direct Steam, external Steam key, in-game code, or unknown);
- Steam app ID and package ID when present;
- publication and expiry timestamps when present;
- source-native status; and
- raw source metadata required for diagnostics.

Source-native data is preserved under provenance. It is not silently rewritten
to look like another provider’s response.

## Canonical Identity and Deduplication

Deduplication is deliberately conservative.

Identity priority:

1. exact Steam app ID for the offered product;
2. exact Steam package ID;
3. canonicalized claim URL;
4. normalized title plus compatible product role and delivery evidence.

Title similarity alone must not merge conflicting games, DLC, base games, or
unrelated promotional rewards. Uncertain pairs remain separate and receive a
possible-duplicate diagnostic for later review.

A canonical offer retains a list of every source observation. The UI presents
one card with a “seen on” list rather than one card per source.

Legacy GamerPower history IDs are mapped to a canonical offer when the link or
Steam identity is unambiguous. The migration must not generate a false wave of
new alerts.

## Failure and Freshness Semantics

- Every adapter reports success, partial success, or failure independently.
- Failure of one source does not block successful sources.
- A malformed or empty-looking response caused by an error cannot overwrite the
  last known valid source snapshot.
- Canonical output states which sources contributed to the run and which failed.
- The UI shows last successful scan per source and an explicit degraded banner.
- Existing bounded retries and error categories are reused.
- Timestamp-only snapshot changes continue to follow the 24-hour heartbeat.
- A new source can increase coverage without changing the meaning of “no
  actionable matches.”

## Output Contracts

The project publishes schema-versioned machine-readable files:

- canonical active offers;
- Karai-ranked active offers;
- offer history;
- per-source status and freshness;
- web bootstrap data; and
- alert events.

All profile-dependent output includes `profile_id: "karai"`. All
platform-dependent output includes `platform: "steam"`. These fields create
future extension points; they do not enable profiles or other platforms in this
release.

Alert events have stable IDs, event type, creation time, canonical offer ID,
profile ID, band, priority, title, reason, expiry, and claim URL. Events are
emitted for meaningful state transitions, not every scan. Initial schema
migration is seeded without notifying all historical offers as new.

The notification recommendation is:

- immediate: `must_claim`, `likely_match`, and `family_only`;
- visible on the site but silent: `conditional`, `needs_review`, and
  `low_priority`;
- never notified as an offer: `skip`.

The feed may expose source-health events, but ChatGPT reports them only when a
failure persists or compromises all discovery sources.

## Web Application

### Technology and deployment

- React, TypeScript, and a small static build;
- public read-only deployment through Sites, connected to this repository;
- no runtime secrets in the browser;
- a replaceable data-provider module between UI code and published JSON;
- asset and data paths that work at the deployed site root and in preview.

### Information architecture

Primary views:

1. **Claim** — must-claim, likely-match, conditional, family, and low-priority
   offers grouped by urgency and value;
2. **Review** — ambiguous Steam identity, DLC parent, delivery, region, or
   verification cases;
3. **Rejected** — already owned, permanent F2P, free weekend, unavailable, and
   hard taste mismatches with their reasons;
4. **History** — previously observed offers and their last state.

### Offer cards

Each card shows:

- capsule/cover image when a trustworthy Steam image is available;
- title and content type;
- recommendation band and score;
- concise human-readable explanation;
- expiry and urgency;
- delivery method;
- verification and ownership state;
- source provenance; and
- a primary button to the best claim destination.

Details expand in place for technical reasons and source-specific instructions.
The UI never labels an unverified external key as guaranteed.

### Responsive behavior

- Mobile: one-column card feed, compact filter drawer, and bottom navigation.
- Desktop: responsive card grid, persistent filter rail, and fuller status
  summary.
- Shared: readable touch targets, keyboard navigation, visible focus states,
  sufficient contrast, loading skeleton, empty states, and degraded-data state.

### Visual direction

Use a dark “hunter base” dashboard rather than a generic storefront: restrained
graphite surfaces, warm amber for actionable finds, cool cyan for verified data,
and red only for genuine expiry/error danger. Visual theme must not obscure
recommendation labels or verification text.

## ChatGPT Monitoring

Monitoring is activated only after the public alert URL is stable.

The recurring task:

- checks the machine-readable alert feed, not page markup;
- reports only unseen alert event IDs marked for notification;
- includes title, why it fits the active profile, expiry, delivery type, and
  claim link;
- remains silent when nothing new is actionable; and
- mentions persistent total-source failure without sending repeated noise.

The already-authorized recurring monitor is created only after a harmless read
confirms the deployed feed is reachable.

## Testing

### Python/data

- adapter contract tests with stored fixtures for valid, empty, malformed,
  rate-limited, and transient-failure responses;
- deduplication tests for same-app, same-package, same-link, ambiguous-title,
  game-versus-DLC, and multiple-source provenance;
- regression tests for current Steam RU, ownership, DLC, F2P, free-weekend,
  retry, degradation, history, and heartbeat behavior;
- migration test proving existing history does not become a mass new-alert
  event;
- schema validation for every published JSON file.

### Web

- unit tests for band grouping, filtering, urgency, claim-link choice, and
  degraded-source display;
- production build verification;
- fixture-driven empty, normal, degraded, ambiguous, and expired states;
- visual inspection at representative mobile and desktop sizes;
- link and asset-path verification against the deployed base URL.

### Live acceptance

- all automated tests and the production web build pass;
- all three sources complete or disclose their independent failure honestly;
- a deliberately duplicated offer becomes one card with multiple sources;
- current Karai recommendations render on mobile and desktop;
- no legacy offer is emitted as a new notification during migration;
- the deployed alert feed is readable by ChatGPT; and
- one controlled test alert is delivered once, not repeatedly.

## Delivery Phases

### Phase 1: Multi-source engine

Introduce adapter boundaries, add Steam and FreeGameFindings, normalize and
deduplicate, migrate history safely, and produce source status. Preserve current
ranking behavior and validate live output before proceeding.

### Phase 2: Public web

Create the static application, connect it to versioned data, implement responsive
views and states, verify visually, and publish the public site.

### Phase 3: Alerts

Generate the alert event feed, verify migration suppression and stable event
identity, connect ChatGPT monitoring, and run one controlled end-to-end alert.

Each phase must leave the repository usable. A failure in a later phase does not
roll back a verified earlier phase.

## Manual Checkpoints

The implementation proceeds autonomously except when one of these is required:

- a new credential, secret, API key, or account connection;
- a hosting or repository setting unavailable to the implementation tools;
- live confirmation of appearance or behavior that only the user can observe;
- a product choice that materially changes privacy, scope, or notification
  volume.

The public Karai deployment and creation of its ChatGPT monitor are already
authorized by this specification.

## Future Projects

After the Karai release is stable:

1. add controlled profiles for husband, brother, brother-in-law, and brother’s
   girlfriend;
2. measure ownership accuracy, ranking precision, false positives, and false
   negatives across those profiles;
3. add authentication and profile isolation;
4. allow arbitrary users to connect Steam and manage taste settings; and
5. add independent hunters for other storefronts behind the shared platform and
   profile contracts.
