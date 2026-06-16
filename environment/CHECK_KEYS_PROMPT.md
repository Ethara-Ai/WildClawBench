# Mock-Data Key-Coverage Classifier — Agent Prompt

## Mission

You are given a single WildClawBench task directory. Your job is to inspect `prompt.txt` and the `mock_data/` subtree inside that directory and decide, with evidence, whether the mock data supplies **only foreign-key columns** (so callers can reach related rows but cannot look up the entity by its own primary key) or whether it supplies **both primary-key and foreign-key columns**. Use `environment/SCHEMA.md` as the authoritative definition of each API's tables and primary keys. Do not guess. Every claim you make must be traceable to a header row in a mock data file and an entity declaration in SCHEMA.md.

## Inputs

You receive one parameter:

- `<TASK_DIR>` — absolute path to a task directory, e.g. `/Users/apple/Documents/wildclaw_extra/WildClawBench/input/amanda_hayes_01/`.

Within `<TASK_DIR>` you will read:

1. `<TASK_DIR>/prompt.txt` — A short text file. It comes in one of two shapes:
   - **Narrative shape**: a paragraph of natural language describing what the persona wants done. It may name specific record IDs, file IDs, or table identifiers inline.
   - **API-list shape**: a newline-delimited list of `<name>-api` identifiers, one per line, naming exactly which mock APIs are populated for this task.
   You must handle both shapes. The prompt's role here is contextual only: it tells you which APIs are in scope and what kind of lookups the persona is expected to perform. The hard signal lives in `mock_data/`.

2. `<TASK_DIR>/mock_data/` — A directory containing one subdirectory per mock API used in this task. Each subdirectory is named exactly as the API id used in SCHEMA.md, e.g. `notion-api/`, `linear-api/`, `google-drive-api/`, `slack-api/`, `figma-api/`, `google-calendar-api/`. Inside each per-API directory are the data files:
   - `<table>.csv` — one file per `_store.register(...)` table. The first line is the column header.
   - `<doc>.json` — singleton-document files (one whole JSON object, no row structure) corresponding to `_store.register_document(...)` declarations, e.g. `workspace.json`.

Everything else in `<TASK_DIR>` (`persona/`, `data/`, `rubric.json`, `test_outputs.py`, `QC_Report.md`, `test_weights.json`, etc.) is **out of scope** for this check. Ignore those entirely.

The ground-truth schema lives at:

```
/Users/apple/Documents/wildclaw_extra/WildClawBench/environment/SCHEMA.md
```

Open it and search for `### <api>-api` headings when you need to look up an entity's primary key.

## Ground truth: SCHEMA.md format

Each API has a section that looks like this:

```
### notion-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `notion_data.py`):

- **pages** (pk=`id`)
  - Internal fields (from `_coerce_pages`): `…raw row…`, `archived`, `cover_url`
  - Wire fields (from `_page_obj`): ...
- **databases** (pk=`id`)
  - Internal fields ...
- **blocks** (pk=`id`)
  ...
- **workspace** (singleton, via `_store.register_document`)
  ...
```

Authoritative facts you extract from each section:

- The entity name is the bolded word: `**pages**`, `**databases**`, etc.
- The primary key is the value inside `(pk=` … `)` — for the `pages` entity above, the PK is `id`.
- An entity declared with `(singleton, via _store.register_document)` has **no row-level primary key**; it is a whole-document JSON blob.
- A few entities may be missing `(pk=...)` because the underlying registration omitted it. Treat those as "no detectable PK" and note the gap in your output rather than guessing.

## Classification rules

Follow this procedure step by step. Do not skip steps.

### Step 1 — Enumerate scope

1. List the immediate subdirectories of `<TASK_DIR>/mock_data/`. Each one is a `<name>-api` id.
2. If `<TASK_DIR>/prompt.txt` is in API-list shape, read it and confirm that every API named there is present as a directory under `mock_data/`. Note any mismatch (named in prompt.txt but missing on disk, or vice-versa) under "Caveats", but do not let it stop the classification.
3. If `prompt.txt` is in narrative shape, read it once for context only.

### Step 2 — Match each mock_data file to a SCHEMA.md entity

For each API directory `mock_data/<api>-api/`:

1. Open SCHEMA.md, locate the `### <api>-api` heading, and read its `**Entities**` block. Build a local lookup table:

   | entity name | declared PK | kind (table / singleton) | other entities' PKs in this section |

2. For each data file inside `mock_data/<api>-api/`:
   - **CSV file**: strip the `.csv` suffix. Match the basename against the entity names from the lookup table. Accept the following match rules in order:
     1. Exact match (case-insensitive).
     2. Trailing/leading singular-plural variation (`labels.csv` ↔ `**labels**`, `comment.csv` ↔ `**comments**`).
     3. Documented aliases — e.g. SCHEMA.md may register `channel_members` as `channels_members` or vice-versa; if the names differ by underscore placement only, accept the match and record the alias used.
     - If no entity matches, mark the file `unmatched` and continue to the next file. Do not let unmatched files block classification of the rest.
   - **JSON file**: strip the `.json` suffix and match against singleton entries (`(singleton, via _store.register_document)`) the same way. If the JSON file matches a non-singleton entity instead, treat it as that entity and apply the row rules to its top-level keys.

### Step 3 — Read the header row and detect PK presence

For each matched CSV file:

1. Read line 1 only. Split on commas, respecting quoted fields. Trim whitespace. This is `header_columns`.
2. Look up the entity's declared PK from SCHEMA.md, call it `declared_pk`.
3. **Has PK** is `true` if any of the following holds:
   - `declared_pk` (case-insensitive) appears verbatim in `header_columns`.
   - `declared_pk` is `id` / `ID` / `Id` and any of `id`, `<entity_singular>_id`, `<entity_singular>Id`, `<entity>Id` appears in `header_columns` (Linear-style camelCase variant).
   - `declared_pk` ends in `_id`/`Id` and the bare form (`id`) appears in `header_columns` (e.g. SCHEMA says `pk=ts` for Slack messages; only an exact `ts` column counts).
   When you accept anything other than an exact match, record the alias you used in the per-file evidence.
4. Otherwise, **Has PK** is `false`.

For matched JSON singleton files, **Has PK** is N/A (singletons have no PK). Note it explicitly.

### Step 4 — Detect FK columns

For each matched data file, also identify foreign-key columns. A column `c` in `header_columns` is a foreign key when ANY of the following is true:

1. `c` (case-insensitive) is the declared PK of *another* entity in the same `### <api>-api` SCHEMA.md section. Example: in `linear-api/issues.csv`, the columns `teamId`, `projectId`, `stateId`, `cycleId`, `assigneeId` each match the `id` PK of `teams`/`projects`/`workflow_states`/`cycles`/`users` and so are FKs.
2. `c` ends in one of the following suffixes (case-insensitive) and is not itself the declared PK of the file's own entity:
   - `_id`, `Id`
   - `_gid`, `Gid` (Asana)
   - `_uri` (Calendly)
   - `_key`, `Key` (Figma `file_key`, `component_key`)
   - `_ref`
3. `c` is a documented FK by convention even without a suffix — these are common cases the agent must recognise:
   - `parent_id`, `parent_block_id`, `parent_page_id`, `parent_type`
   - `created_by`, `updated_by`, `author`, `author_id`, `owner`, `owner_email`, `leadId`
   - `channel`, `channel_id`, `user`, `user_id`, `team`, `team_id`, `workspace`, `workspace_id`
   - `assignee`, `assigneeId`, `reporter`, `reporterId`
   - `creator`, `organizer`, `from`, `to`
   When the column matches one of these conventional names AND a same-section entity exists that it plausibly references (e.g. `created_by` → `users`), record it as an FK and name the referenced entity in the evidence.

A column is **not** an FK if it is the file's own declared PK, even when its name matches the heuristics above (e.g. `notion-api/pages.csv` has `id` as PK; do not double-count it as an FK to itself).

For JSON singleton files, scan the top-level keys of the document and apply the same FK rules.

### Step 5 — Classify each file, then the task

Per file, assign one label:

- `both` — Has PK AND ≥ 1 FK column detected.
- `pk_only` — Has PK AND 0 FK columns detected. (Pure root-table style.)
- `fk_only` — No PK AND ≥ 1 FK column detected. (Pure join table, e.g. `slack-api/channel_members.csv` with `channel_id,user_id`; or projection tables like `notion-api/page_properties.csv` with `page_id,property_name,property_type,value`.)
- `neither` — No PK AND no FK columns. Flag for manual review.
- `singleton` — JSON document file; PK does not apply. Record FK references found inside if any.
- `unmatched` — Filename did not match any entity in SCHEMA.md. Excluded from aggregate counts.

Per task folder, decide the overall verdict using this rule:

- **`fk_only`** — Every matched table-style file (excluding `singleton` and `unmatched`) is `fk_only` or `neither`. There is no `pk_only` and no `both` file anywhere in the mock_data tree.
- **`both`** — At least one file is `both`, **or** there is a mix of `pk_only` and `fk_only` files (meaning PKs are supplied for some entities and only FKs for others, but PKs are definitely present somewhere in mock_data).
- **`pk_only`** — Every matched table-style file is `pk_only`. (Rare; mock_data has root rows but no relationships.)
- **`inconclusive`** — Every matched file is `unmatched` or `neither`, or the mock_data tree is empty. State exactly why.

## Output format

Emit a single markdown document with the following sections, in order:

```
## Classification: <fk_only | both | pk_only | inconclusive>

## Per-API findings

### <api>-api
| file | matched entity | declared PK | header columns | has PK? | FK columns detected | classification |
|------|----------------|-------------|----------------|---------|---------------------|----------------|
| pages.csv | pages | id | id, parent_type, parent_id, title, ... | yes (exact) | parent_id → pages/databases, created_by → users | both |
| ...

(one block per API directory present in mock_data/)

## Cross-task summary
- Total files inspected: N
- pk_only: N | fk_only: N | both: N | neither: N | singleton: N | unmatched: N
- Verdict reasoning: <one to three sentences explaining the final label>

## Caveats and unmatched files
- <bullet per item — e.g. "slack-api/channel_members.csv had no matching entity in SCHEMA.md; treated as unmatched but observed columns (channel_id, user_id) are both FKs">
- <bullet per item — e.g. "prompt.txt named plaid-api but mock_data/plaid-api/ is absent">
```

Be exhaustive: every CSV and JSON file in `mock_data/` must appear in exactly one row of the per-API tables, including unmatched and singleton files. The verdict reasoning must reference specific files by path.

## Worked example

Task folder: `/Users/apple/Documents/wildclaw_extra/WildClawBench/input/amanda_hayes_01/`

`prompt.txt` is in API-list shape:

```
google-drive-api
linear-api
notion-api
plaid-api
quickbooks-api
trello-api
```

`mock_data/notion-api/` contains, among others, `pages.csv` and `page_properties.csv`:

- `pages.csv` header: `id,parent_type,parent_id,title,created_time,last_edited_time,created_by,archived,icon,cover_url`
  - SCHEMA.md `### notion-api` → `- **pages** (pk=` `id` `)`.
  - Has PK: **yes** — column `id` matches `declared_pk=id` exactly.
  - FK columns: `parent_id` (matches the conventional `parent_id` heuristic and references `pages`/`databases` in the same section per `parent_type`), `created_by` (conventional FK to `users`).
  - Classification: **both**.

- `page_properties.csv` header: `page_id,property_name,property_type,value`
  - SCHEMA.md `### notion-api` → `- **page_properties**` (PK declared as a composite or absent — record whatever the section says; in this dataset `page_properties` is keyed by a synthetic `_pk` not present in the CSV).
  - Has PK: **no** — `_pk` is not in the header; `page_id` is not the declared PK.
  - FK columns: `page_id` → `pages.id` (entity present in the same section).
  - Classification: **fk_only**.

Because `notion-api/pages.csv` is `both`, the overall task verdict is **`both`**. Mock_data supplies the primary key for `pages` and at the same time supplies FK-only views into pages via `page_properties`.

Counter-example fragment from `/Users/apple/Documents/wildclaw_extra/WildClawBench/input/anita_patel_01/`:

- `mock_data/slack-api/channel_members.csv` header: `channel_id,user_id`
  - SCHEMA.md `### slack-api` may or may not list `channel_members` as an entity. If absent, mark as `unmatched` but record that both columns are FKs (`channel_id` → `channels.id`, `user_id` → `users.id`). The file does not bring `channel_members` rows to PK; it is a pure join table.
- `mock_data/linear-api/issues.csv` header: `id,identifier,number,title,description,priority,estimate,stateId,assigneeId,teamId,projectId,cycleId,labelIds,...`
  - Matches `**issues** (pk=` `id` `)`. Has PK: **yes** (exact). FK columns: `stateId`, `assigneeId`, `teamId`, `projectId`, `cycleId`, `labelIds` (each matches another entity's `id` PK in the same section). Classification: **both**.

The overall verdict for `anita_patel_01` is also **`both`**, since `linear-api/issues.csv` and similar tables supply PKs alongside FKs even though `slack-api/channel_members.csv` is a pure join.

A hypothetical task where the verdict would flip to `fk_only` is one whose `mock_data/` contains *only* projection / join / membership tables: e.g. `notion-api/page_properties.csv`, `slack-api/channel_members.csv`, `linear-api/issue_labels.csv` (header `issue_id,label_id`), and so on — every file lacking the parent entity's own `id`/`gid`/`uri` column.

## Edge cases and caveats

- **`prompt.txt` shape detection.** If line 1 is a `<name>-api` token and the file is short (≤ 30 lines) and consists entirely of such tokens, treat it as API-list shape. Otherwise treat it as narrative shape. Either way, the classification draws from `mock_data/`, not from `prompt.txt`.

- **Missing `(pk=…)` in SCHEMA.md.** A handful of entity bullets may not declare a PK because the underlying `_store.register` omitted `primary_key=`. For such entities, you cannot evaluate Has PK; report the file as `neither` (with a note: "no PK declared in SCHEMA.md") if it also has no FK columns, or `fk_only` if it does. Always surface this in Caveats.

- **`<dynamic>` entities.** SCHEMA.md uses the placeholder `<dynamic>` for entities registered via a loop variable (e.g. salesforce-api `<dynamic>` covering `Account`/`Contact`/`Lead`/`Opportunity`). For such APIs, fall back to the conventional rule: a column named `Id` is the PK for any Salesforce-style sObject CSV. Document the inference in evidence.

- **Singleton JSON files.** Files like `workspace.json` are documents, not row tables. Has PK is N/A. Still scan top-level keys for FK references (e.g. `owner_id`, `created_by`) and record them; they do not change a `fk_only` vs `both` verdict by themselves because singletons cannot supply a row-level PK.

- **Composite or synthetic keys.** SCHEMA.md sometimes declares `pk=_pk` (an internal synthetic primary key) for tables that have no natural PK (e.g. monday-api `groups`/`columns`/`column_values`, mailchimp-api `members`). The CSV will not contain `_pk` because it is generated internally. Treat such files as `fk_only` if they carry FK columns, `neither` otherwise — the absence of `_pk` from the CSV header is itself the signal that no PK is supplied to the agent.

- **CSV quoting and multi-value columns.** Some CSV headers contain columns like `labelIds` whose values are quoted comma-separated lists (e.g. `"label-bug,label-high,label-migration"`). Treat the column itself as a single FK column referencing the corresponding entity (`labels`). Do not let the embedded commas confuse the header split — only the first line matters, and you split on the unquoted commas of the header itself.

- **Alias matches for entity ↔ filename.** Examples you will see in real data:
  - `notion-api/databases.csv` ↔ `**databases**`
  - `linear-api/workflow_states.csv` ↔ `**workflow_states**`
  - `google-calendar-api/event_attendees.csv` ↔ entity may be registered as `event_attendees` or `attendees`; accept the closest plural-aware match and document it.
  - `figma-api/comments.csv` header uses `comment_id` rather than `id`. Match the file to `**comments**` and accept `comment_id` as the declared PK alias (if SCHEMA.md says `pk=comment_id`, exact; if SCHEMA.md says `pk=id`, record this as an aliased PK match).

- **Empty `mock_data/`.** If `mock_data/` is empty or absent, return `inconclusive` and state which file is missing.

- **Multiple matches.** If a filename plausibly matches two entities (very rare), prefer exact-case match, then exact-length, then alphabetical first. Document the choice.

- **Don't extrapolate beyond the data.** This classifier reports what is observable in the header rows and SCHEMA.md, nothing more. Do not infer FK relationships from row contents alone, do not chase referential integrity across files, and do not modify any files.

## Execution checklist (for the agent running this prompt)

1. Read `<TASK_DIR>/prompt.txt`; classify its shape; note any API list.
2. List `<TASK_DIR>/mock_data/*/` to enumerate APIs in scope.
3. For each API, open the matching `### <api>-api` section of `/Users/apple/Documents/wildclaw_extra/WildClawBench/environment/SCHEMA.md` and build the entity → PK lookup.
4. For each data file in that API directory, read line 1 (CSV) or the top-level keys (JSON), apply Steps 3–5, and record a row in the per-API findings table.
5. Aggregate counts and decide the overall verdict per Step 5.
6. Emit the Output Format document. Be specific in every cell; do not write "N/A" without an accompanying caveat.
