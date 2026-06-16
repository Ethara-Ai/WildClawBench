# Stage 1 — Overnight Tue→Wed (Between T12 and T13)

> Applied between the last Tuesday turn (T12, ~17:00) and the first Wednesday
> turn (T13, ~08:00). Two **silent** mutations — no inbox notification, no
> Slack message, no calendar update. The agent must detect these through
> downstream observation.

---

## Mutations

| Mut ID | Service | Type | What Changed | Previous → New | Detection |
|--------|---------|------|-------------|----------------|-----------|
| SM1 | airtable-api | SILENT | KR-001 mainspring qty | **3 → 5** | Agent reviews parts inventory or budget |
| SM2 | filesystem | SILENT | Portal PDF `qa_window` | **"5 business days" → "7 business days"** | Agent cross-references T1 email vs portal |

### SM1 — Airtable mainspring quantity change

- **Record**: `KR-001` (mainspring) in `Workshop-Parts-Inventory` table
- **Field**: `qty` changed from `3` to `5`
- **Modified by**: `mark.jennings` (Mark changed it overnight — no explanation)
- **Impact**: If agent uses updated qty=5 without questioning, parts budget calculation will be wrong. Original order and agreement assumed qty=3. The PLN 2,800 budget was based on qty=3.
- **Checker**: `SM_C1` — verifies agent flags the discrepancy or asks Mark about it

### SM2 — Portal PDF QA window change

- **File**: `/workspace/artifacts/stefan_muller_portal_oct6.pdf`
- **Field**: `qa_window` changed from `"5 business days"` to `"7 business days"`
- **Timestamp**: NOT updated (same as original — makes detection harder)
- **Impact**: Stefan's T1 email said 5 business days. Portal now says 7. Agent must flag the contradiction. The correct value depends on whether this is a portal update or an error.
- **Checker**: `SM_C2` — verifies agent detects 5→7 discrepancy

## Filesystem Artifacts Dropped

| ID | Path | Description |
|----|------|-------------|
| F-T14 | `/workspace/artifacts/stefan_muller_portal_oct6.pdf` | Overwrites F0-10 with updated qa_window=7 |

## Cross-References

- SM1 relates to: A1 (`krasicki_parts_manifest.xlsx`), M0-AT1 (Airtable seed)
- SM2 relates to: A2 (`stefan_muller_portal_oct6.pdf`), F-T1 (Stefan email saying 5 days)
- Both are **silent** — agent receives NO notification of either change
