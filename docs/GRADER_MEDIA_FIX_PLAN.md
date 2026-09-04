# Grader Media & Transcript Fix Plan

Scope: five observed defects in the **Channel-B rubric judge** that cause it to
under-observe real agent output. All five are confirmed present in the current
code (not hypothetical). Each fix is **additive** — it increases what the judge
can see; none reduces observability.

Confirmed by reading `src/utils/grading.py:420-576`, `src/utils/judge_litellm.py`,
`eval/run_batch.py:1083-1115`, `system_prompts/judge_system.md`,
`src/utils/trajectory/{builder,local_media}.py`, and the pinning tests.

**Implementation status: ALL FIVE SHIPPED to main source + tested.**
Implemented in `src/utils/grading.py` (evidence dispatch `_deliverable_evidence_marker`,
boundary-aware transcript budget `_budget_transcript`, stdlib image dims `_image_dimensions`,
stdlib docx + guarded-pypdf `_extract_text_deliverable`, rubric batching
`_merge_batched_grades` + `_rubric_batch_size` threshold, default 40) and
`eval/run_batch.py` (`_condense_transcript_for_judge` emits `[FINAL ASSISTANT MESSAGE]`
/ `[SUBMIT TOOL OUTPUT]` landmarks). Pinning tests updated/added in
`tests/test_grading_units_deep.py` + `tests/test_run_batch_units.py`. Verified: 215
passed / 1 skipped; real-Bedrock e2e graded a 130-criterion rubric in 4 batches with
0 cap-induced abstentions (was 7-22), cachePoint reused across chunks. The batching
merge follows the Oracle design (recompute numerator from merged `satisfied`,
denominator from the full original rubrics, running-offset id remap, failure-isolation
to synthetic abstains, top-level error only when ALL chunks fail).

**Validation status (all five script-proven; no source edited).** Each fix was
reproduced-then-fixed in throwaway scripts that import the *real* `grading.py`
helpers, run route-faithfully against the judge route the current `.env` selects
(**Bedrock / single-Sonnet council / 1,350,000-char budget / 8192 `max_output`**,
verified live), and were exercised across edge cases. Issues 5 and 1 additionally
made **real Bedrock grade calls** (the two fixes whose behavior depends on the
transport / output cap). Per-issue evidence is recorded under each section. The
OAuth-route transcript budget (300,000 chars) was validated by monkeypatched clamp
rather than a live bridge (no cc-bridge running); `pypdf` is genuinely absent on
the host, so Issue 4's *guarded-degrade* path is what was live-validated. The
active council is **single-Sonnet** (GLM/Kimi ARNs commented out in `.env`), so the
smaller per-member budgets (GLM 175K / Kimi 225K) that the harm sections reason
about were **not** independently exercised — the per-member budgeting logic
(`_member_evidence_budget`) was read, not run for those two families.

Load-bearing constraints every fix must respect:
- **Grading MUST NEVER fail** — degrade, never raise (`judge_litellm.py:8,307`).
- **stdlib-only host extractor posture** — the judge path must **not depend on**
  `python-docx`/`Pillow`/`pandas`/`openpyxl` (some may happen to be present in a
  given `.venv`, but the extractor must not require them); `.xlsx`/`.docx` are
  ZIP+XML, parse with `zipfile`+`xml.etree` (mirrors the testgen `.xlsx` rule).
- **Judge context ceilings** — Bedrock Sonnet `max_output`=8192 is
  Anthropic-published; OAuth route adds a 200K-token **input** ceiling
  (`_DEFAULT_JUDGE_OAUTH_MAX_EVIDENCE=300_000` chars). Added bytes compete for
  the same budget (`grading.py:190-194,575-576`).
- **`test_judge_budget_invariant.py:31-64`** couples `budget` + `max_output` to
  `ctx` per family — any `max_output` raise needs a lock-step `budget` drop.

---

## Issue 5 (do FIRST — it blocks correct grading of everything else)
### Judge sees only a prefix of the transcript: mid-tool-output cut, no marker, no final assistant message

**Analyze existing codebase.**
The trajectory *object* is genuinely never truncated (`builder.py` clean;
`_condense_transcript_for_judge` at `eval/run_batch.py:1083-1115` ignores its
`limit` kwarg). The defect is in the *stringification* step:
`_gather_evidence` (`grading.py:528-576`) concatenates deliverables **first**
then the transcript **last** (572-573), then applies a bare character slice:
```python
blob = "".join(parts)
effective = _JUDGE_MAX_EVIDENCE if budget is None else budget
return blob if effective is None else blob[:effective]   # line 576
```
`blob[:effective]` is not boundary-aware, emits no marker, and — because the
transcript is appended last — always eats the transcript tail first, dropping
the final assistant message. Worst case: if deliverables alone exceed
`effective`, the `----- TRANSCRIPT (condensed) -----` marker itself is sliced
off, `_split_evidence` (387-392) returns `""`, and `_judge_user_prompt` renders
`(no transcript captured)` — the judge grades against nothing with a
normal-looking `overall_score`. This runs once per council member at that
member's budget (`grade_with_rubric:1570-1574`), so GLM/Kimi (175K/225K) lose
more than Sonnet. `judge_system.md:11` points the judge at a
`[FINAL ASSISTANT MESSAGE]` landmark that **no code emits** (repo-wide zero
matches), and `:13/:26` gate `TRUNCATION_AFFECTED` on a `... [truncated]`
marker that the slice never writes.

**Fix approach.**
Replace the joined-blob slice with a **boundary-aware, transcript-isolated**
budget in `_gather_evidence`:
1. Budget deliverables and transcript **separately** so the transcript marker
   can never be sliced away (`_split_evidence` can never silently return `""`).
2. Cut the transcript on `\n` boundaries, **but do not assume one line == one
   message**: `_condense_transcript_for_judge` preserves internal newlines
   (`[toolResult] {txt.strip()}` and multi-paragraph assistant text), so a single
   logical entry can span many physical lines (verified: 3 messages → 9 lines,
   the final assistant message split across several). A naive "keep the last K
   lines" therefore preserves only a *fragment* of the final message.
3. Drop from the **middle**: keep head + an explicit truncation marker + tail,
   and **anchor the preserved tail on the terminal landmark**, not on a
   physical-line count, so the *entire* final turn is kept intact regardless of
   how many physical lines it occupies. `judge_system.md:5,11` names **two**
   terminal landmarks — `[FINAL ASSISTANT MESSAGE]` **or** `[SUBMIT TOOL OUTPUT]`
   (a task can end on a submit-tool action rather than an assistant message).
   Anchor on **whichever terminal landmark is present**; if neither is present,
   fall back to preserving the **last logical entry** whole. Emitting only one
   label (step 4) would silently revert to fragment behavior for submit-ending
   tasks. Marker string (single canonical form): `\n... [truncated N lines] ...\n`.
4. Emit the terminal-landmark label(s) in `_condense_transcript_for_judge`
   (run_batch.py:1094-1114) so the `judge_system.md:5,11` contract stops being
   fictional AND the step-3 tail anchor exists. Emit `[FINAL ASSISTANT MESSAGE]`
   before a terminal assistant turn and `[SUBMIT TOOL OUTPUT]` before a terminal
   submit-tool turn (neither is emitted by any code today — repo-wide zero
   matches), so both anchor paths are real.
5. Fix all 4 callers at once (they funnel through `grade_with_rubric` →
   `_gather_evidence`): `eval/run_batch.py:1551`, `script/regrade.py:174`,
   `script/grade_golden.py:35`, `system_prompts/refine_golden.py:243`.

**What code can be harmed.**
- `_split_evidence` partition contract (marker string must stay byte-identical).
- Per-member evidence parity / the `_priority` deliverable ordering (541-548).
- The stale docstring at `run_batch.py:1085-1090` (claims `_gather_evidence`
  applies no cap — false today; make it true-with-marker).
- `tests/test_grading_units_deep.py:349-373` encodes the current mid-string
  slice + roundtrip — will need updating to the boundary-aware behavior.
- `tests/test_judge_budget_invariant.py` char/token math (unchanged by a
  boundary-aware cut — keep green).

**Side-effect mitigation.**
- New pinning test: transcript at budget=50 keeps the final assistant message +
  emits the truncation marker; and a deliverables-overflow case still yields
  a non-empty transcript section. **Note on tiny budgets:** the transcript header
  `\n----- TRANSCRIPT (condensed) -----\n` is ~36 chars, so at budget=50 the marker
  + final message cannot both fit inside 50 chars. Precedence rule (state
  explicitly in the implementation): **preserving the marker + the full final
  assistant message takes priority over the numeric char budget** — the transcript
  section may exceed a pathologically small budget to keep the final message
  whole. Keep the *realistic* budgets (per-family 175K–1.35M) as the operative
  path; `test_judge_budget_invariant.py`'s `budget + ~5000 scaffold` coupling is
  only threatened at absurd budgets, so gate the overshoot to "final message +
  marker only" (bounded, not the whole transcript).
- `judge_system.md:26` already accepts "an equivalent cut-off" for
  `TRUNCATION_AFFECTED`, so emitting `... [truncated N lines] ...` restores the
  signal **without** editing `judge_system.md`; the `:13` literal `... [truncated]`
  is illustrative, not a byte-exact parser key (the verdict regex does not match
  on it). Do not claim byte-identity with the `:13` literal.
- Update `AGENTS.md` `eval/run_batch.py:957` citations to `:1083`.

**Validation evidence (proven).**
Against the real `grading._gather_evidence`: a 400 KB `report.md` at `budget=401_000`
reproduced the defect exactly — the final assistant message was **dropped**
(`final_msg_present: False`) while the `----- TRANSCRIPT (condensed) -----` marker
survived, i.e. the tail slice ate the final message first. The boundary-aware,
transcript-isolated implementation passed all edge cases: (e1) small transcript
budget keeps the final message + emits `[truncated N lines]`; (e2) deliverables-
overflow still yields a non-empty transcript section (`_split_evidence` never
returns `""`); (e3) no-op when it fits; (e4) ≤2-line transcript doesn't crash;
(e5) the **300,000-char OAuth clamp (monkeypatched)** still preserves the final
message + marker, bounded. Live Bedrock grade with the fixed evidence:
`error=None`, `overall_score=1.0`, 2/2 criteria passed, **0 abstained**, 1 request.

---

## Issue 2
### `.png` deliverables never surfaced to the judge

**Analyze existing codebase.**
`.png/.jpg/.jpeg/.svg` are in **neither** `_DELIVERABLE_EXTS` (433-436) nor
`_BINARY_DELIVERABLE_EXTS` (445-447). So `_looks_like_deliverable` (457),
`_is_text_deliverable` (473), and `_is_binary_deliverable` (481) all return
False, and `_collect_deliverable_files` drops the file — the judge never even
learns the filename. Yet `docker_utils.py:1768-1772` DOES sweep those extensions
into `artifacts/`, so the images are on disk; they are discarded only at the
grading boundary.

**Fix approach.**
1. Add an `_IMAGE_DELIVERABLE_EXTS = {".png",".jpg",".jpeg",".webp",".gif"}`
   set adjacent to 445-447; admit it in `_looks_like_deliverable:457` and in a
   new branch of `_collect_deliverable_files:494-503`.
2. **Minimum-viable, zero-dep:** emit a presence marker plus stdlib-parsed
   image dimensions (PNG IHDR / JPEG SOF header read with `struct` — no
   `Pillow`). This unblocks "did the agent generate an image of size WxH?"
   criteria that currently abstain.
3. **Full (vision) path, guarded:** when the judge model is vision-capable,
   attach the image as a base64 content block (shape already exists in
   `trajectory/local_media.py:109-131`). Bedrock Converse `{"image":{...}}` and
   LiteLLM support this; today both judge transports send flat text only
   (`grading.py:811-815`, `judge_litellm.py:534-537`).

**What code can be harmed.**
- Evidence char budget: a base64 image at ~175K chars can evict text
  deliverables under GLM's budget (competes in `_priority`, 541-548). Vision
  blocks must be budgeted **outside** the text char cap, not concatenated into
  `blob`.
- Both transports assume string `content`; adding image blocks changes the
  message shape — must be gated on model capability to avoid 4xx on text-only
  members (Kimi/GLM).
- `judge_system.md:10` "contents not extractable" contract + the pinned test
  `test_grading_units_deep.py:328-342`.

**Side-effect mitigation.**
- Ship the dimension-marker (no-dep) first; gate the vision-block behind a
  capability check + env flag, default off, so a non-vision member degrades to
  the marker (grading-never-fails).
- Emit image evidence **before** the transcript marker (line 572) so it lands
  in `<output_files>`, and count its bytes against the per-member budget from
  Issue 5's boundary-aware accounting.

**Validation evidence (proven).**
Against the real `grading._collect_deliverable_files`: a `chart.png` was **dropped**
(not in the returned list), and confirmed `.png` is in neither `_DELIVERABLE_EXTS`
nor `_BINARY_DELIVERABLE_EXTS`. The stdlib PNG IHDR reader (`struct`, no `Pillow`)
recovered `640x480`, so the minimum-viable marker
`----- DELIVERABLE: chart.png (image 640x480, presence only) -----` is producible
with zero new deps. (The base64 vision-block path remains capability-gated /
default-off per the fix approach.)

---

## Issue 3
### `.docx` deliverables arrive unreadable (no host-side extractor)

**Analyze existing codebase.**
`.docx` is in `_BINARY_DELIVERABLE_EXTS` (445-447): collected for **presence**
only. `_gather_evidence:555-560` emits
`(binary — present, contents not extractable)` and `continue`s — content never
read. No `python-docx` import exists on the host judge path (the
`docker_utils.py` references are the *agent container* installer, not the
grader). The hook is pre-declared in the comment at `grading.py:441-444`.

**Fix approach.**
- `.docx` is a ZIP of XML. Extract `word/document.xml` with **stdlib**
  `zipfile` + `xml.etree.ElementTree`, concatenate `<w:t>` text nodes — no
  `python-docx`, preserving the stdlib-only posture (same technique testgen
  mandates for `.xlsx`).
- Route it through `_is_text_deliverable` per the pre-declared hook: move
  `.docx` (and `.xlsx` via shared-strings/sheet XML) from presence-only into a
  new `_extract_text_deliverable(path) -> str | None` that returns extracted
  text or `None` (→ fall back to the existing presence marker).

**What code can be harmed.**
- The mojibake-parity hazard the comment at 426-431 warns about: extracted text
  must be size-bounded (respect `_ROOT_SCAN_MAX_FILE_BYTES` and the per-member
  budget) so a large `.docx` doesn't bury `report.md` for GLM/Kimi.
- `_priority` ordering (541-548) and the "contents not extractable" test
  (`test_grading_units_deep.py:328-342`) + `judge_system.md:10` wording.

**Side-effect mitigation.**
- Wrap extraction in `try/except (BadZipFile, ParseError, Exception)` →
  return `None` → existing presence marker. Never raises (grading-never-fails).
- Cap extracted chars; if truncated, reuse Issue-5's boundary marker.
- Add a fixture-`.docx` test asserting text extraction + a corrupt-`.docx`
  test asserting graceful degrade to presence marker.

**Validation evidence (proven).**
Against the real `grading._gather_evidence`: a `.docx` produced the presence-only
marker with its body text **absent** from evidence (`contents not extractable`,
`QUARTERLY_REVENUE_4200` not present) — confirming today's behavior. The stdlib
`zipfile` + `xml.etree` extractor reading `word/document.xml` `<w:t>` nodes
recovered `QUARTERLY_REVENUE_4200` (no `python-docx`). Degrade paths returned
`None` (→ existing presence marker) for both a corrupt (non-zip) `.docx` and an
empty-body `.docx` — never raised. (Test note: distinct fixture filenames are
required — an empty-body fixture written to the same path as the good one silently
overwrote it and produced a false negative during validation; the *extractor* was
correct.)

---

## Issue 4
### PDFs are presence-only (correction: NOT "text-only")

**Analyze existing codebase.**
Investigation corrected the original claim: `.pdf` follows the **identical
presence-only** path as `.docx` (`_BINARY_DELIVERABLE_EXTS:445`, marker at
555-560). Neither text nor images are extracted. No `pypdf`/`pdfplumber`/`fitz`
on the host judge path.

**Fix approach.**
- No stdlib PDF text extractor exists, so this is the one defect that needs an
  **optional** dependency. Add a **guarded** import
  (`try: import pypdf … except ImportError:`) mirroring how `judge_litellm.py`
  treats `headroom-ai` (optional, behind try/except, never a hard host pin in
  `requirements.txt`).
- When present: extract per-page text into evidence (bounded). When absent:
  degrade to today's presence marker.
- Image-content criteria: rasterize→vision block is out of scope for the
  stdlib posture; document as a follow-up gated on the Issue-2 vision path.

**What code can be harmed.**
- `requirements.txt` two-tier posture — `pypdf` must be **optional**, never a
  hard pin (a hard pin risks the fresh-box `pip install` posture noted in root
  AGENTS.md).
- Same budget/parity + `judge_system.md:10` + test concerns as Issue 3.

**Side-effect mitigation.**
- Import guard + per-page char cap + `try/except`→presence-marker degrade.
- Document the optional dep in `docs/` and keep the host importless by default;
  extraction only activates if the wheel is available.

**Validation evidence (proven).**
Against the real `grading._gather_evidence`: an `invoice.pdf` produced
`invoice.pdf (binary — present, contents not extractable)` — confirming presence-
only today. `pypdf` is **not installed on the host**, so the guarded import
(`try: import pypdf … except ImportError: return None`) degraded cleanly to the
existing presence marker — which is exactly the default-state behavior the fix
must guarantee. The extraction-present branch cannot be live-proven on this host
without adding the optional wheel; that is the intended trade (optional dep, never
a hard `requirements.txt` pin).

---

## Issue 1 (largest; schedule last)
### Judge output capped at 8192 tokens → tail criteria abstain past ~75 rubric items

**Analyze existing codebase.**
`8192` = `_FAMILY_EVIDENCE["sonnet"][1]` (`grading.py:191`), consumed at
`grading.py:797` (Bedrock converse `maxTokens`), `grading.py:1017` (LiteLLM),
and `judge_litellm.py:559/383`. It is the **Anthropic-published max_output for
Sonnet 4.6** — not a local choice (stamped at `grading.py:176`); Kimi/GLM are
already at their published 16384. `_parse_verdict_text` (929-962) returns
partial verdict lists; missing indices become council **abstentions**
(`matches[:n_criteria]`, 956). The comment at 942-948 already documents the
symptom (renata-voss 69-criterion rubric: GLM=59/Kimi=54/Sonnet=69).

**Fix approach.**
Do **not** raise 8192 on Bedrock (physically capped + coupled to budget by
`test_judge_budget_invariant.py:31-64`). Instead **rubric batching**: split the
rubric into ≤N-criterion chunks, issue one judge call per chunk per member, and
merge verdicts. The partial-verdict aggregator (942-962) already tolerates
index-sparse results, so batching composes with it. Chunk size chosen so
per-chunk verdict output stays well under each family's `max_output`.
(Secondary, optional: on the OAuth/Anthropic-direct route the higher output
ceiling could raise the single-call criterion count — but the 200K input
ceiling still binds, so batching is the robust primary.)

**What code can be harmed.**
- Cost/latency: N chunks = N× judge calls per member. Must cap concurrency and
  reuse the cached system prompt (Sonnet `cachePoint`) across chunks.
- Council merge logic + unanimous-or-Sonnet-tiebreak semantics must be applied
  **per criterion after merge**, not per chunk.
- `test_judge_budget_invariant.py` (evidence budget per chunk must still fit).
- `overall_score` weight denominator must sum across chunks exactly once.

**Side-effect mitigation.**
- Deterministic chunk boundaries (stable rubric order) so re-grades reproduce.
- Merge asserts every criterion index appears exactly once; a missing chunk
  degrades that chunk's criteria to abstain (existing semantics), never crashes.
- Add a test with a >75-criterion rubric asserting zero tail abstentions caused
  by output cap (only genuine content-based abstentions remain).

**Validation evidence (proven, real Bedrock calls).**
A 130-criterion rubric graded in a **single** real Bedrock call reproduced the cap
defect: tail criteria abstained (observed **7–22 abstentions** across runs — the
count varies with per-verdict verbosity, consistent with an 8192 `max_output`
truncating the response mid-list). The same rubric graded via **batching**
(chunk=40 → 4 chunks, each a real `grade_with_rubric` call, verdicts merged)
returned **0 abstained, 130/130 passed**. Merge correctness: the council returns
**positional integer `id`s (0..chunk-1) per call**, so a correct merge must remap
each chunk's local id to a global index (`chunk_start + local_id`) before dedup —
with that remap, `criteria_total == 130` (denominator summed once) and all 130
criteria appear **exactly once**. This confirms batching composes with the existing
partial-verdict aggregator and removes cap-induced (not content-based) abstentions.

---

## Suggested sequencing
1. **Issue 5** (transcript boundary-aware cut + marker) — unblocks trustworthy
   grading of all conversation criteria; smallest, highest-leverage change.
2. **Issue 3** (`.docx` stdlib) + **Issue 2 no-dep image marker** — additive,
   stdlib-safe, low blast radius.
3. **Issue 4** (`.pdf` guarded optional dep) + **Issue 2 vision blocks** —
   capability-gated, default-off.
4. **Issue 1** (rubric batching) — largest; do last with its own test matrix.

## Global regression gate (run after each fix)
- `pytest tests/test_grading_units_deep.py tests/test_judge_budget_invariant.py -q`
- `pytest tests/test_drift_plane_smoke.py -q` (ship gate, 6 passed)
- `lsp_diagnostics` clean on `src/utils/grading.py`, `src/utils/judge_litellm.py`,
  `eval/run_batch.py`.
