# Stage 0 — Day 1 Seed (Before T0)

> Injected before the scenario begins. Establishes baseline state across all services.
> Indira is starting a 24-hour on-shift day at Station 47 (Oct 20, 2026 — Tuesday).

---

## Filesystem

| File | Target Path | Description |
|------|------------|-------------|
| dat4845.tsv | /artifacts/dat4845.tsv | Lt exam practice scores — 7 sets (Aug 15 – Oct 17), scores 78–82% |
| dat7012.tsv | /artifacts/dat7012.tsv | Shift schedule Oct–Nov 2026, 24-on/48-off Group 2 |
| dat3841.tsv | /artifacts/dat3841.tsv | Station meal kitty tracker (distractor) |
| doc5962.pdf | /artifacts/doc5962.pdf | Study Guide v1 — NFPA 2020 edition |

## Google Sheets

| Sheet Name | Source | Content |
|-----------|--------|---------|
| Lt Exam Practice Scores | dat4845.tsv | 7 practice set rows: Set 1 (78%), Set 2 (78%), Set 3 (79%), Set 4 (80%), Set 5 (80%), Set 6 (82%), Set 7 (82%). Columns: date, exam_set, score_pct, target_pct, weakest_section, study_group_attended, notes |
| CE Hours Tracker | Created | Baseline 18 hours completed. Rows: Basic Hazmat (4hr, completed Aug 2026), EMS Basics (4hr, completed Sep 2026), Fire Behavior (4hr, completed Sep 2026), ACLS Refresher (8hr, status: confirmed Oct 2026), EMS Refresher (4hr, status: completed Oct 2026). Target: 40hr by Dec 31. Remaining: 22hr. Note: Rope Rescue (4hr, pending — scheduled Feb 2026, carryover question). |
| Shift Schedule Oct-Nov | dat7012.tsv | 13-row shift rotation. Key: Oct 20 ON, Oct 22 OFF, Oct 24 ON (inspection), Oct 27 OFF, Nov 14 OFF (exam day) |
| Meal Kitty | dat3841.tsv | Distractor — 12 rows, $15/person meal contributions |

## Google Drive

| File | MIME Type | Folder | Description |
|------|----------|--------|-------------|
| MCFR Lt Exam Study Guide v1.pdf | application/pdf | /Study Materials/ | doc5962.pdf uploaded — NFPA 2020 edition, Q12=C (wrong), Q28=B (wrong) |

## Google Calendar

| Event | Date/Time | Recurrence | Location | Notes |
|-------|----------|------------|----------|-------|
| Lt Exam Study Group | Sat 1:00–3:00 PM CT | Weekly Oct 24 – Nov 9 | Station 47 kitchen | Recurring — members: Indira, Eddie, Martinez, Pham |
| LT EXAM — Nov 14 | Nov 14, all day | None | MCFR Training Center, 3510 S Michigan Ave | High-priority, exam day |
| CPR/ACLS Recertification | Nov 20, 09:00–17:00 | None | MCFR Training Center | Full-day recert |
| Adult Hockey League | Wed 9:15 PM | Weekly | Lakeview Ice Arena | Recurring league game |
| U-6 Coaching | Sat 8:00–9:30 AM | Weekly | Jefferson Park field | Coaching Liam's team |
| Brennan Study Session | Tue/Thu 07:00–08:00 | Weekly Oct 21 – Nov 12 | Station 47 | Added per Brennan's offer (confirmed T1) |

## Gmail

No emails seeded at stage 0. Inbox is empty at scenario start.

## Google Contacts

Pre-existing contacts (from MEMORY.md):
| Name | Email | Phone | Relationship |
|------|-------|-------|-------------|
| Kyle Hudson | kyle.hudson.rn@example.org | (312) 555-0102 | Husband |
| Diane Hudson | diane.hudson@example.org | (630) 555-0145 | Mother |
| Sean Hudson | sean.hudson.ibew@example.org | (312) 555-0178 | Brother |
| Lt. Mike Brennan | mbrennan.cfd@example.org | (312) 555-0201 | Mentor, MCFR |
| Eddie Vasquez | eddie.v.cfd@example.org | (312) 555-0215 | Partner, Engine 81 |
| J. Martinez | j.martinez.cfd@example.org | (312) 555-0230 | Study group member |
| R. Pham | r.pham.cfd@example.org | (312) 555-0244 | Study group member |
| Coach Pete Andersen | coach.pete@lakeviewhockey.org | (773) 555-0310 | Hockey coordinator |
| Maria Gonzalez | maria.g.nanny@example.org | (312) 555-0267 | Nanny |
| Dr. Amanda Torres | — | (773) 555-0290 | Pediatrician |
