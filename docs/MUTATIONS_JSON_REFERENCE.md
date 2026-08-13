# `inject/stageN/mutations.json` — Complete Key & Value Reference

Authoritative, code-derived catalogue of **every key the harness reads** from a
stage's `mutations.json`, the exact shape each key must take, and the full domain
of legal values.

Derived from (read these if you need to go deeper):

| Concern | File |
| --- | --- |
| Stage parsing | `src/utils/inject_director.py` — `InjectScript.load`, `_coerce_mutation_buckets`, `_turn_to_index`, `stage_for_boundary` |
| Op dispatch | `src/utils/inject_director.py` — `_apply_api_mutation`, `_apply_admin_op`, `_replay_admin_rest`, `_resolve_target`, `_apply_as_api`, `_apply_filesystem` |
| Static validation | `src/utils/inject_validator.py` — `validate_inject_script`, `_op_kind`, `_FS_ALLOWED_ACTIONS`, `_resolve_fs_src` |
| Preflight warnings | `script/preflight_task.py` — `_check_fires`, bare-REST warning |
| Wire surface | `environment/admin_plane.py` — endpoint surface + `Table.patch` merge semantics |
| Canonical generator | `script/compile_declarative_task.py` — `emit_inject` (writes this file for you) |

> **Rule zero.** If your task is authored declaratively, **do not hand-write this
> file** — `script/compile_declarative_task.py::emit_inject` generates it and
> guarantees the positional keys (`applies_between_turns`, `fires_at_turn`,
> service slug suffix, `action` defaults). Hand-write only when patching an
> already-compiled task.

---

## 1. File-level shape

```json
{
  "stage": 1,
  "stage_name": "cohasset_courtesy_adjustment_posts",
  "description": "Prose: what drifts, why, and whether the agent can see it.",
  "applies_between_turns": ["T2", "T3"],
  "applied_at_local_time": "2026-10-08T02:15:00-04:00",
  "mutations": {
    "filesystem": [],
    "loud": [],
    "silent": []
  },
  "expected_audit_summary_after_stage": { "square-api": {} }
}
```

### 1.1 Top-level keys

| Key | Type | Required | Read by harness? | Legal values / notes |
| --- | --- | --- | --- | --- |
| `stage` | int | no | **No** — ignored | Documentation only. The real stage index comes from the **directory name** (`stage(\d+)$`), never this key. Keep it equal to the dir number to avoid confusing reviewers. |
| `stage_name` | string | recommended | **Yes** | Any non-empty string. Falls back to the directory name if absent/empty. Used as the label in `inject_timeline.jsonl` and every log line. Convention: `snake_case` verb phrase (`seed_baseline`, `cohasset_courtesy_adjustment_posts`, `out_of_scope_september_adjustment`). |
| `description` | string | recommended | **No** — ignored | Documentation only. Must state whether the drift is agent-visible; reviewers and QC read it. |
| `applies_between_turns` | array of exactly 2 turn tokens | **yes** | **Yes** | See §1.2. Alias accepted: `applied_between`. |
| `applied_at_local_time` | string | recommended | **No** — ignored | ISO 8601 / RFC 3339 with a UTC offset, e.g. `2026-10-08T02:15:00-04:00`. The `-04:00` is a **sign + offset**, not a range. Must be consistent with `prompts.json.timezone` (America/New_York → `-04:00` during DST, `-05:00` otherwise) and must fall **inside the wall-clock gap** between the two turns named in `applies_between_turns`. Empty string `""` is what the compiler emits when unspecified. |
| `mutations` | object **or** array | **yes** | **Yes** | See §2. |
| `expected_audit_summary_after_stage` | object | optional | **No** — ignored | Documentation only. Convention: `{"<service-slug>": {"<METHOD> <path>": <count>}}`. For a `silent` stage it must be `{"<service>": {}}` (or `{}`) because silent ops route through `/admin/*`, which `tracking_middleware` skip-lists — they can never appear in the agent-visible `/audit/*` feed. Do **not** list an injected inbound record here; an injection is not an agent action. |

Any other top-level key is silently ignored.

### 1.2 `applies_between_turns`

Exactly two elements: `[from_turn, to_turn]`. Parsed by `_turn_to_index`, which
accepts:

| Token form | Example | Parses to |
| --- | --- | --- |
| `"T<n>"` | `"T3"` | `3` |
| `"<n>"` (string) | `"3"` | `3` |
| `<n>` (int) | `3` | `3` |
| `null` | `null` | `None` |
| unparseable | `"third"` | `None` |

Semantics:

* **Seed stage** (`stage0`): `from_turn` must be `null` → `[null, "T0"]`.
  `InjectStage.is_seed` is `from_turn is None`. Nothing else marks a stage as seed.
* **Mid-run stage** (`stageN`, N ≥ 1): `["T<n-1>", "T<n>"]`. The stage is applied
  **before** turn `to_turn` while the agent is idle. `stage_for_boundary(i)`
  matches on `to_turn == i` only — `from_turn` is never used for scheduling
  (it is narrative bookkeeping and is what distinguishes seed from non-seed).
* Two non-seed stages sharing the same `to_turn` is an authoring bug: the first
  match in directory order wins and the other never fires.

Wiring: `eval/run_batch.py`'s `_inject_before_turn` hook calls
`stage_for_boundary(turn_index)` then `apply_stage`.

### 1.3 Seed-stage capability limits (critical)

`InjectApplier.seed()` walks **only** `stage.filesystem` and `stage.loud`
(the latter gated behind `replay_loud`, default **False**). It **never** walks
`stage.silent`.

| Bucket in `stage0` | Fires? |
| --- | --- |
| `filesystem` | **Yes** (needs a workspace copy hook; otherwise `skipped`) |
| `loud` | Only when the applier is constructed with `replay_loud=True` (default off, because `mock_data/` overlays already carry pre-T0 state) |
| `silent` | **Never — dead code.** No timeline entry, no state change |

A `silent` op in `stage0` is a hard correctness defect. Put pre-T0 API state in
`mock_data/` overlays instead.

---

## 2. `mutations`

### 2.1 Dict form (canonical)

```json
"mutations": { "filesystem": [ ... ], "loud": [ ... ], "silent": [ ... ] }
```

All three keys are optional; `null` is treated as `[]`. Any other key is ignored.

| Bucket | Applied by | Agent-visible? | Use for |
| --- | --- | --- | --- |
| `silent` | `apply_stage` (all stages ≥ 1) | **No** — routed via `/admin/*`, skip-listed from `/audit/*` | Undetectable-by-audit state drift the agent must catch by **re-reading live data** |
| `loud` | `apply_stage` (stages ≥ 1); `seed` only if `replay_loud=True` | **Yes** — recorded `silent=False` | New rows the agent discovers through normal API reads (an inbound email, a new calendar event) |
| `filesystem` | `seed` and `apply_stage` | Yes (a file appears in the workspace) | Dropping documents/notes into the agent's workspace |

### 2.2 List form (tolerated legacy)

```json
"mutations": [ { "silent": true, "service": "...", "admin": { ... } } ]
```

`_coerce_mutation_buckets` classifies each element, in this precedence order:

1. `op.silent === true` **or** `op.bucket == "silent"` **or** `op.kind == "silent"` → `silent`
2. `"action" in op` **or** `bucket/kind == "filesystem"` → `filesystem`
3. `op.service` or `op.path` present → `loud` (**silent must be opted into**)

Non-dict elements are dropped. Prefer the dict form; list form makes bucket
membership implicit and easy to get wrong.

Any other type for `mutations` (string, number, `null`) yields three empty lists
and logs `inject: stageN mutations had no recognized ops (shape=...)`.

---

## 3. API op envelope (shared by `silent` and `loud`)

Every API op is a flat JSON object. These keys form the **envelope** and are
never treated as row data (`_INJECT_ENVELOPE_KEYS`):

`fires_at_turn`, `raw_eml_path`, `service`, `api`, `method`, `path`, `id`, `admin`

| Key | Type | Required | Legal values |
| --- | --- | --- | --- |
| `id` | string | **yes** (in practice) | Unique within the whole inject script. Appears verbatim in `inject_timeline.jsonl` and is how every failure is traced. Compiler default: `s<stage>-<bucket>-<k>`, e.g. `s1-silent-0`. Hand-authored convention: `sil_<what>_<from>_to_<to>` / `loud_<what>`. Missing → `null` in the timeline, effectively untraceable. |
| `service` | string | **yes** | A **canonical service slug** that exists as `environment/<slug>/` — always `*-api`. See §3.1. A bare slug (`"square"`, `"gmail"`) is fatal: `_apply_api_mutation` does `api not in self._urls` → `status="unresolved"`, `reason="no admin URL for square"`. The compiler auto-appends `-api`; hand-authors must not rely on that. |
| `api` | string | no | Alias for `service` (`op.get("service") or op.get("api")`). Prefer `service`. |
| `description` | string | no | Ignored by the parser; documentation only. Reviewers rely on it, so write it. |
| `fires_at_turn` | `"T<n>"` | recommended | Documentation/preflight only — stripped from bodies, never sent. Must satisfy `script/preflight_task.py::_check_fires`: `to_turn <= n < <next stage's to_turn>`. For a stage with `applies_between_turns: ["T2","T3"]` and the next stage at `T5`, legal values are `T3`, `T4` — the conventional value is the stage's own `to_turn` (`"T3"`). Out-of-window → WARN, not FAIL. |
| `silent` | bool | no | Only meaningful in the **list form** of `mutations` (§2.2). In dict form the bucket decides; this key is ignored. |
| `bucket` / `kind` | `"silent"` \| `"filesystem"` | no | List form only. |
| `admin` | object | **strongly recommended** | The explicit admin-op block. Its presence selects **Form A** (§4) and bypasses all fuzzy resolution. |
| `method` | HTTP verb string | Form B/C only | Uppercased by the applier. See §5 and §6 for the accepted verb per path shape. |
| `path` | string | Form B/C only | See §5 / §6. |
| `body` | object | Form B/C only | See §5 / §6. |
| `params` | object | Form C only | Legacy "stage3 params" shape: `{table_id, record_id, field_updates:{...}}`. Also forwarded as the `query` of `POST /admin/apply_as_api`. |
| `raw_eml_path` | string | optional | Stage-relative path to a `.eml` file; validated by preflight (`raw_eml_path OK` / `MISSING`). Envelope key, never sent as row data. |

### 3.1 Legal `service` values

The full set is `basename(environment/*-api)`. As of this writing:

```
activecampaign-api  airbnb-api           airtable-api        algolia-api
alpaca-api          amadeus-api          amazon-seller-api   amplitude-api
asana-api           bamboohr-api         bigcommerce-api     binance-api
box-api             calendly-api         cloudflare-api      coinbase-api
confluence-api      contentful-api       datadog-api         discord-api
docusign-api        doordash-api         dropbox-api         etsy-api
eventbrite-api      fedex-api            figma-api           freshdesk-api
github-api          gitlab-api           gmail-api           google-analytics-api
google-calendar-api google-classroom-api google-drive-api    google-maps-api
greenhouse-api      gusto-api            hubspot-api         instacart-api
instagram-api       intercom-api         jira-api            klaviyo-api
kraken-api          kubernetes-api       linear-api          linkedin-api
mailchimp-api       mailgun-api          microsoft-teams-api mixpanel-api
monday-api          myfitnesspal-api     nasa-api            notion-api
obsidian-api        okta-api             openlibrary-api     openweather-api
outlook-api         pagerduty-api        paypal-api          pinterest-api
plaid-api           posthog-api          quickbooks-api      reddit-api
ring-api            salesforce-api       segment-api         sendgrid-api
sentry-api          servicenow-api       shippo-api          slack-api
spotify-api         square-api           strava-api          stripe-api
telegram-api        ticketmaster-api     tmdb-api            trello-api
twilio-api          twitch-api           twitter-api         typeform-api
uber-api            ups-api              vimeo-api           webflow-api
whatsapp-api        woocommerce-api      wordpress-api       xero-api
yelp-api            youtube-api          zendesk-api         zillow-api
zoom-api
```

Regenerate with `ls environment | grep -- '-api'`. The slug must also be in the
task's `required_apis` (`task.yaml`) so the container is actually launched.

### 3.2 The three op forms, and which one runs

`_apply_api_mutation` dispatches in this order:

| Order | Condition | Handler | Verdict |
| --- | --- | --- | --- |
| 1 | `isinstance(op["admin"], dict)` | `_apply_admin_op` | **Form A — use this.** Unambiguous; no guessing. |
| 2 | `op["path"].startswith("/admin/")` | `_replay_admin_rest` | Form B. Verbatim replay + read-back. Acceptable. |
| 3 | otherwise | `_resolve_target` → fuzzy PATCH, with `_apply_as_api` as an additive fallback | **Form C — avoid.** Cannot create rows; scans every table and can write to the wrong one. Preflight WARNs: *"bare REST form (no admin block) — fuzzy resolution may write to the wrong table/document; use an explicit admin op"*. |

---

## 4. Form A — the `admin` block (canonical)

```json
{
  "id": "sil_cohasset_net_200_to_180",
  "service": "square-api",
  "description": "Net drops 200.00 -> 180.00 as the courtesy adjustment clears.",
  "admin": { "op": "patch", "table": "payments", "pk": "PAY_DOYLE_1003",
             "set": { "amount_money": { "amount": 18000, "currency": "USD" } } },
  "fires_at_turn": "T3"
}
```

### 4.1 `admin.op` — the complete set

| Value | Aliases | Purpose |
| --- | --- | --- |
| `patch` | *(default when `op` is absent)* | Change fields on **one existing row** |
| `update_where` | `bulk` | Change fields on **every row matching a filter** |
| `upsert` | — | **Create** (or overwrite) a row |
| `doc_set` | `doc_merge`, `doc.merge` | Set a **nested value inside a registered document** |

`op` is lower-cased before matching. **Anything else** — notably `delete`,
`delete_where`, `doc_put` — hits the `else` branch and is recorded
`ok=false, status="unresolved", reason="unknown admin op '<kind>'"`. There is
**no delete op** in this form; if you truly need one, use Form B
(`POST /admin/data/<table>/bulk` with `{"op": "delete_where", ...}`).

### 4.2 `op: "patch"`

| Key | Type | Required | Legal values |
| --- | --- | --- | --- |
| `op` | `"patch"` | no (default) | — |
| `table` | string | **yes** | A **registered store table name**. Resolved via `_resolve_store_table` against `GET /admin/tables`: exact (case-insensitive) match first, then `records_<wanted>`, `<wanted>s`, `records_<wanted>s`. **This is the name passed to `_store.register(...)` in `environment/<svc>/<svc>_data.py` — NOT the seed filename and NOT the service slug.** Putting the service slug here produces `400 table '<slug>' not registered on store '<slug>'`. |
| `pk` | string \| number | **yes** | The **primary-key value** of the target row, using the column the store declares as `primary_key` in `/admin/tables` (`id`, `Id`, `item_id`, `sys_id`, `component_key`, …). Coerced with `str()`. Missing row → `status="unresolved", reason="row not found"`. |
| `set` | object | **yes** | `{<live_field_name>: <value>}`. Empty/absent `{}` makes the op a no-op and trips the validator's zero-field check. See §7 for the two rules that govern the values. |

### 4.3 `op: "update_where"` / `"bulk"`

| Key | Type | Required | Legal values |
| --- | --- | --- | --- |
| `op` | `"update_where"` \| `"bulk"` | **yes** | — |
| `table` | string | **yes** | As §4.2. |
| `where` | object | **yes** in practice | `{<live_field>: <expected>}`, ANDed. Compared with `_loose_eq`: exact `==` first, then case-insensitive string comparison — so `true`/`"True"`/`"true"` and `1`/`"1"` all match. **An empty/absent `where` matches EVERY row** (`all([])` is `True`) — a silent mass-mutation footgun. |
| `set` | object | **yes** | As §4.2, applied to each matched row. |

Outcome: `matched` / `patched` counts; `status="applied"` if ≥1 row patched,
else `"no-match"`. Verification samples the **first** matched row only.

### 4.4 `op: "upsert"`

| Key | Type | Required | Legal values |
| --- | --- | --- | --- |
| `op` | `"upsert"` | **yes** | — |
| `table` | string | **yes** | As §4.2. |
| `row` | object | **yes** | The complete new row, **in LIVE (post-coercion) shape** — see §7.2. For airtable-style stores nest the business columns under `"fields"`. |
| `pk_field` | string | no (default `"id"`) | Which key of `row` holds the primary key. Set this when the store's declared pk is not `id` (e.g. `"Id"`, `"item_id"`, `"component_key"`). If `row[pk_field]` is absent, `pk` is `None` and read-back verification is skipped. |

This is the **only** way to create a row. A bare REST `POST` with no `admin`
block resolves no existing target and is logged `unresolved`
(`inject_director.py:474-476`).

> **Danger.** `upsert` writes straight into the live table, **bypassing the
> store's `_coerce_*` loader**. Seed-shaped values (CSV strings, `"0"`/`"1"`
> booleans, stringified epochs) will crash the service's list endpoints at read
> time. Author `row` in exactly the shape `_coerce_<table>` produces.

### 4.5 `op: "doc_set"` / `"doc_merge"` / `"doc.merge"`

For services whose state is a **document**, not a table (e.g. notion-api's
`properties` = `{page_id: {prop_name: {type, value}}}`, or `workspace`).

| Key | Type | Required | Legal values |
| --- | --- | --- | --- |
| `op` | `"doc_set"` \| `"doc_merge"` \| `"doc.merge"` | **yes** | All three behave identically (read-modify-merge of one leaf). |
| `document` | string | **yes** | A name passed to `_store.register_document(...)`; check `GET /admin/tables` → `documents`. Alias accepted: `doc`. |
| `path` | array | **yes** | Ordered key path to the leaf, e.g. `["B-11", "Panel Score", "value"]`. **Must be non-empty** (`reason: "empty path"`) and **every intermediate key must already exist** (`reason: "path [...] missing at '<key>'"`) — `doc_set` cannot create intermediate nodes. Point at the **leaf you mean**: targeting `["B-11","Panel Score"]` replaces the whole `{type, value}` wrapper. |
| `value` | any JSON | **yes** | The new leaf value. Match the leaf's existing type — e.g. keep it a **string** when the property is seeded `rich_text`; only `number`-typed properties get float-coerced by the service. |

Mechanics: reads `GET /admin/doc/<doc>`, mutates the leaf in memory, then
`POST /admin/doc/<doc>/merge` with `{"fields": {path[0]: <whole subtree>}}`, then
re-reads and asserts the leaf. If `before == value` it short-circuits to
`ok=true, changed=false, status="no-change"`.

---

## 5. Form B — admin-plane REST replay

Selected when `path` starts with `/admin/`. Replayed verbatim, with read-back.
`method` defaults to `"POST"` and is upper-cased.

| Shape | `method` | `path` | `body` | Effect |
| --- | --- | --- | --- | --- |
| Upsert | `POST` | `/admin/data/<table>` | `{"row": { ... }}` | Store upsert. pk taken from `row[<declared pk>]`, else `row.id`, else `row.pk`. |
| Patch | `PATCH` | `/admin/data/<table>/<pk>` | `{"fields": { ... }}` **or** a flat object | Store patch. If `body.fields` is a dict it is used; otherwise the **whole body** is used as the field map. Either way `_admin_patch` re-wraps it as `{"fields": ...}` on the wire, which is what the endpoint's `_PatchIn` model requires — so both authorings are safe. |
| Raw / other | `POST` | any other `/admin/...` | endpoint-specific | Posted as-is. If the response body has a `results` list, any `ok:false` entry fails the op with `"<n>/<m> raw op(s) failed"`. |

Anything else (`GET`, `PUT`, `DELETE`, or a non-POST unmatched path) →
`ok=false, status="unresolved", reason="unsupported admin-plane replay: <M> <path>"`.

**`<table>` is parsed positionally** by `^/admin/data/([^/]+)/?$` and
`^/admin/data/([^/]+)/([^/]+)/?$`. The service slug is already supplied by
`op.service` and must **never** appear in the path. Writing
`/admin/data/notion-api/page_properties` yields `table="notion-api"`,
`pk="page_properties"` and a hard `400`.

Reachable admin endpoints (`environment/admin_plane.py`):

```
GET    /admin/health                    GET    /admin/doc/{doc}
GET    /admin/tables                    PUT    /admin/doc/{doc}
GET    /admin/data/{table}              POST   /admin/doc/{doc}/merge
GET    /admin/data/{table}/{pk}          POST   /admin/inject/raw
POST   /admin/data/{table}              POST   /admin/inject/one_shot
PATCH  /admin/data/{table}/{pk}          POST   /admin/scenario/apply
DELETE /admin/data/{table}/{pk}          GET    /admin/snapshot
POST   /admin/data/{table}/bulk          POST   /admin/snapshot/restore
POST   /admin/apply_as_api               GET    /admin/drift/log
                                        POST   /admin/drift/log/clear
```

`POST /admin/inject/raw` takes `{"operations": [...]}` where each operation's
`op` is one of: `data.upsert`, `data.patch`, `data.delete`, `data.delete_where`,
`doc.set`, `doc.merge`. `POST /admin/data/<table>/bulk` takes
`{"op": "update_where" | "delete_where", "where": {...}, "set": {...}}`. These
are the only routes to a **delete** from a mutations.json.

---

## 6. Form C — bare service REST (legacy; avoid)

No `admin` block and a `path` that does not start with `/admin/`. Two sub-shapes
are understood by `_resolve_target`:

**REST form** — `{method, path: ".../{rec_KEY}", body: {...}}`. The business key
is pulled from the `{...}` placeholder in `path` (prefixes `rec_`, `page_id_`,
`id_`, `rec`, `page_` are stripped); if there is no placeholder, the last path
segment is used.

**`params` form** — `{action, params: {table_id, record_id, field_updates: {...}}}`.
Key comes from `params.record_id` / `params.page_id` / `params.id`;
values from `params.field_updates`.

Field extraction precedence in `_extract_fields`:

1. `params.field_updates` (keys starting `_` dropped)
2. `body.fields` (airtable shape)
3. `body.properties` (notion/confluence shape, flattened to leaf scalars)
4. the whole `body` minus `_INJECT_ENVELOPE_KEYS` and keys starting `_`

Resolution then scans candidate tables — narrowed by `_SERVICE_RESOLUTION` for
`airtable-api` (`records_*`), `notion-api` and `confluence-api` (`pages`), and
**every table** for all other services — matching the key against the pk or
against key columns with fuzzy normalisation (`_`/`-` → space, lower-case,
substring both ways).

Why to avoid it: it can only **PATCH an existing row** (creates are impossible),
its table scan can land on the wrong table, and its field matching is
case-insensitive-fuzzy. Zero extractable fields → `unresolved`. Fields that
map to no column → `status="partial"`, `unmapped_fields=[...]`.

An additive fallback, `POST /admin/apply_as_api`, replays the op through the
mock's **own** endpoint and can rescue an otherwise-failing op; it reports
success only on a 2xx **and** an observed store change. Disable with
`WCB_INJECT_APPLY_AS_API=0`. It never turns a success into a failure.

---

## 7. Field values in `set` / `row` / `fields`

Two rules cause nearly all real-world injection failures.

### 7.1 The admin PATCH shallow-merges **top-level keys only**

`environment/admin_plane.py` → `Table.patch(pk, fields)` replaces each top-level
key wholesale. A nested object must be **resent whole**:

```json
"set": { "amount_money": { "amount": 18000, "currency": "USD" } }   // correct
"set": { "amount_money": { "amount": 18000 } }                      // WRONG: clobbers currency
```

### 7.2 Target the **LIVE** field name and the **LIVE** value type

`environment/<svc>/<svc>_data.py`'s `_coerce_<table>()` functions reshape seed
rows at **load** time. The live row key is often **not** the seed-file column
name, and the live value type is often not the seed's string.

```python
# environment/square-api/square_data.py
def _coerce_payments(rows):
    ...
    "amount_money": _money(r["amount"], r["currency"]),   # flat amount+currency -> nested Money
```

Seed row: `{"id": "PAY_DOYLE_1003", "amount": "20000", "currency": "USD"}`
(the string `amount_money` appears **nowhere** in `mock_data/`).
Live row: `{"id": "PAY_DOYLE_1003", "amount_money": {"amount": 20000, "currency": "USD"}}`.
→ the op must set `amount_money`, with an **int** `amount`.

Contrast `figma-api`, whose `_coerce_components` is
`[_strip_ctx(r) for r in rows]` — no renaming, so seed key `description`
is also the live key.

**Procedure before writing any `set`/`row`:** read
`environment/<svc>/<svc>_data.py`, find `_store.register("<table>", primary_key=...)`
(or `register_document`), read its `_coerce_*`, and author against the
post-coercion shape. Confirm with `GET /admin/tables` and
`GET /admin/data/<table>/<pk>` on a live container.

### 7.3 Verification blind spot

`_read_back_row` filters **nested dicts and lists** out of the comparable set, so
`verified` is vacuously `True` for a nested value like `amount_money`. A wrong
nested write can be reported as verified. Scalar values are genuinely asserted.
`_admin_doc_set`, by contrast, always re-reads and asserts the exact leaf.

---

## 8. `mutations.filesystem` ops

```json
{ "id": "fs_seed_reconcile_dir", "action": "mkdir",
  "dst": "/workspace/home/home/Documents/reconcile_tmp" }

{ "id": "fs_stage2_memo", "action": "copy",
  "src": "note_16.docx",
  "dst": "/workspace/home/home/Documents/note_16.docx" }
```

| Key | Type | Required | Legal values |
| --- | --- | --- | --- |
| `id` | string | **yes** | Unique; appears in the timeline. |
| `action` | string | **yes** | **Exactly `"copy"` or `"mkdir"`** (`_FS_ALLOWED_ACTIONS`). Anything else — notably `"patch"`, `"delete"`, `"move"`, `"append"` — is `ok=false, status="invalid"` with the message *"'patch' is not supported — author a full replacement file and use action:copy"*. The compiler defaults a missing `action` to `"copy"`. |
| `src` | string | **yes for `copy`**, ignored for `mkdir` | Path resolved in three steps: (1) `<stage_dir>/<src>`; (2) `rglob` on the **basename** under the stage dir, skipping any path containing `_placeholders` (ambiguous if >1 hit — rglob order is unstable, so prefer an explicit relative path); (3) task-root fallback `<stage_dir>/../../<src>`. Unresolvable → `ok=false, status="missing_src"`. Best practice: the bare filename of a file the compiler copied into `inject/stage<N>/`. |
| `dst` | string | **yes** | **Absolute** container path (the compiler raises `CompileError` if it does not start with `/`). Convention is under the agent workspace, e.g. `/workspace/home/home/Documents/<name>`. Missing `src` or `dst` for a `copy` → `status="skipped", reason="missing src/dst"`. |

Outcomes: `copied`, `mkdir`, `invalid`, `missing_src`, `skipped`
(`"no workspace copy hook"` / `"missing src/dst"`), `skipped_container_down`,
`error`. `skipped_container_down` at **seed** time is the one documented benign
non-`ok` outcome (`is_defect`).

---

## 9. Fail-closed vs advisory validation

`src/utils/inject_validator.py::validate_inject_script` is **fatal** on:

* a `service` slug that resolves to no admin URL;
* an op from which zero mutation fields can be extracted;
* a `filesystem` `action` not in `("copy", "mkdir")`;
* a `filesystem` `src` or `dst` that cannot be resolved;
* a first-boundary stage whose target `pk` does not exist in the snapshot.

`script/preflight_task.py` **warns** (does not fail) on:

* bare-REST form with no `admin` block;
* `fires_at_turn` outside `[to_turn, next_stage.to_turn)`;
* a missing `raw_eml_path`;
* a stage whose `mutations` produced no recognized ops.

**Known gap:** nothing statically checks that `<table>` in an
`/admin/data/<table>/...` path — or `admin.table` / `admin.document` — is
actually registered. Verify by hand against `GET /admin/tables`.

---

## 10. Outcome vocabulary in `inject_timeline.jsonl`

Read this after every run; `ok:false` on any op invalidates the run.

| `status` | Meaning |
| --- | --- |
| `applied` | Write landed and (for scalars) was verified on read-back |
| `partial` | Write landed but some fields were dropped as unmapped (Form C) |
| `no-change` | `doc_set` leaf already held the target value |
| `no-match` | `update_where` matched zero rows |
| `unresolved` | Unknown `admin.op`; no admin URL for the service; row not found; target not locatable; unsupported admin-plane replay |
| `failed` | Non-2xx from the admin plane, **or** a 2xx whose values were absent on read-back (`"write not observed on read-back"`) |
| `invalid` | Unsupported filesystem `action` |
| `missing_src` | Filesystem `src` unresolvable |
| `skipped` / `skipped_container_down` | No copy hook / container not up |
| `error` | Unhandled exception (`reason` carries the message) |

Event types: `inject.seed.start`, `inject.seed.done`, `inject.stage.applied`
(carries `silent_ops`, `loud_ops`, `applied_ops`, `failed_ops`, `outcomes`),
`inject.api`, `inject.fs`.

---

## 11. Authoring checklist

1. Is this stage `stage0`? Then **no `silent` ops** — they are dead code. Put
   pre-T0 API state in `mock_data/`.
2. `applies_between_turns` = `[null, "T0"]` for seed, else `["T<n-1>", "T<n>"]`,
   with a unique `to_turn` across all non-seed stages.
3. `applied_at_local_time` sits inside the real wall-clock gap between those two
   turns, with the correct DST offset for `prompts.json.timezone`.
4. `service` is a canonical `*-api` slug present in `task.yaml: required_apis`.
5. Every op carries an explicit `admin` block (Form A).
6. `admin.table` / `admin.document` is the name from
   `_store.register(...)` / `register_document(...)` — not a filename, not the slug.
7. `admin.pk` uses the column named by that table's declared `primary_key`.
8. `admin.set` / `row` uses **post-`_coerce_*` field names and value types**, and
   resends nested objects whole.
9. `update_where` has a non-empty `where`.
10. `doc_set.path` ends at the exact leaf and every intermediate key exists.
11. Every `silent` op is covered by a grader — either a value assertion
    (`test_output.py` / `pytest.json`) or a scope-discipline rubric item that
    rewards correctly ignoring it.
12. Run `script/preflight_task.py`, then after the run grep
    `inject_timeline.jsonl` for `"ok": false`.
