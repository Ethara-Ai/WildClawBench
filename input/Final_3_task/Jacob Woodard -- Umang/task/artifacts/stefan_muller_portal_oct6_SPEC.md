# PDF Content Spec — `stefan_muller_portal_oct6.pdf`

> **Artifact ID**: A2 (F0-10)
> **Purpose**: Stefan Müller supplier portal status export for Krasicki parts order
> **Format**: PDF, 2–3 pages, styled as a Swiss precision-parts supplier portal export
> **Stage**: Seeded at T0; silently mutated at T14 (SM2: qa_window 5→7) and T26 (SM4: qa_status in_progress→on_hold)

---

## Document Layout

The PDF should look like a web portal export — header with company branding, status dashboard fields, parts line-item table, and a footer with export timestamp.

### Page 1 — Header & Status Dashboard

**Company Header:**
```
BIEL PRECISION TIMEPIECES AG
Supplier Portal — Order Status Export
Rue de la Gare 17, 2502 Biel/Bienne, Switzerland
Tel: +41 32 365 xxxx | portal.biel-time.ch
```

**Order Summary Panel:**

| Field | Value (T0 initial) | SM2 value (T14) | SM4 value (T26) |
|-------|-------------------|-----------------|-----------------|
| Order Reference | SM-KR-2026-042 | SM-KR-2026-042 | SM-KR-2026-042 |
| Client | Woodard Horology, Kraków | (unchanged) | (unchanged) |
| Client Contact | jacob.woodard@Finthesiss.ai | (unchanged) | (unchanged) |
| Order Date | 2026-09-15 | (unchanged) | (unchanged) |
| QA Status | **in_progress** | **in_progress** | **on_hold** |
| QA Window | **5 business days** | **7 business days** | **7 business days** |
| Origin Facility | **Biel/Bienne, Switzerland** | (unchanged) | **Biel/Bienne** (STALE — email says Pforzheim) |
| Estimated Ship Date | 2026-10-12 | (unchanged) | (unchanged) |
| Priority | Standard | (unchanged) | (unchanged) |

**CRITICAL for checkers:**
- T0 version: qa_window=5, qa_status=in_progress, origin=Biel
- T14 version (SM2): qa_window=**7** (changed silently), everything else same
- T26 version (SM4): qa_status=**on_hold** (changed silently), origin STILL Biel (contradicts T27 email)

### Page 2 — Parts Line Items

| Line | Part No. | Description | Qty | Unit (EUR) | Total (EUR) | Status |
|------|----------|-------------|-----|-----------|-------------|--------|
| 1 | BP-MS-440 | Mainspring, blue steel, 440mm | 3 | 28.00 | 84.00 | QA — measuring tension |
| 2 | BP-EW-127 | Escapement wheel, brass, 12.7mm | 1 | 65.00 | 65.00 | QA — tooth profile check |
| 3 | BP-CS-018 | Click spring, tempered steel | 2 | 8.50 | 17.00 | Passed |
| 4 | BP-BS-085 | Balance staff, hardened steel, **0.85mm** | 1 | **38.00** | **38.00** | QA — pivot tolerance |
| 5 | BP-BA-220 | Barrel arbor, brass, 22.0mm | 1 | 42.00 | 42.00 | Passed |
| 6 | BP-PB-038 | Pendulum bob, cast iron, 3.8kg | 1 | 55.00 | 55.00 | Passed |
| 7 | BP-SS-160 | Suspension spring, phosphor bronze | 2 | 12.00 | 24.00 | Passed |
| 8 | BP-EP-045 | Escape wheel pivot, hardened steel | 2 | 15.00 | 30.00 | Passed |
| 9 | BP-WK-310 | Winding key, brass, size 3.10 | 1 | 18.00 | 18.00 | Passed |
| 10 | BP-DF-066 | Dial feet, brass, 6.6mm (set of 4) | 1 | 22.00 | 22.00 | Passed |
| 11 | BP-HN-290 | Hands, blued steel, 29.0mm/21.5mm | 1 | 35.00 | 35.00 | Passed |
| 12 | BP-BL-080 | Bell, cast bronze, 80mm | 1 | 48.00 | 48.00 | Passed |

**Subtotal**: EUR 478.00
**FX Rate**: 4.32 PLN/EUR
**PLN Equivalent**: PLN 2,064.96
*Note: remaining budget items (local Polish suppliers) not included in this order*

### Page 3 — QA Notes & Footer

**QA Inspector Notes:**
```
Inspector: M. Gerber
Date of last inspection: 2026-10-06
Notes:
- BP-BS-085 (balance staff): Pivot tolerance measurement in progress.
  Current reading within 0.002mm of spec. Final measurement tomorrow.
- BP-EW-127 (escapement wheel): Minor tooth profile adjustment needed.
  Non-critical — expected pass within 2 business days.
- BP-MS-440 (mainspring): Tension test scheduled for Oct 8.
```

**Export Footer:**
```
Exported: 2026-10-06 08:30:00 UTC
Portal version: 4.2.1
Document ID: EXP-SM-KR-2026-042-001
This document is auto-generated from the Biel Precision Timepieces supplier portal.
For questions, contact orders@biel-time.ch or +41 32 365 xxxx.
```

---

## Three Versions Required

You need to produce **three versions** of this PDF:

1. **T0 version** (stage0 seed): qa_window=5, qa_status=in_progress, origin=Biel
2. **T14 version** (SM2 — stage1): qa_window=**7**, qa_status=in_progress, origin=Biel — **same timestamp**
3. **T26 version** (SM4 — stage2): qa_window=7, qa_status=**on_hold**, origin=Biel (STALE)

The timestamp in the footer must be **identical** across all three versions (2026-10-06 08:30:00 UTC) — this is deliberate to make the silent change harder to detect.

## Source/Template Guidance

- Style after a real Swiss manufacturing portal export (e.g., Swatch Group supplier systems)
- Clean, professional layout with company logo placeholder
- Status fields should look like form fields from a web export
- Parts table should look like a standard ERP/order management line-item view
