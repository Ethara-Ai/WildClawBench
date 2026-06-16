# Feedback to the Audit/Personas Team — "Class F Primary-Key Collisions" (33 MAJORs)

**From:** Environment team
**Re:** The 33 "Class F primary-key collision" MAJORs filed against `environment/*/*_data.py`
**Verdict:** **All 33 are false positives.** We could not reproduce a single genuine integrity violation. They share one root cause, and it is in the auditor's assumptions, not in the data.

Every claim below is backed by a command you can re-run against this repo. Where we could not prove something, we say so explicitly rather than overstate it.

---

## 1. The data is clean under any defensible key — verified, not asserted

For every flagged table we measured three things:

- **col0 dups** — repeated values in the *first* column (what a single-column-PK auditor would count)
- **composite-key dups** — repeated values under the table's *real* composite key
- **exact-row dups** — fully identical rows (the only unambiguous sign of corrupt seed data)

| Table | rows | col0 dups | composite-key dups | exact-row dups |
|---|--:|--:|--:|--:|
| google-classroom/students | 91 | 85 | 0 | 0 |
| google-classroom/topics | 30 | 24 | 0 | 0 |
| pinterest/pin_analytics | 30 | 24 | 0 | 0 |
| slack/channel_members | 25 | 19 | 0 | 0 |
| binance/depth | 16 | 14 | 0 | 0 |
| typeform/answers | 21 | 14 | 0 | 0
 |
| ga/events | 16 | 12 | 0 | 0 |
| tmdb/credits | 17 | 12 | 0 | 0 |
| instacart/order_items | 15 | 12 | 2¹ | 0 |
| okta/group_memberships | 13 | 9 | 0 | 0 |
| binance/klines | 11 | 9 | 0 | 0 |
| spotify/playlist_tracks | 11 | 7 | 0 | 0 |
| strava/kudoers | 12 | 7 | 0 | 0 |
| zillow/price_history | 16 | 7 | 0 | 0 |
| twitter/follows | 12 | 6 | 0 | 0 |
| discord/members | 8 | 6 | 0 | 0 |
| telegram/chat_members | 9 | 6 | 0 | 0 |
| mailgun/list_members | 7 | 5 | 0 | 0 |
| calendly/availability | 5 | 4 | 0 | 0 |
| shippo/tracking | 7 | 4 | 0 | 0 |
| doordash/order_items | 5 | 3 | 0 | 0 |
| ups/rates | 8 | 3 | 0 | 0 |
| okta/app_assignments | 6 | 2 | 0 | 0 |
| fedex/rates | 8 | 2 | 0 | 0 |
| ...remaining flagged tables | | | 0 | 0 |
| **TOTAL across all 33** | | (hundreds) | **0²** | **0** |

¹ instacart/order_items: the two "composite dups" under `(order_id, product_id)` are **two distinct line items for the same product at different prices** (an original line plus a price-adjusted/replacement line — see the `replacement_for` column). The correct grain is the order *line*, not the product; under the line grain there are zero duplicates.

² Zero composite-key violations and zero exact-duplicate rows across all 33 tables.

The pattern is unmistakable: the reported "collision" count tracks **col0 dups** — exactly the artifact you get when you treat column 0 as a unique primary key on a table whose real key is composite.

---

## 2. The root cause: column 0 is not the primary key on these tables

Every flagged table is a **junction, line-item, event-log, or time-series** relation. Its key is composite by design, and the leading column repeats on purpose:

- `slack/channel_members (channel_id, user_id)` — one channel has many members
- `twitter/follows (follower_id, following_id)` — one user follows many
- `zillow/price_history (zpid, event_date, event)` — one property has many events over time
- `instacart/order_items (order_id, …)` — one order has many lines

"`channel_id` repeats in `channel_members`" is not a defect any more than "the same customer appears on many invoices" is. It is the schema working as designed.

---

## 3. Nothing breaks at runtime — checked, not assumed

A single-column key duplicate would only cause real harm if the server *indexed* a flagged table by that single column and silently dropped rows. We searched every flagged service for single-column dict-keying over these tables. Three hits exist, and all three are benign:

- `amplitude_data.py` keys by `date` **within a single `event_type` group**, where `(event_type, date)` is unique — no row lost.
- `tmdb_data.py` keys `_people_by_id` and `genre_lookup` over the **people** and **genres** dimension tables (genuinely unique `id`s) — *not* over the flagged `credits` table.

No server keys a flagged junction/line-item/time-series table by a single column. No lookup collides; no write is dropped. None of these 33 changes a single API response.

---

## 4. The environment was never specified to have these primary keys

The report states the harness "reads `primary_key=…` from the canonical `*_data.py`." Two precise facts:

1. **No `*_data.py` declares a single-column primary key for any flagged table.** They load these relations as plain lists and query them on the full composite key, e.g. `twitter_data.py`:
   ```python
   if not any(l["user_id"] == user_id and l["tweet_id"] == tweet_id for l in _likes_store):
   ```
   The only `PRIMARY KEY` declarations in the repo are in `src/utils/store.py` — the harness's **own SQLite task/score store** (`task`, `score` tables) — which has nothing to do with API seed data.

2. **The environment's own validation contract has no row-level PK concept.** The committed test suite defines what "uniqueness" and "integrity" mean here:
   - `tests/mocks/test_uniqueness.py` checks uniqueness of **service ports, service names, and env-var names** — not data rows.
   - `tests/mocks/test_data_integrity.py` checks **CSV column-count vs header, JSON parseability, and tracking-middleware presence** — not data-row keys.

   The data passes this contract. The "primary-key collision" rule exists nowhere in the environment it was filed against.

If a per-table PK constraint is defined in a grader/rubric source outside this repo, **cite it** — and note that even then, the data satisfies it under the correct composite key (§1).

---

## 5. Why a 100% false-positive batch matters

- It forced the environment team to disprove a non-issue across 25 services.
- 33 phantom MAJORs bury whatever real findings the audit contains, inverting its signal-to-noise.
- It erodes trust in the audit: the next batch gets read assuming a third is noise — corrosive to a process whose only value is being trustworthy.

---

## 6. What must change before this class of finding is filed again

1. **Don't assume column 0 is the primary key.** It is wrong for junction, line-item, event-log, and time-series tables — i.e. most relational data.
2. **Test for composite keys.** If the full row is unique but column 0 is not, the table has a composite key and there is no collision. It is a three-line check (§1). Run it.
3. **Only `exact-row dups > 0` is an unambiguous integrity finding.** Nothing else in this batch qualified.
4. **Confirm the constraint exists before assigning severity.** Cite the file and line that defines the PK you're enforcing. If it isn't in the environment under audit, it isn't a defect in the environment.
5. **Tie severity to runtime impact.** A MAJOR should break observable behavior. None of these alter any API response.

---

## 7. The bar for the next batch

Before any "primary-key collision" is filed, show one of:
- an **exact duplicate row**, with file and line numbers; or
- a duplicate under the table's **actual composite key**, with file and line numbers; or
- a **specific code path** (file:line) that indexes the table by the colliding column and drops rows.

If you can produce none of these, it is not a collision — it is a junction table. We will act on a real integrity bug the moment one is shown with that evidence. This batch contained none.
