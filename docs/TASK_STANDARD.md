# Task Authoring Standard

Every new task follows this standard. The living exemplar is
**`input/standard_task_template`**, compiled from
**`declarative/standard_task_template/`** — copy that source directory as your
starting point. Format details: `declarative/README.md`; multi-turn mechanics:
`docs/MULTITURN.md`.

## The one rule

**Tasks are authored declaratively and compiled — never hand-written.**
A task is four data files (`metadata.json`, `prompt.txt`, `stages.toml`,
`rubrics.json`) plus passthrough dirs (`persona/`, `data/`, `mock_data/`,
optional `gt/`) under `declarative/<task_id>/`. `assets/` holds files that
filesystem mutations reference — the compiler copies each referenced file
into its stage dir rather than copying `assets/` itself. The compiler emits
the native bundle; the harness never changes.

`input/` is **gitignored**: the declarative source is the only
version-controlled representation of a task. Never hand-edit a compiled copy
in `input/` — edit the source and recompile, then re-promote.

## Workflow

```bash
cp -R declarative/standard_task_template declarative/<task_id>   # start here
# ... edit the four data files + passthrough dirs ...
python3 script/compile_declarative_task.py declarative/<task_id> --force
python3 script/preflight_task.py declarative/_build/<task_id>    # must be 0 fail
cp -R declarative/_build/<task_id>/ input/<task_id>/             # promote
pytest tests/test_drift_plane_smoke.py -q                        # must stay 6 passed
```

## World-state rules

1. **Pre-T0 state lives in overlays, not stages.** API baseline →
   `mock_data/<api>-api/` seed files (string-celled rows matching the
   baseline schema — load-time coercers own the typing); workspace files →
   `data/home/**`. The first `[[stages]]` block is a documentation-only seed
   marker: the harness applies **no** API ops from stage0.
2. **Every mid-run API op carries an explicit `admin` block**
   (`{op: patch|upsert|update_where|doc_set, table, pk, set/row}`). The bare
   REST form is provenance only — fuzzy resolution breaks on domain-keyed
   tables and can never insert rows. Values in `set`/`row` are **typed**
   (ints/bools/arrays) because admin writes bypass load-time coercion.
3. **Silent means silent.** Mutations must edit data in place with no
   tell-tale filenames; the admin plane keeps them out of the agent-visible
   audit feed. Verify every firing in the run's `inject_timeline.jsonl` —
   every op logs a status (`applied` / `unresolved` / `failed` / `no-match` /
   `error`; filesystem ops log `copied` / `skipped` / `missing_src`), patches
   log before/after values, upserts log the inserted pk. Anything other than
   `applied`/`copied` is an authoring bug.

## Grading rules

4. **Weights come from {±5, ±3, ±1}.** +5 = the core outcome; +3 = required
   supporting outcomes/behaviors; +1 = hygiene. Negative = violation
   detectors.
5. **Judge criteria are single, self-contained, binary-judgeable sentences.**
   Negative criteria are phrased as "the forbidden thing happened"
   (SATISFIED:Yes = violation). Cover at least: the core outcome
   (`final_answer`), one `state_change`, one `trajectory` behavior, and one
   negative guardrail.
6. **Deterministic checks use the declarative grammar only**
   (`audit_request`, `api_get`, `file_exists`, `file_valid_json`,
   `file_contains`, `any_of`/`all_of`). A check that needs arithmetic,
   ordering between requests, or timestamp math doesn't fit the grammar —
   redesign the check or grade it via the judge instead.
7. **Anchor your matches.** Bare substrings false-positive: `"750"` matches
   `1750`; a stale value an honest agent may legitimately mention must never
   trigger a negative. Anchor to field names or ids with `regex_any`
   (see `outcome_plan_final_total` / `negative_plan_still_stale_total` in the
   template) and anchor audit paths with end-of-path regexes. Know the
   grammar's limits and document them in the check description when you
   accept one: file matching is structure-blind (a JSON history array can
   re-introduce an anchored field match), and audit conditions match single
   entries — two entries cannot be correlated (e.g. tying a draft-create to
   the same draft's send).
8. **Behavioral checks read the audit feed, outcomes read state/files —
   and every check must grade the agent, not the environment.** A check that
   passes on a freshly-seeded world with no agent action awards free points;
   proving that a mutation landed is `inject_timeline.jsonl`'s job, not a
   weighted check's. `audit_request` proves what the agent *did* (add a
   `status_code` condition when only completed actions should count);
   `api_get` / `file_*` prove what is now *true*. Use `min_count: 2` for
   re-verification behaviors and `result_key: "."` for object-shaped detail
   endpoints.

## Task-design rules

9. **Every task carries a staleness trap.** Some source the agent will see
   early (a workspace note, a first read) must go stale via a silent
   mutation, so re-verification is what's actually being measured.
10. **The prompt never names the graded tokens outright** — the agent must
    derive them from the environment (the template's prompt names the file
    and fields, but the amount only exists in the mail).
11. **Persona is the 7-file OpenClaw set**, coherent with the mock world
    (the mailbox profile, addresses, and company must match the persona).
12. **Name tasks `<persona>_<short_slug>`**, snake_case, matching the corpus
    (`davis_*`, `martinez_*`).

## Definition of done

- `preflight_task.py` on the compiled bundle: **0 fail** (the empty-seed
  warning is expected).
- `load_task` reports the intended turn count and `test_code` provided.
- The smoke gate stays at 6 passed.
- Prove the grading loop offline before any live run: boot the required mock
  with the task overlay, apply the compiled stages through `InjectApplier`,
  simulate a perfect agent, and run the generated `test_outputs.py` — expect
  every positive check to pass and every negative to stay dormant, then
  trigger each negative deliberately and watch it fire (see the session
  pattern in `docs/MULTITURN.md`; a runnable example lived at
  `scratchpad/e2e_sim_template.py`).
- When reading live-run scores: `tests_errored > 0` means the mock stack was
  unreachable during grading (network checks raise instead of failing) — a
  zeroed positive *and* silently-dormant negatives. Re-run the grading; don't
  trust that run's Channel-A numbers.
