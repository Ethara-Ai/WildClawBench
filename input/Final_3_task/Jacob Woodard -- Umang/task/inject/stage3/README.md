# Stage 3 — Overnight Thu→Fri (Between T37 and T38)

> Applied between the last Thursday turn (T37, ~17:00) and the first Friday
> turn (T38, ~08:00). Mixed mutation types: three **stale-data persistence**
> traps (SM9-n, SM9-ln, SM9-at) where NO mutation occurs but old data was
> never corrected, plus one **active filesystem mutation** (SM10) where
> Mitchell changes the Gniezno attribution in the shared Dropbox draft.

---

## Mutations

| Mut ID | Service | Type | What Changed | Key Detail | Detection |
|--------|---------|------|-------------|------------|-----------|
| SM9-n | notion-api | SILENT (stale) | Nothing — data never updated | Delivery still shows Oct 26 | Agent audits project records |
| SM9-ln | linear-api | SILENT (stale) | Nothing — data never updated | Parts arrival still shows Oct 12-13 | Agent audits issue tracker |
| SM9-at | airtable-api | SILENT (stale) | Nothing — data never updated | ETA still shows Oct 12, qa_window still 5 | Agent reviews shipping tracker |
| SM10 | filesystem | SILENT | Gniezno attribution | **1798 → 1802** in Dropbox draft | Agent reviews monograph at T47 |

### SM9-n — Notion stale delivery date

- **Record**: Krasicki Estate Clock project page
- **Field**: `Delivery` still shows `2026-10-26`
- **Correct value**: `~2026-11-10` (per T27 Pforzheim pivot email)
- **Trap**: This was **never a mutation** — the data was seeded at T0 and simply never updated. The agent should have updated it after T27 but if it did not, this stale data persists into Friday.
- **Checker**: `SM_C9` — verifies agent eventually updates or flags stale delivery date

### SM9-ln — Linear stale parts arrival

- **Record**: `Krasicki — Parts QA tracking` issue
- **Field**: Parts arrival ETA still shows `2026-10-12`
- **Correct value**: Shipping `2026-10-28`, delivery `~2026-11-10` (per T27 email)
- **Trap**: Same as SM9-n — never updated after T27 Pforzheim pivot
- **Checker**: `SM_C9`

### SM9-at — Airtable stale shipping tracker

- **Record**: `SM-KR-2026-042` in `Shipping-Tracker` table
- **Fields**: `qa_window` still shows `5 days`, `ETA` still shows `2026-10-12`
- **Correct values**: `qa_window=7 days` (per SM2), `ETA=~2026-11-10` (per T27)
- **Trap**: Double staleness — both the SM2 qa_window change AND the T27 timeline change were never reflected in the shipping tracker
- **Checker**: `SM_C9`

### SM10 — Gniezno attribution change (three-way discrepancy)

- **File**: `/workspace/monograph/ch4_sept_draft.md`
- **Field**: Gniezno longcase attribution changed from **1798** to **1802**
- **Changed by**: Prof. Mitchell (via shared Dropbox access)
- **Schuler attribution**: Unchanged — still "Berlin workshop"
- **Three-way discrepancy after SM10**:

| Source | Schuler 1923 | Gniezno |
|--------|-------------|---------|
| Dropbox (`ch4_sept_draft.md`) | Berlin | **1802** (changed) |
| Obsidian (`ch4_oct_working.md`) | Dresden | 1798 (unchanged) |
| Confluence (Jagiellonian page) | Berlin | 1800 (seed) |

- **Impact**: Agent must detect that Mitchell changed the date unilaterally. Three different values across three sources. Agent should flag and request resolution with Mitchell.
- **Checker**: `SM_C10`, `CROSS_C8`

## Filesystem Artifacts Dropped

| ID | Timing | Path | Action |
|----|--------|------|--------|
| F-T47 | T47 (Fri 15:00) | `/workspace/monograph/ch4_sept_draft.md` | Overwrite with Gniezno=1802 |

## Cross-References

- SM9 traps relate to: T27 (Pforzheim pivot email), SM2 (stage1 qa_window change), M0-N1/M0-LN1/M0-AT3 (original seeds)
- SM10 relates to: A3 (`ch4_sept_draft.md`), A4 (`ch4_oct_working.md`), M0-CF1 (Confluence seed), M0-OB1 (Obsidian seed)
- SM9 is a **detection trap** — no data was mutated; the failure is that stale data was never corrected
- SM10 is an **active mutation** — Mitchell changed the file externally via shared Dropbox
