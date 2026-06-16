# Demo Task — Casey Lee Q4 Sweep

**Status: TEMPLATE.** Not runnable as-is. Demonstrates the canonical
WildClawBench task layout; copy this directory to `input/<your_task_id>/`,
rename Casey/the company/dates to your scenario, and replace placeholder
data with real artifacts.

Cross-reference: `docs/task_format.md` (spec) and
`docs/task_validator_prompt.md` (LLM cross-check).

## What's here

3-turn scenario: Casey Lee, a junior analyst at Northway Logistics, runs a
single-day Q4 readiness sweep with one Multi-Agent fan-out in the middle.

- T1 Light — morning briefing, read inbox + project tracker
- T2 Multi-Agent — fan out across inbox, tracker, and shared workspace
- T3 Light — write a consolidated status memo (draft, do not send)

One silent mutation fires between T1 and T2 (project deadline shifts +1
week, agent must detect on T2 re-read).

## Files

- `prompts.txt` — 3-turn Talos format
- `rubric.json` — 5 judge criteria covering all enum variants
- `task.yaml` — metadata (modalities, required_apis, distractors)
- `test_outputs.py` — HTTP-probe-style weighted pytest suite
- `test_weights.json` — positive + negative weights
- `golden_steer_flow.md` — author narrative
- `persona/` — 7 standard MDs flat (Casey Lee)
- `data/` — one demo artifact
- `mock_data/gmail-api/` + `mock_data/airtable-api/` — two API overlays
- `inject/stage0/` + `inject/stage1/` — seed + one silent mutation

## How to validate

```
python3 scripts/check_injection.py docs/task_template
```

Then run the validator prompt at `docs/task_validator_prompt.md` against
the directory listing + key files.
