# Certification Sprint — Project Log

**Project:** Citizen Water-Monitoring Certification Program
**Lead:** Ian Salazar
**Sprint start:** 2026-10-14
**Source of truth for schedule:** Google Calendar (`ian.salazar@voissync.ai`).

---

## 2026-10-14 (Day 1)

### Field
- **07:45–15:30** — Monthly upstream sampling, Sites 1–6 completed.
  Log: `field_log_2026-10-14_sites_1-6.tsv` (sample IDs WQ-2026-013 through WQ-2026-018).
  Nitrate trend 0.8 → 1.9 mg/L upstream→downstream (well below 10 mg/L MCL); turbidity 2.8 → 6.8 NTU; DO 8.3 → 7.4 mg/L. Pattern is textbook ag/wastewater enrichment, no anomalies.

### Schedule changes
- **Sprint kickoff w/ Dr. Olivera (`ev_olivera_kickoff`) — RESCHEDULED.**
  - Original calendar slot: 16:30–17:15 MDT.
  - **New slot: 17:15–18:00 MDT** per Olivera text, 2026-10-14.
  - Action: PATCH submitted to Google Calendar API (HTTP 200). Mock environment did not echo the new time in the response body — verify in the calendar UI before walking over.
- **Framework v1 review window** — Olivera flipped twice today:
  - Voicemail (earlier): Thursday 10:00 MDT, bring field-safety draft.
  - Email (12:48 MDT): Wednesday 14:00 MDT, same conference room. (This was during the upstream run — Ian could not attend.)
  - Pending: confirm whether the kickoff at 17:15 today absorbs the v1 review, or whether a separate review slot still needs to land later this week.

### Documents
- `curriculum_module1_watershed_fundamentals_outline.md` — Module 1 outline drafted (6 contact hours; sub-units 1a geography, 1b hydrology, 1c pollutant pathways, 1d field-safety primer).
- `curriculum_framework_v1_pre.json` vs `_post.json` — **divergence flagged:** Module 4 hours 8 (pre) vs 12 (post), no author/timestamp on the edit. Pending resolution with Olivera.
- `volunteer_coordinator_availability_pre.csv` vs `_post.csv` — **divergence flagged:** Maria Sanchez Thu AM "Yes" (pre) vs "No (conflict)" (post), no author/timestamp. Direct confirmation with Maria pending before booking the curriculum walkthrough.

### Communications drafted (not sent)
- `draft_reply_olivera_framework_v1.eml` — confirms framework v1 by EOD, pushes back on 14:00 Wed slot.
- `draft_text_amara_lunch_reschedule.txt` — apology + reschedule to Friday or next Wednesday (lunch missed).

### Open items / risks
1. **Two silent edits in shared docs today** (Module 4 hours, Maria's Thu AM) with no author/timestamp. Process issue worth raising with Olivera — either the share folder is auto-syncing from a stale source, or someone is editing without leaving a trail.
2. Module 4 hour allocation must be locked before the framework can claim a real total-hours figure.
3. Friday's Olivera curriculum review (calendar event `ev_olivera_review`, Fri 14:00) is still on the books.

---

*Maintained by Ian; updates appended chronologically.*
