# Ruth Armstrong — Task-Side Issues

Companion to the harness-only fix list. Every item below is a change inside
`input/Ruth Armstrong/`, **not** in the harness source. Numbering follows the
m1330 search-mode list so the two docs cross-reference cleanly.

> **Scope.** This file enumerates *what the task author should change*.
> Harness bugs (loader fallthrough on Talos `mutations:[list]`, judge council
> all-abstain, env propagation, etc.) live in a separate doc and are referenced
> here only when the workaround sits on the task side.

---

## Quick triage

| Priority | What | Where in task pack | Effort |
|---|---|---|---|
| **High** | Lift per-op `fires_after_turn` to top-level (workaround for stages 1+2 silent fail) | `inject/stage1/STAGE1_INJECT.json`, `inject/stage2/STAGE2_INJECT.json` | 2 lines/file |
| **High** | Drop `test_behavioral_` / `test_negative_weight_` prefixes from test names | `test_outputs.py`, `test_weights.json` | Rename across pair |
| **High** | Clean `test_outputs.py` imports (drop `try/except pytest`, drop unused `subprocess`+`sqlite3`) | `test_outputs.py` | Header edit |
| **High** | Confirm `pass_at_k = 1` for single Claude run | `task.toml` | 1 line |
| **Medium** | `Instructions.md` + `prompts.txt` should carry **all** turn prompts, not just turn 1 | `prompts.txt`, `Instructions.md` | Template fill |
| **Medium** | Confirm `rubric.json` enum coverage for `type`, `evaluation_target`, `importance` | `rubric.json` | Audit pass |
| **Medium** | `TOOLS.md` should list only required + distractor tools (no extras) | `persona/TOOLS.md` | Trim list |
| **Medium** | Clarify the "no distractor skills?" question | task pack at-large | Decision + doc |
| **Low** | `solve.sh` should make the golden-trajectory API calls (defer until golden traj exists) | `solve.sh` | Pending dep |
| **Low** | Provide justifications for failing rubric criteria (for review when judges land) | `rubric.json` rationale fields | Per-criterion |

---

## High priority

### 1. Lift `fires_after_turn` to top-level (stages 1 + 2 silent mutations)

**Symptom on Ruth's last run.** `inject_timeline.jsonl` shows only stage 0's
seed (30 loud ops). Stages 1 and 2 never apply.

**Cause.** The loader (`src/utils/inject_director.py:169–187`) only reads a
top-level `applies_between_turns` / `applied_between` / `fires_after_turn`.
Ruth's Talos export ships the trigger per-op:

```jsonc
// inject/stage1/STAGE1_INJECT.json — current
{
  "stage": "stage1",
  "description": "...",
  "mutations": [
    { "mutation_id": "stage1.gcal.attendance", "fires_after_turn": 5, ... },
    { "mutation_id": "stage1.box.minutes_revised", "fires_after_turn": 5, ... }
  ]
}
```

`raw.get('fires_after_turn')` is `None` at the top level → falls through to
`[None, None]` → treated as seed-only → never fires.

**Workaround (apply now).** Add the trigger at the top level. The harness fix
**L** will eventually accept per-op too, but until then:

```jsonc
// inject/stage1/STAGE1_INJECT.json — fixed
{
  "stage": "stage1",
  "description": "...",
  "fires_after_turn": 5,            // ← add this
  "mutations": [ ... unchanged ... ]
}
```

Do the same on `inject/stage2/STAGE2_INJECT.json` with `fires_after_turn: 11`
(matching the existing per-op value there).

**How to verify.** Run `python scripts/check_injection.py "input/Ruth Armstrong"`.
Output should list stage 1 firing after turn 5 and stage 2 firing after turn 11,
each with the silent ops you'd expect.

---

### 2. Drop `test_behavioral_` and `test_negative_weight_` prefixes

`test_weights.json` (and the matching test function names in `test_outputs.py`)
currently look like:

```jsonc
{
  "test_behavioral_twilio_queried": 1,
  "test_negative_weight_gmail_forbidden_send_detected": -5,
  "test_behavioral_box_minutes_read": 1
}
```

Rename both sides so the prefixes disappear:

```jsonc
{
  "test_twilio_queried": 1,
  "test_gmail_forbidden_send_detected": -5,
  "test_box_minutes_read": 1
}
```

Then update the same names in `test_outputs.py` so pytest still resolves them.
Sign of the weight (positive vs negative) already encodes whether a test is a
gate or a guardrail — the prefix is redundant noise.

---

### 3. Clean `test_outputs.py` imports

Current header:

```python
import json, os, subprocess, sqlite3
from urllib.request import Request, urlopen

try:
    import pytest
except ImportError:
    pytest = None
```

Issues:
- `subprocess` and `sqlite3` are unused (the file uses live HTTP probes only).
- The `try/except` on `pytest` is unnecessary — the harness runs this file
  through pytest unconditionally.

Replace with:

```python
import json
import os
from urllib.request import Request, urlopen

import pytest
```

---

### 4. `pass_at_k` in `task.toml`

`task.toml` currently sets `pass_at_k = 8`. For a single-Claude run we want
`pass_at_k = 1`. The harness has a parallel fix tracked as **D** (default
plumbing), but the task can pin it explicitly to be safe:

```toml
# task.toml
pass_at_k = 1
```

---

## Medium priority

### 5. `Instructions.md` + `prompts.txt` should carry every turn

Right now both files contain only the turn-1 prompt. The harness reads the
remaining turns from `prompts.txt` via the `--- TURN N (...) ---` header
convention, so all 15 turns must appear with their headers and bodies in
`prompts.txt`. `Instructions.md` should mirror them in a reader-friendly
form (one section per turn).

Header shape the parser expects (`_TURN_RE`):

```
--- TURN 5 (Day 2, 09:30, Multi-Agent) ---

<turn 5 prompt body>
```

The `Multi-Agent` label on a header is what auto-detects which turns get
multi-agent fan-out — leaving it out drops a turn back to single-agent.

---

### 6. `rubric.json` enum sanity pass

Each criterion in `rubric.json` carries `type`, `evaluation_target`, and
`importance`. Observed distributions on Ruth's pack:

| Key | Observed values | Note |
|---|---|---|
| `type` | `task completion`, `factuality and hallucination`, `safety & boundaries`, `instruction following` | Looks healthy |
| `evaluation_target` | only `user_facing_message` (all 32 criteria) | **Suspicious — confirm.** Most tasks also have criteria targeting tool calls or artifacts. |
| `importance` | `critically_important`, `important` | Looks healthy |

Action: cross-reference with the project's rubric enum doc. If
`evaluation_target` should include other values (`tool_call`, `artifact`,
`workspace_state`, etc.), re-tag the criteria that actually need them — that
also unblocks any judge logic that switches on the target type.

---

### 7. `TOOLS.md` should match the active tool set

`persona/TOOLS.md` should advertise only the tools the run actually exposes —
that is, the union of `required_apis` and `distractor_apis` declared in
`task.yaml`. Anything else in the file is either dead text (harmless) or, in
some past tasks, a hallucination magnet for the agent. Trim.

Required from `task.yaml`:
```
gmail, google-calendar, google-drive, notion, confluence, asana,
airtable, microsoft-teams, box, slack, twilio, whatsapp
```

Distractors:
```
salesforce, linkedin, hubspot
```

Anything in `TOOLS.md` outside that union should be removed.

---

### 8. "No distractor skills?" question

The pack defines distractor *APIs* (salesforce/linkedin/hubspot) but no
distractor *skills*. Decide explicitly and document the choice:

- If skills aren't applicable here (Ruth's setup uses live API probes for
  guardrails), say so in `Instructions.md` or `README.md` so reviewers don't
  flag it.
- If they should be added, list which skills would be plausible noise and add
  them under `skills/` with the matching `_meta.json`.

---

## Low priority (or dependent on other work)

### 9. `solve.sh` and the golden trajectory

`solve.sh` should reproduce the golden trajectory's API calls so a graded run
can be replayed offline. **Blocker:** golden trajectory generation isn't wired
yet (the user plans to drive it from a global system prompt — see prior
session note). When that lands, populate `solve.sh` from the produced
trajectory.

For now, leave `solve.sh` with the current placeholder and add a comment that
it will be regenerated once golden trajectories are produced.

---

### 10. Rubric justifications for failed criteria

The latest run scored 0.000 with every rubric criterion returning
`Abstain/Abstain/Abstain`. That's a judge-side failure (tracked separately as
harness **G**), not a rubric defect — but when judges come back, expect some
criteria to legitimately fail.

For each criterion that fails, the task author should write a one-line
*expected failure mode* in the rubric (in the `rationale` or a sibling field).
This both:

1. Makes review faster — the reviewer sees what the criterion is sensitive to.
2. Catches stale criteria where the rubric drifted from the task narrative.

Defer this until at least one real judge pass produces non-abstain verdicts,
so you're writing rationales against actual votes, not hypotheticals.

---

## Sanity checklist before next run

After the high-priority items land, this checklist should all be true before
the next launch:

- [ ] `python scripts/check_injection.py "input/Ruth Armstrong"` shows stages 1
      and 2 firing at the expected turn boundaries.
- [ ] `pytest --collect-only test_outputs.py` resolves every test in
      `test_weights.json` (no prefix mismatch).
- [ ] `test_outputs.py` imports only `json`, `os`, `urllib.request`, `pytest`.
- [ ] `task.toml` has `pass_at_k = 1`.
- [ ] `prompts.txt` contains all 15 turn headers.
- [ ] `persona/TOOLS.md` lists only the 12 required + 3 distractor APIs.

Anything in the medium / low section can wait for the next iteration.
