# Medication Tracker — Alden Croft

15th-of-the-month refill check. Triggers a Rite Aid walk if any
script is running below two weeks of supply.

## Active scripts (as of last refresh)

| # | Drug | Strength | Schedule | Indication | Supply baseline |
|---|---|---|---|---|---|
| 1 | Allopurinol | 300 mg | Daily, morning | Gout maintenance | 30 days |
| 2 | Amlodipine | 10 mg | Daily, morning | Hypertension | 30 days |
| 3 | Lisinopril | 20 mg | Daily, morning | Hypertension | 30 days |
| 4 | Colchicine | 0.6 mg | PRN (acute gout flare) | Gout flare | Variable - depends on flare frequency |
| 5 | Naproxen | 500 mg | PRN (knee pain) | Knee maintenance | Variable |
| 6 | Vitamin D3 | 2000 IU | Daily, morning | Routine supplementation | 60 days |

## Refill cadence rules

1. Daily meds (1, 2, 3, 6): 30-day refill on the 15th if running below 2 weeks
2. PRN meds (4, 5): NO flat-rate refill assumption. Estimate from ACTUAL recent use.
   - If colchicine was used in a gout flare in the prior month, supply will be lower than the cadence suggests
   - Pull from this month's flare-log column when present
3. Pharmacy: Rite Aid Rockland (primary), CVS (backup if out of stock)

## Recent flare log (informs colchicine PRN estimation)

| Date | Flare type | Colchicine consumed |
|---|---|---|
| 2026-11-28 | Left big toe, ~3 days | ~6 tablets |
| 2026-09-12 | Right ankle, ~2 days | ~4 tablets |
| 2026-07-04 | Left big toe (mild) | ~3 tablets |

## Notes

- This tracker is NOT meant to give medical advice.
- Surface the refill picture on the 15th and present options.
- The 2-week threshold is a guideline, not a hard line.
- For the upcoming Dec 15 check (after the haul-out week task window):
  expected to run a colchicine refill due to the November flare.
