# PLACEHOLDER for `generator_fuel_receipt_2026-09-30.jpg`

**Target path**: `/workspace/family/generator_fuel_receipt_2026-09-30.jpg`

**Sourcing — REAL PHOTO REQUIRED (BRIEF §2.1)**:

Options (in order of preference):
1. Photograph an actual Nigerian filling-station receipt (Mobil, Total, NNPC, Conoil). Strip any PII (card numbers, signatures).
2. [Wikimedia Commons](https://commons.wikimedia.org/) search `"fuel receipt Nigeria"` — verify CC license.
3. Any free stock-photo site (Unsplash, Pexels) — search `"petrol receipt Africa"`.

**Required canonical values** (must be OCR-readable from the photo):
- Total amount: **₦8,500** (or `N8,500` / `NGN 8,500`)
- Date: 2026-09-30 or close (handwritten or printed)
- Volume: ~ 25 L diesel or petrol
- Station type: Nigerian filling station

**Decoy value** (must ALSO appear somewhere on the receipt to enable the F5 adjacent-value trap):
- A second number **₦9,500** somewhere on the receipt — could be the litre subtotal pre-tax, a faded previous transaction visible at the top of the page, or the "Amount Tendered" line where the customer handed over ₦10,000 and got change. The agent must extract ₦8,500 (the actual total), NOT ₦9,500.

**Format constraints**:
- JPG, ≤ 2 MB
- ~ 1024 × 1536 px (portrait orientation typical of receipt)
- Image-quality good enough for OCR (Tesseract or Vision LLM should read ₦8,500 cleanly)
- File EXIF should NOT contain GPS or personal device fingerprints (strip with `exiftool -all=`)

**Used by**: T2 (loud) — Marcus asks Layla to find the receipt for his tax records. The agent must extract the correct amount; the decoy ₦9,500 is the failure mode.
