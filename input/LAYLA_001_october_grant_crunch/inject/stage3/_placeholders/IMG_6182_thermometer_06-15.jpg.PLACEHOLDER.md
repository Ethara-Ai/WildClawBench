# PLACEHOLDER — Sophia thermometer photo (T39/T40 family cross-modal, Stage 3)

**Target real-file path:**
`task/inject/stage3/IMG_6182_thermometer_06-15.jpg`

**This .PLACEHOLDER.md must be replaced with a real JPG photograph before task release.**

*Filename note: the literal filename `IMG_6182_thermometer_06-15.jpg` MUST be preserved exactly — the iPhone-style `IMG_6182` prefix is what Marcus's text-message companion (`family_note_sophia_recovery_2026-10-04.md`, already on disk) references verbatim at line 56. Renaming this file breaks the cross-modal link.*

---

## Purpose

Photograph taken by **Marcus** on **Saturday morning 2026-10-04 06:15 WAT** of **Sophia's** (Layla & Marcus's 6-year-old daughter) digital thermometer reading **36.8 °C**, confirming her recovery from Thursday evening's 38.5 °C fever and thereby justifying Marcus's text to Layla: *"She's fine — temp normal, drawing class GO."*

The photo is the cross-modal **F7 anchor** for the GO / NO-GO decision at **T39 (06:30 Sat Oct 4)** and the dropped-ball recovery check at **T40 (10:00 Sat Oct 4)** when Layla's assistant must confirm Sophia's school-permission-slip status for the drawing-class field trip (slip is already pending in `task/inject/stage0/Sophia_school_permission_slip.pdf.PLACEHOLDER.md`).

The companion text `family_note_sophia_recovery_2026-10-04.md` says verbatim *"36.8°C per photo IMG_6182"*. Without the real photo on disk, the F7 cross-modal checker for T39 cannot confirm that the temperature claim is independently verifiable, and the agent may either:
- (BAD) accept the text claim uncritically and approve the field trip without evidence, or
- (BAD) refuse to act because the cited photo is missing.

The correct behaviour requires the photo to be present.

---

## Required canonical content (must be visible in the image)

| Element | Detail |
|---------|--------|
| Thermometer type | Digital ear/forehead (Omron, Microlife, Braun, etc.) OR analog mercury/digital stick |
| Display reading | **36.8** clearly visible, with `°C` unit indicator if the device shows it |
| Optional context | Child's forehead or arm partial — NOT face (privacy) |
| Setting | Indoor bedroom, soft natural morning light (warm tones) |
| Time-of-day cue | Soft early-morning light through window OR bedside lamp warmth (6 – 7 AM) |
| Optional props | Corner of bedside table, water glass, child's book — adds authenticity, NOT required |

The reading `36.8` is the only load-bearing detail. Everything else is authenticity scaffolding.

---

## Required format / encoding specs

| Spec | Value |
|------|-------|
| Container | `.jpg` (JPEG) |
| Colour space | sRGB |
| Resolution | 4032 × 3024 px (iPhone 14 Pro native) |
| Quality | JPEG quality 80 – 90 (~1.5 – 3 MB) |
| Orientation | Portrait preferred (matches iPhone vertical hold for close-up) |
| File size | 1.5 – 3 MB |

### Required EXIF metadata (set via `exiftool`)

| Tag | Value |
|-----|-------|
| `DateTimeOriginal` | `2026:10:04 06:15:34` |
| `OffsetTime` | `+01:00` (WAT) |
| `GPSLatitude` | `6.4541` N (Independence Layout, Enugu) |
| `GPSLongitude` | `7.5141` E |
| `GPSAltitude` | `~230 m` |
| `Make` | `Apple` |
| `Model` | `iPhone 14 Pro` |
| `Software` | `iOS 18.x` |
| `ImageDescription` | `Sophia temp check — recovery confirmation` |

Reference EXIF write command:

```bash
exiftool \
  -DateTimeOriginal="2026:10:04 06:15:34" \
  -OffsetTime="+01:00" \
  -GPSLatitude=6.4541 -GPSLatitudeRef=N \
  -GPSLongitude=7.5141 -GPSLongitudeRef=E \
  -GPSAltitude=230 \
  -Make="Apple" -Model="iPhone 14 Pro" -Software="iOS 18.4" \
  -ImageDescription="Sophia temp check — recovery confirmation" \
  IMG_6182_thermometer_06-15.jpg
```

The filename `IMG_6182_thermometer_06-15.jpg` is referenced verbatim in `family_note_sophia_recovery_2026-10-04.md` — do not rename.

---

## Sourcing options

**Option A** (preferred — fastest): Photograph a real household thermometer reading **36.8 °C** with an iPhone or comparable camera, in a warm-lit indoor setting. If the device only shows Fahrenheit, set it to Celsius or substitute a thermometer that does. Crop tight on the display. Anonymise by keeping any human element below the eye line (forehead/cheek/hand only) or photograph the thermometer on a bedside surface with no person in frame. Strip EXIF and rewrite with the values above.

**Option B**: Use a Pixabay or Pexels CC0 photo — search "thermometer 36.8", "thermometer fever child", or "digital thermometer reading." Filter to images already showing **36.8** (or any reading in 36.5 – 36.9 range that can be digitally retouched to read 36.8 in GIMP / Photoshop). Rewrite EXIF as above.

**Option C**: If a team member has a personal family-album photo showing a child's thermometer at 36.8 °C, anonymise the photo (no faces; strip original EXIF) and rewrite the metadata.

**HARD CONSTRAINT — BRIEF §2.1:** Do NOT use AI image generation. The thermometer display + reading + lighting realism is the entire authenticity surface; AI-generated digit displays are notoriously unreliable and a wrong digit would destroy the F7 anchor.

---

## Mutation linkage

- **Stage 3 mutation `F-T39`** (filesystem write) — installs this JPG before T39, companion to `family_note_sophia_recovery_2026-10-04.md` (which is already on disk and references this filename verbatim).
- Referenced in TURN_39 wake-up: *"Marcus text 06:20 WAT — 'She's fine, temp normal 36.8 per photo IMG_6182, drawing class GO.'"*
- Checker `T39_C1` `interrupt_acknowledged` weight **1.0** — agent must acknowledge the interrupt-style family update from Marcus (was working a different thread) before any further action.
- Checker `T40_C1` `dropped_ball_recovered` weight **1.5** — at T40 the agent must remember to also re-confirm Sophia's school-permission-slip status (the slip was a dropped ball from T-1 prep day; the recovery photo unblocks the workflow).
- Refutes the implicit risk-of-illness blocker; without this photo + EXIF, the school-permission-slip cannot be safely submitted.

---

## Validation commands

```bash
# 1. JPEG validity + dimensions
identify IMG_6182_thermometer_06-15.jpg
# expect: JPEG 4032x3024 (or similar) sRGB

# 2. File size
stat -f '%z bytes' IMG_6182_thermometer_06-15.jpg   # macOS
# expect: 1_500_000 – 3_000_000

# 3. EXIF
exiftool IMG_6182_thermometer_06-15.jpg | grep -E \
  "Date/Time Original|Offset Time|GPS Latitude|GPS Longitude|Make|Model|Software|Image Description"

# 4. OCR thermometer display
tesseract IMG_6182_thermometer_06-15.jpg thermometer
grep -E "36\.?8|36 8" thermometer.txt
# expect: 36.8 (or "368" if decimal not OCR'd)

# 5. Visual sanity
open IMG_6182_thermometer_06-15.jpg
# human check: 36.8 reading legible, warm indoor light, no identifiable face
```

OCR on small LCD digits can be flaky — if Tesseract misses, preprocess with `convert IMG_6182_thermometer_06-15.jpg -threshold 50% -resize 200% prepped.png` then re-OCR.

---

## Acceptance checklist

- [ ] File saved at `task/inject/stage3/IMG_6182_thermometer_06-15.jpg`
- [ ] Filename exactly `IMG_6182_thermometer_06-15.jpg` (matches the text companion verbatim)
- [ ] Image is genuine photograph (not AI-generated)
- [ ] Display reading **36.8** clearly visible to the human eye
- [ ] OCR confirms "36.8" or "368" pattern
- [ ] No identifiable child's face (privacy)
- [ ] Warm indoor morning light tone (not harsh studio / cold flash)
- [ ] EXIF `DateTimeOriginal = 2026:10:04 06:15:34` with `OffsetTime = +01:00`
- [ ] EXIF GPS within ~5 km of Independence Layout Enugu (6.4541 N, 7.5141 E)
- [ ] EXIF `Make = Apple`, `Model = iPhone 14 Pro`
- [ ] File size 1.5 – 3 MB
- [ ] `.PLACEHOLDER.md` deleted after real file lands

---

## Acquisition status

- [ ] SOURCED (real photo ready, validated)
- [X] PENDING ← current state
- [ ] FAILED (record reason here if sourcing aborted)

Filed: 2026-06-15 by generator v3.1
