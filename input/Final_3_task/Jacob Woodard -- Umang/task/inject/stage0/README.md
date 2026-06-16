# Stage 0 — Initial Seed (Before T0)

> Applied before the orchestrator starts the first turn.
> Seeds all workspace files and API baseline state.

---

## Filesystem Seeds (F0-01 through F0-27)

| ID | Workspace Path | Description |
|----|---------------|-------------|
| F0-01..F0-08 | `/workspace/persona/jacob-woodard/*` | 8 persona files (README, SOUL, MEMORY, HEARTBEAT, USER, IDENTITY, AGENTS, TOOLS) |
| F0-09 | `/workspace/artifacts/krasicki_parts_manifest.xlsx` | Parts manifest, 12 Krasicki items + SC + RZ items, total PLN 2,800 |
| F0-10 | `/workspace/artifacts/stefan_muller_portal_oct6.pdf` | Stefan supplier portal export: qa_window=5 days, origin=Biel |
| F0-11 | `/workspace/monograph/ch4_sept_draft.md` | Dropbox monograph chapter 4: Schuler="Berlin", Gniezno=1798 |
| F0-12 | `/workspace/monograph/ch4_oct_working.md` | Obsidian monograph chapter 4: Schuler="Dresden", Gniezno=1798 |
| F0-13 | `/workspace/quickbooks/september_summary.csv` | September expenses (PLN 9,530), revenue (PLN 18,000), 3 supplier invoices |
| F0-14 | `/workspace/training/mark_training_log.md` | Mark's training log, 13 modules, address=Dietla 42/3 (stale) |
| F0-15 | `/workspace/radziwill/condition_report.pdf` | Radziwill balance cock=28.4mm (old filename, renamed at T16) |
| F0-16 | `/workspace/radziwill/provenance_notes.md` | CONFIDENTIAL provenance chain (old filename, renamed at T16) |
| F0-17 | `/workspace/krasicki/agreement_original.pdf` | DocuSign agreement: balance_staff EUR 38, total PLN 2,800, delivery Oct 26 |
| F0-18 | `/workspace/guild/presentation_template.md` | Empty guild presentation outline |
| F0-19 | `/workspace/logs/.gitkeep` | Placeholder for daily logs |
| F0-20 | `/workspace/weekly/.gitkeep` | Placeholder for weekly summary |
| F0-21 | `/workspace/wroclaw/fair_registration.json` | Eventbrite: table=14, train 07:15 from Krakow Glowny |
| F0-22 | `/workspace/inbox/spotify_bach_playlist.txt` | DECOY — Bach playlist |
| F0-23 | `/workspace/inbox/guild_agenda_oct21.eml` | Pre-T0 seed: guild secretary agenda |
| F0-24 | `/workspace/krasicki/krasicki_family_sept18.eml` | Sent email reference for T18 |
| F0-25 | `/workspace/training/mark_timesheet_current.md` | Mark's timesheet, Mon Oct 5: 8h |
| F0-26 | `/workspace/commissions/jankowski_bracket_clock.md` | Completed, pickup Thu Oct 8, PLN 1,200 |
| F0-27 | `/workspace/commissions/mazur_mantel_clock.md` | Completed, pickup Thu Oct 8, PLN 950 |

## API Seeds (M0-*)

| ID | Service | Purpose | Key Values |
|----|---------|---------|------------|
| M0-N1..N6 | notion-api | 6 project pages | Krasicki (PLN 2800, Oct 26 delivery), St. Catherine's (Nov 30 stale), Radziwill (28.2mm stale), Junghans, Jankowski, Mazur |
| M0-AT1 | airtable-api | 15 parts inventory records | KR-004=0.85mm, SC-003=0.86mm, RZ-005=0.84mm |
| M0-AT2 | airtable-api | 6 supplier contacts | Stefan, Hans, Klaus, Fritz, Andrzej, Marek |
| M0-AT3 | airtable-api | Shipping tracker | SM-KR-2026-042, QA in progress, ETA Oct 12 |
| M0-GC1 | google-calendar-api | 14+ calendar events | Weekly recurring + one-off appointments |
| M0-LN1 | linear-api | 6 issues | Barrel arbor date=Oct 1 (WRONG), delivery=Oct 26 (STALE) |
| M0-HB1 | hubspot-api | 11 contacts | Provenance locked on museum contacts |
| M0-BHR1 | bamboohr-api | Mark Jennings record | Address=Dietla (STALE), barrel_arbor=Oct 1 (WRONG) |
| M0-TR1 | trello-api | 4 training cards | Barrel arbor completed=Oct 1 (WRONG) |
| M0-OB1 | obsidian-api | Monograph vault | Schuler=Dresden, Gniezno=1798 |
| M0-CF1 | confluence-api | Jagiellonian page | Schuler=Berlin, Gniezno=1800 (third value!) |
| M0-BX1 | box-api | Museum folders | Radziwill files (old names), Sophie folder (empty) |
| M0-SL1 | slack-api | Channel seed messages | #bench-notes, #admin |
| M0-DS1 | docusign-api | Krasicki agreement | balance_staff EUR 38, total PLN 2,800 |
| M0-QB1 | quickbooks-api | September journal | 12 categories, 3 invoices, net PLN 8,470 |
| M0-GS1 | gusto-api | Mark Sept payroll | 168h × PLN 28 = PLN 4,704 |
| M0-GM1 | gmail-api | Seed sent/received | Krasicki Sept 18 update, guild agenda Sept 29 |

## Stale/Wrong Data Seeds (traps for later detection)

| Location | Field | Seeded Value | Correct Value | Detection Turn |
|----------|-------|-------------|---------------|----------------|
| Notion (M0-N2) | Next_Service | Nov 30 | Per Newman rescheduling | T10 |
| Notion (M0-N3) | Balance_Cock | 28.2mm | 28.4mm (condition report) | T41 |
| Linear (M0-LN1) | barrel_arbor completed | Oct 1 | Sept 30 | T6 |
| BambooHR (M0-BHR1) | address | Dietla 42/3 | Starowi\u015blna 18/7 | T24 |
| BambooHR (M0-BHR1) | barrel_arbor_completed | Oct 1 | Sept 30 | T6 |
| Trello (M0-TR1) | barrel_arbor completed | Oct 1 | Sept 30 | T6 |
| Calendar (M0-GC1) | Krasicki delivery | Oct 26 | ~Nov 10 (after T27) | T38 |
| Confluence (M0-CF1) | Gniezno attribution | 1800 | 1798 or 1802 | T7/T47 |
