# API Data and Filesystem Mutation — End-to-End Mechanism

This document explains, end-to-end, two coupled mechanisms in the kensei
delivery fork of WildClawBench:

1. **API data** — how the mock API fleet under `environment/` is built, seeded,
   overlaid per task, injected into a task run, and reached by the agent over
   HTTP.
2. **Filesystem mutation** — how the agent's writes into its workspace are
   captured via a baseline-diff, separated from harness noise, collected into
   `artifacts/` vs. `workspace_full/`, and finally published into a delivery
   bundle.

Every claim below is anchored to a concrete `file:line` reference. Where a line
count or a symbol is cited, it was read directly from source (not from a stale
`AGENTS.md` header — several of those line counts are known to be out of date).

> **Scope note.** This covers the *mock API data plane* and the *workspace file
> mutation plane*. It does not cover trajectory JSON construction, judging
> (Channel A pytest / Channel B rubric council), or model routing except where
> those planes touch data/filesystem flow.

---

## 0. Mental model (one picture)

```
                          HOST (orchestrator: eval/run_batch.py)
  ┌──────────────────────────────────────────────────────────────────────────────┐
  │  input/<task>/                          environment/            (source)     │
  │  ├─ prompt.txt / task.yaml              ├─ <api>-api/                        │
  │  ├─ rubric.json  (HOST-ONLY)            │   ├─ server.py                     │
  │  ├─ persona/  data/                     │   ├─ <name>_data.py                │
  │  └─ mock_data/<api>-api/*.csv|json  ─┐  │   ├─ service.toml                  │
  │         (per-task overlays)          │  │   └─ *.json / *.csv  (baked seed)  │
  │                                      │  └─ _mutable_store.py, admin_plane.py │
  │  1) _augment_task_with_mocks ────────┘        tracking_middleware.py         │
  │       task['required_apis'/'distractor_apis'/'mock_overlays'/'env_dict']     │
  │                                                                              │
  │  2) build_mock_image_if_needed → kensei3-mocks:v1  (all 101 svcs baked in)   │
  │  3) start_mock_stack(overlays, enabled_apis, admin_env, publish_ports)       │
  │        -v host_overlay:/opt/mocks/<api>/<file>:ro                            │
  └──────────────────────────────────────────────────────────────────────────────┘
                 │ docker network (task net is --internal, no egress)
                 ▼
  ┌────────────────────────────┐        ┌─────────────────────────────────────┐
  │  MOCK CONTAINER            │        │  AGENT CONTAINER (task_id)          │
  │  supervisord               │  HTTP  │  /tmp_workspace  (real dir)         │
  │  ├─ uvicorn :8000 api A    │◄──────►│  /root/workspace ── symlink ───┐    │
  │  ├─ uvicorn :8001 api B    │ <API>_ │  /root/.openclaw/workspace ────┤    │
  │  └─ ...                    │ API_URL│                                ▼    │
  │  in-memory Store per api   │        │            (agent reads/writes here)│
  │  /opt/mocks/<api>/<file>   │        └─────────────────────────────────────┘
  └────────────────────────────┘                         │
                                                         │ 4) baseline snapshot
                                                         │    (before agent runs)
                                                         │ 5) agent runs
                                                         │ 6) collect_output:
                                                         ▼    diff → artifacts/
                       output/<backend>/<task>/trajectories/<model>/run_N/
                       ├─ artifacts/        (baseline-diff: agent-produced only)
                       ├─ workspace_full/   (forensic: entire /tmp_workspace)
                       ├─ task_output/  sessions/  artifacts_excluded.json
                       └─ ... → harbor bundle → deliver.sh → kensei-delievery
```

Two invariants hold this whole picture together:

- **The agent only ever talks to mock APIs over HTTP.** No mock seed file, no
  rubric, no ground truth is ever copied into the agent container. (Prevents
  data leakage.)
- **The agent's deliverables are whatever it *changes* under its workspace**,
  computed as a diff against a baseline taken *after* all input staging and
  *before* the agent starts. An empty `artifacts/` is therefore a real signal
  ("agent produced nothing"), not a harness bug.

---

# PART 1 — The Mock API Data Plane

## 1.1 The fleet: `environment/`

`environment/` contains **101 mock API services**, one directory per API named
`<api>-api/` (e.g. `airbnb-api/`, `quickbooks-api/`, `gmail-api/`). There are
104 directories in total; 3 are legacy stubs without a `service.toml` and are
not part of the live fleet.

Each service directory carries a fixed contract:

| File | Role |
|---|---|
| `service.toml` | `[service]` block: `name`, `port`, `env_var_name`, `healthcheck_path`. The **single source of truth** for a service's port and env var. |
| `server.py` | FastAPI app. Imports `<name>_data`, installs the audit + admin planes, exposes the API endpoints. |
| `<name>_data.py` | The data module: registers tables/documents on the in-memory store, defines the seed loaders, implements business mutations. |
| `Dockerfile` | Pinned `python:3.12-slim` base for standalone single-service builds. |
| `requirements-locked.txt` | Hash-pinned deps (`pip install --require-hashes`). |
| `*.json` / `*.csv` | **Baked seed data.** |
| `<name>_api_postman_collection.json` | Endpoint documentation. |

**Ports** run from `8000` to `8101` with a gap at `8069` (unassigned — do not
reuse it).

At the `environment/` root sit the **shared planes** every service links against:

| File | Role |
|---|---|
| `_mutable_store.py` | The in-memory mutable store (`Table`, `Document`, `Store`, `get_store`). |
| `tracking_middleware.py` | The **audit plane** (request/response capture, `/audit/*`). |
| `admin_plane.py` | The **admin/drift plane** (out-of-band data mutation, `/admin/*`). |
| `smoke_eager_load.py` | Mandatory pre-merge import gate: imports all `<name>_data` modules; exits `0` iff every one loads cleanly. |
| `test_all_apis.py` | Fleet-wide integration harness. |
| `MIGRATION_RECIPE.md` | The 5-step recipe used to migrate all 101 services onto the drift plane. |

## 1.2 Storage model: pure in-memory, process-local

There is **no database** behind the stores. Each service holds its data in
plain Python dicts inside the uvicorn process. (`sqlite_mcp_server.db` exists in
the tree but belongs to an unrelated MCP server, not the mock stores.)

`_mutable_store.py` defines three classes:

- **`Table`** — a row collection. `__slots__` = `_rows`, `_order`, `_lock`,
  `_parent`. Methods: `rows()`, `get()`, `find()`, `upsert()`, `patch()`,
  `delete()`, `update_where()`, `delete_where()`, plus `_dump()`/`_load()`.
- **`Document`** — a single JSON blob. Methods: `get()`, `set()`, `merge()`.
- **`Store`** — the per-API container. Holds an `RLock` **shared** with all of
  its child `Table`s and `Document`s, so cross-table writes within one API are
  consistent.

A module-global registry `get_store(api_name)` returns (and lazily creates) the
single `Store` for each API, keyed in a global `_STORES` dict.

Key behavioral guarantees:

- **Reads return deep copies.** Callers can never mutate store state by holding
  a returned row.
- **Writes hold the `RLock`, preserve insertion order (`_order`), and refuse to
  change the primary-key column** of an existing row.
- **Lazy loading.** A table is registered with an `initial_loader` lambda;
  actual seed loading is deferred until first access. Every `<name>_data.py`
  module calls `_store.eager_load()` at import time, which forces every loader
  to run so that any malformed seed surfaces as a `CoerceError` *at import*
  (this is what `smoke_eager_load.py` gates on).
- **Primary-key collisions** in seed data are auto-suffixed as
  `_pk={val}#{i}` rather than silently dropped.

### The CSV-overlay-wins invariant

`read_seed_with_ctx()` in `_mutable_store.py` enforces the single most important
seeding rule: **a sibling `.csv` always shadows a baked `.json`.** If both
`<name>.json` and `<name>.csv` exist for a table, the CSV wins. This is exactly
what makes per-task overlays work (see §2.3): a task drops a `.csv` overlay and
it deterministically replaces the baseline `.json`.

### Resilient loading

When the environment variable `MOCK_RESILIENT_LOAD=1` is set — and the mock
stack **always** sets it (`mock_stack.py:389`) — a seed/overlay that fails to
load **degrades to an empty table** instead of crashing uvicorn. This keeps a
single bad per-task overlay from taking down the whole shared container (which
would also disable the admin/injection plane). Host-side importers and
validators stay strict; only the live container is lenient.

### Canonical `<name>_data.py` shape (airbnb example)

```python
_store = get_store("airbnb-api")
_store.register("reservations",
                primary_key="reservation_id",
                initial_loader=lambda: [])       # born-empty table

def create_reservation(row):
    _store.table("reservations").upsert(row)

def cancel_reservation(pk):
    _store.table("reservations").patch(pk, {"status": "cancelled"})

_store.eager_load()                              # surface load errors at import
```

## 1.3 The audit plane (`tracking_middleware.py`)

`install_tracker(app)` installs a `RequestTracker` ASGI middleware that captures
every request/response pair into a module-global `_request_log`:

- Ring buffer capped at **10,000 entries**; each body capped at **512 KB**.
- Skips `/audit*`, `/admin*`, `/health`, and any request carrying the
  `x-wcb-suppress-audit` header.
- Exposes `GET /audit/requests`, `GET /audit/requests/clear`,
  `GET /audit/summary`.

The audit plane is what Channel A tests and rubric judges use to verify *that
the agent actually called an API* (e.g. "did it POST a reservation?").

## 1.4 The admin / drift plane (`admin_plane.py`)

The admin plane is an **out-of-band mutation surface** used by the host-side
DriftDirector to change mock data *mid-run* (simulate a booking getting
cancelled, an invoice getting paid, etc.). It is **off by default** and gated
three ways:

1. `MOCK_ADMIN_ENABLED=1` must be set.
2. The caller's IP must be in `MOCK_ADMIN_ALLOWLIST`.
3. An optional `MOCK_ADMIN_TOKEN` must match.

On any gate failure it returns **404, not 403** — it hides its own existence
from the agent.

`install_admin_plane(app, store, one_shot_registry)` is idempotent and calls
`store.capture_baseline()` on install. Endpoints under `/admin` include:

- `apply_as_api` — replays a mutation through the *real* API endpoint in-process
  (audit-suppressed), returning `changed: bool` via an md5 fingerprint.
- `health`, `tables`, `data/{table}` (GET/POST),
  `data/{table}/{pk}` (GET/PATCH/DELETE), `data/{table}/bulk`.
- `doc/{doc}` (GET/PUT), `doc/{doc}/merge`.
- `inject/raw`, `inject/one_shot` (queues a one-shot response interceptor;
  fires an `X-Mock-Drift-One-Shot` header).
- `scenario/apply`, `snapshot`, `snapshot/restore`.
- `drift/log`, `drift/log/clear`.

Every admin write goes through a `Store` method **and** appends a record to the
`_drift_log` via `store.record(op)`:
`{ts, op, table, pk, before, after, source}`. There are two drift logs:
the per-container `/admin/drift/log`, and the host-side DriftDirector's
`drift_timeline.jsonl`.

## 1.5 `server.py` wiring order (matters)

Each service's `server.py` wires the planes in a fixed order (airbnb example):

```python
import airbnb_data
try:
    from tracking_middleware import install_tracker
    from admin_plane import install_admin_plane
except ModuleNotFoundError:            # standalone runs: no-op stubs
    install_tracker = install_admin_plane = lambda *a, **k: None

app = FastAPI()
install_tracker(app)                    # FIRST
install_admin_plane(app, store=airbnb_data._store)   # THEN admin
```

The ordering is deliberate: installing the tracker first keeps the `/admin`
surface **out of audit scope**, so admin traffic never pollutes the audit log
the graders read.

## 1.6 Building the mock image (`src/utils/mock_stack.py`)

The fleet is **not** a single Python app mounting 101 sub-apps. Instead:

- **One Docker image** (`MOCK_IMAGE = "kensei3-mocks:v1"`) bakes in *all* 101
  services.
- Inside one container, **each service is its own uvicorn process**, launched by
  **supervisord**.

Image build (`build_mock_image_if_needed`, ~`mock_stack.py:296-366`):

- A **content hash** (`_compute_mock_content_hash`, `:28-50`) is a sha256 over a
  manifest of `(relpath, size, mtime)` for `environment/` plus a builder-version
  string (`_BUILDER_VERSION = "rt-filter-1"`), truncated to 16 hex chars.
- The built image is labeled `kensei3.content_hash=<hash>`
  (`_CONTENT_HASH_LABEL`). On the next run, the current content hash is compared
  to the image label; if they differ the image is **rebuilt**. Forcing:
  `KENSEI_MOCK_REBUILD=1` or `--rebuild-mocks` (does `docker rmi` then rebuild).
- A cross-process **flock** on `/tmp/kensei3-mocks-build.lock` prevents
  concurrent rebuilds.
- The generated Dockerfile (`_generate_dockerfile`, `:215-265`) is
  security-hardened:
  - **Digest-pinned base** — `FROM python:3.11-slim@sha256:a3ab0b96...`
    (`:244`); the `@sha256:` suffix defends against tag-mutation supply-chain
    attacks and **must not** be removed (BUG-S-002, `:224-233`). Note the
    *shared* image pins `3.11`, while the per-API standalone Dockerfiles pin
    `3.12-slim@sha256:090ba77...`; the version mismatch is a known, separately
    tracked item — both surfaces stay digest-pinned.
  - **Non-root runtime** — creates an `app` system user, does all root-only work
    first, then `chown -R app:app /opt/mocks` and `USER app` before `CMD`
    (`:245`, `:259`, `:263`; BUG-S-001). The 101 services bind non-privileged
    ports 8000+, so no `CAP_NET_BIND_SERVICE` is needed.
  - **Hash-pinned per-service install** — a loop over
    `/opt/mocks/*/requirements-locked.txt` with `pip install --require-hashes`
    (`:250-253`; BUG-S-003) rejects any wheel whose sha256 diverges from the
    lockfile.
  - `COPY env_dir/ → /opt/mocks/` (`:249`), `ENV PYTHONPATH=/opt/mocks`
    (`:260`), a `HEALTHCHECK` calling `/healthcheck.sh` (`:261-262`), and
    `CMD ["/start.sh"]` (`:264`), which is what launches supervisord.
- The generated supervisord config (`_GEN_SUPERVISORD_PY`, `:155-194`) reads
  `MOCK_ENABLED_APIS` (a comma list; empty ⇒ all; a zero-match list falls back
  to all) and emits one `[program:<api>]` uvicorn block per selected API, plus a
  `/tmp/mock_enabled_ports` manifest.

## 1.7 Starting the stack (`start_mock_stack`, `mock_stack.py:369-450`)

```python
def start_mock_stack(container_name, network, image=MOCK_IMAGE,
                     overlays=None, admin_env=None,
                     publish_ports=None, enabled_apis=None): ...
```

What it does, verified against source:

1. `docker rm -f <container_name>` (clean slate).
2. **Overlay mounts** — for each `overlays[api][filename] = host_path`, add
   `-v {host_path}:/opt/mocks/{api}/{filename}:ro` (`:377-384`). This bind-mounts
   the per-task overlay file **read-only over the baked baseline** inside the
   image. This is how task data reaches the mock.
3. Always add `-e MOCK_RESILIENT_LOAD=1` (`:389`).
4. If `enabled_apis` is non-empty, add
   `-e MOCK_ENABLED_APIS=<sorted csv>` (`:392-394`) so supervisord starts only
   those services.
5. Add each `admin_env` var (token value never logged) (`:397-402`).
6. For each `publish_ports` entry, add `-p 127.0.0.1::<port>` — **bind to
   loopback only** (`:404-408`); the host-side DriftDirector reaches the admin
   plane via localhost, and it must never be exposed on public interfaces.
7. **Dual-homing** (`:409-450`): the task network is created `--internal`
   (no egress). Port publishing (`-p`) only binds if the container's
   creation-time network can route to the host — which the internal net cannot
   on Docker Desktop. So when `publish_ports` is requested, the container is
   **created on `bridge` first** (so `-p` binds), then **`docker network connect
   <internal task net> <container>`** is run afterwards. The agent stays
   isolated with no egress; only this one mock is dual-homed, mirroring the
   LiteLLM sidecar. Quiescent runs (no publish) stay single-homed on the
   internal net.

The agent reaches each service at `http://<mock_container>:<port>`, learning the
URL from the `<API>_API_URL` environment variable derived from `service.toml`'s
`env_var_name`.

## 1.8 Author-side validation (not on the eval path)

`mock_overlay_validator/` is a **standalone, stdlib-only** author-time auditor
(`validate.py` + `examples/<api>-api/` snapshots) that checks per-task overlays
(e.g. `SCHEMA_MISSING_COLUMNS`) against the CSV-overlay-wins invariant. It is
**not imported** by anything under `eval/` or `src/`. A companion
`mock_overlay_validator_docker/` exists for containerized validation.

---

# PART 2 — The Task Data-Injection Path

This part traces how one task's data goes from `input/<task>/` on the host to a
live, overlaid mock stack the agent can call.

## 2.1 The task corpus: `input/<task>/`

A representative task directory (`input/darren_weston_...`):

| Path | Role | Sent to agent? |
|---|---|---|
| `prompt.txt` | The task prompt. | Yes (as the prompt). |
| `task.yaml` | `difficulty`, `required_apis: [...]`, `distractor_apis: [...]`. | No (metadata). |
| `rubric.json` | Channel B judging criteria. | **No — HOST-ONLY.** |
| `test_outputs.py` + `test_weights.json` | Task-provided Channel A tests (skips testgen). | **No — HOST-ONLY.** |
| `golden_steer_flow.md` | Authoring note / ground-truth. | **No — HOST-ONLY.** |
| `persona/` | `AGENTS.md`, `SOUL.md`, `MEMORY.md`. | Yes (docker-cp'd, §2.6). |
| `data/` | Legacy input docs (xlsx/pdf/docx/jpg). | Yes (docker-cp'd, §2.6). |
| `mock_data/<api>-api/*.csv\|json` | **Per-task API overlays.** | Indirectly — served *by the mock*, never copied into the agent. |

Ground-truth values are baked into the mock overlays (e.g. a vendor `Balance` of
`1247.50` that the rubric later checks). The agent obtains those values **only by
querying the mock**, which is the whole point of the exercise.

`task_parser.parse_native_task` (`task_parser.py:680-731`) reads the corpus into
a typed task with `persona_dir` / `data_dir` / `gt_dir` fields, and
`_append_workspace_hint` (`:716`) appends the list of staged `home/` files to
the prompt so the agent knows what inputs it has. `_normalize_api_name`
(`run_batch.py:596-602`) coerces bare names like `quickbooks` to the canonical
`quickbooks-api`.

## 2.2 `_augment_task_with_mocks` (`run_batch.py:743-786`)

Called once per task from `run_single_task` (guarded by `if config is not None`).
It **mutates the task dict in place** and does **not** edit the prompt.

```python
def _augment_task_with_mocks(task, config, mock_env_dict):
    required, distractor, overlays = _resolve_task_apis(task, config)
    task["env_dir"]       = str(config.environment_dir) if config.environment_dir else ""
    task["required_apis"] = sorted(required)
    task["mock_overlays"] = overlays
    task["distractor_apis"] = distractor
    enabled_apis = (set(task.get("required_apis") or [])
                    | set(task.get("distractor_apis") or [])
                    | set((task.get("mock_overlays") or {}).keys()))
    if mock_env_dict:
        if enabled_apis:
            filtered = {}
            for k, v in mock_env_dict.items():
                if k.endswith("_API_URL"):
                    api = k[:-4].lower().replace("_", "-")   # GMAIL_API_URL -> gmail-api
                    if api not in enabled_apis:
                        continue
                filtered[k] = v
            task["env_dict"] = filtered
        else:
            task["env_dict"] = dict(mock_env_dict)
    else:
        task.setdefault("env_dict", {})
    # skills path + default-skill merge ...
```

The critical effect: the task's `env_dict` is filtered down to **only the APIs
this task uses** (required ∪ distractor ∪ overlays). The agent never receives
URLs for the other ~100 services, so it cannot call servers its stack never
starts, and the mock-health logger does not spam warnings for intentionally
disabled services.

## 2.3 API resolution: `_resolve_task_apis` (`run_batch.py:605`)

Precedence for **required** APIs:

1. `task['required_apis_declared']` — from `task.yaml` / `task.json`
   (`task_parser` `:754` / `:885`).
2. The subdirectory names under `<task_dir>/mock_data/<api>/`
   (`run_batch.py:656-668`).
3. `infer_required_apis()` — keyword inference over the prompt
   (`:645-650`), used only when nothing more explicit exists.

**Distractor** resolution:
- `'auto'` ⇒ the full-catalog complement (via `compute_distractor_skills`).
- an explicit list ⇒ those minus the required set.
- missing / null / `[]` ⇒ no distractors.

**Overlays** (`:661-668`) are built by walking `<task_dir>/mock_data/<api>/*`,
producing `{api: {filename: absolute_host_path}}` — exactly the shape
`start_mock_stack` consumes at §1.7 step 2.

## 2.4 Bringing up the stacks

There are two mock-stack tiers:

**Shared/base stack** (`_setup_litellm_and_mocks`, `run_batch.py:3221-3247`):
`build_mock_image_if_needed` → `mock_container = f"mocks-{batch_id}"` →
`start_mock_stack(..., enabled_apis=mock_enabled_apis)` →
`wait_for_mock_stack_healthy(180s)`. Then `discover_services` populates
`mock_env_dict[env_var_name] = f"http://{mock_container}:{port}"`.

**Per-task stack** (`_start_task_mock_stack`, `run_batch.py:3269`, invoked from
`run_single_task` `:1849-1857` **only** when `enable_mock_stack` and a network
exist and `task['mock_overlays']` is non-empty):

- Container name `mocks-task-<safe_id>-<uuid[:6]>`.
- Enables the **admin plane** when the task carries `drift.yaml` / `stages.yaml`
  / an `inject/` dir, or overlays overlaid ports: sets `MOCK_ADMIN_ENABLED=1`,
  an allowlist of gateway IPs, a random `MOCK_ADMIN_TOKEN=uuid.hex`, and
  `publish_ports`.
- Waits only for the **overlaid** ports to become healthy
  (`max(180s, 20s/API)`, overridable via `KENSEI_TASK_MOCK_HEALTH_TIMEOUT`).
- Builds the task's `env_dict` as `{ENV_VAR: http://<container>:<port>}`.

## 2.5 How the data actually reaches the agent (and how it does NOT)

- **Mock overlays are never docker-cp'd into the agent.** They are bind-mounted
  `:ro` over the baked baseline inside the *mock* container
  (`/opt/mocks/<api>/<file>`), and FastAPI serves the task's overlay values.
  The agent obtains them **only by HTTP** to `<API>_API_URL`.
- A **secondary host-side mirror** is produced by
  `stage_environment_with_overlays` → `output/<backend>/<task>/data/environment/<api>/`.
  This is a byte-identical copy of what the mock containers see (baseline +
  overlays), used by the **bundler and graders**, not the agent. It is
  wipe-and-recopied per API dir, and writes an `_overlay_manifest.json` that is
  explicitly **stripped from delivery bundles** (it would leak overlay
  provenance).
- **Rubrics, ground truth, and `test_outputs.py` stay host-side and are never
  sent to the agent.** This is the no-data-leakage guarantee (see §4.2).

## 2.6 Persona and data staging (docker-cp, `src/utils/docker_utils.py`)

Unlike mock data, persona and input documents *are* copied into the agent
container:

| Function | Copies | Into | Notes |
|---|---|---|---|
| `inject_lobster_workspace` (`:2124-2147`) | `persona/.` | `task_id:/root/` | |
| `inject_persona_into_workspace` (`:2150-2178`) | `persona/.` | `task_id:/tmp_workspace/` | OpenClaw reads context from the workspace, not `/root`; **must run after `setup_workspace`**. |
| `inject_data_into_workspace` (`:2181-2230`) | `data/.` | `task_id:/tmp_workspace/home/` | |

The OpenClaw runner ordering (`src/agents/openclaw/runner.py:299-368`) is:
`inject_lobster` → `inject_persona` → `inject_data` →
`setup_skills`/`inject_api_connectors` → `run_warmup` → **workspace baseline
snapshot LAST**. That final ordering is the linchpin of Part 3: the baseline is
taken only after *every* input has been staged.

---

# PART 3 — The Filesystem Mutation Plane

The agent's deliverables are defined as *what it changes* under its workspace.
This part explains how that change set is captured.

## 3.1 The workspace and why `/root/workspace` is a symlink

`setup_workspace` (`docker_utils.py:448-489`):

- `cp -r /app/. /tmp_workspace` then `chmod u+w` — `/tmp_workspace` is the real
  working directory.
- Symlinks `/root/.openclaw/workspace → /tmp_workspace` (`:472`) and
  `/root/workspace → /tmp_workspace` (`:485-489`).

Because `/root/workspace` is a **symlink** to `/tmp_workspace` (not a copy), the
canonical deliverable location the agent writes to is the *exact same tree* that
gets baselined, diffed, and collected. A copy would drift out of sync; a symlink
cannot.

Constants (`:17-18`):
- `TMP_WORKSPACE` = `$TMP_WORKSPACE` or `/tmp_workspace`.
- `WORKSPACE_BASELINE_PATH` = `/tmp/wildclaw_workspace_baseline.json` (inside the
  container).

## 3.2 The baseline snapshot (`snapshot_workspace_state`, `:1890-1921`)

Runs a `python3` heredoc via `docker exec`. It walks `root.rglob('*')`, and for
each file or symlink records, keyed by posix relpath:

```python
files[rel] = {
    "size":     stat.st_size,
    "mtime_ns": stat.st_mtime_ns,
    "is_symlink": path.is_symlink(),
}
```

then `json.dumps(files, sort_keys=True)` into `WORKSPACE_BASELINE_PATH` **inside
the container**. There is **no content hash** — the baseline is pure metadata
(size + mtime + symlink flag).

Callers (each taken *after* all input staging, *before* the agent runs):
- `src/agents/openclaw/runner.py:387`
- `src/agents/codex/runner.py:186`
- `src/agents/claudecode/runner.py:156`

> **`hermesagent` does not snapshot**, so its `artifacts/` is silently empty by
> design (documented in `NOMENCLATURE.md:38`).

## 3.3 The diff (`_copy_changed_workspace_outputs_from_container`, `:2021-2121`)

A `python3` heredoc runs **in the container** to compute the changed set, then a
host-side loop copies survivors out.

**Exclude list (verbatim, `:2034-2038`).** A relpath is dropped if it is in
`excluded_names` or starts with one of `excluded_prefixes`:

```
results/  gt/  tmp/  .git/  node_modules/  .venv/  venv/  __pycache__/  .cache/
```

(These are scratch, dependency, and host-only directories that must never enter
a deliverable.)

**Change test (`:2056`).** A path is "changed" when:

```python
before.get(rel) != {size, mtime_ns, is_symlink}   # of current file
```

This captures **new and modified** files. **Deletions are ignored** (a deleted
baseline file simply isn't in the current tree). Output is
`print(json.dumps(sorted(changed)))`. A **missing baseline prints `'[]'`**
(graceful — no baseline ⇒ no attributed changes).

**Host-side survivor loop (`:2061-2121`).** For the changed set:

1. Build `drop = exclude ∪ _HARNESS_BOOKKEEPING`.
2. If `.wildclaw_spawn_steering.md` is present in the diff, also drop
   `AGENTS.md` (the harness rewrote it for native-spawn steering — not agent
   work).
3. For each remaining path:
   - **Traversal guard** — reject absolute paths or `..`.
   - **Injected-payload byte check** — if this path was written by the injector
     (see §3.4), sha256-compare the container file to the injector's source: if
     **identical**, withhold it (harness wrote it, not the agent); if the agent
     **edited** it after the drop, **keep** it.
   - Otherwise `docker cp task_id:/tmp_workspace/<rel> dest/<rel>`.

## 3.4 Separating harness noise from agent work

Two mechanisms distinguish files the *harness* placed after the baseline from
genuine agent output.

**Harness bookkeeping (`_HARNESS_BOOKKEEPING`, `:1928-1932`):**

```python
{".wildclaw_current_turn", ".wildclaw_spawn_steering.md", "spawn_tree.jsonl"}
```

Note persona `.md` files are **deliberately not** in this set — an agent editing
`MEMORY.md` is real work and must not be hidden.

**Injected payloads (`_injected_payloads`, `:1939-1977`):** reads
`inject_timeline.jsonl`, selecting records with `type == "inject.fs"` and a
truthy `ok`, mapping each `dst → src` via `_map_workspace_dst`. `_same_bytes`
(`:1994-2007`) sha256-compares; on any read error it returns `False`, which the
caller treats as "cannot tell → fall back to exclusion" (never keep an
unverified file as agent work). This is what corrects for mid-run injector drops
(and `copy_file_into_workspace`, `:156-203`, tri-state
`None`/`False`/`True` drops) that land *after* the baseline.

**`artifacts_excluded.json` (`:1879-1887`):** whenever anything is withheld, this
manifest is written so an empty/thin `artifacts/` is *diagnosable*:

```json
{
  "note": "Changed under the agent's workspace but NOT agent-produced; still present in workspace_full/.",
  "injected_by_harness": [ ... ],
  "harness_bookkeeping":  [ ... ]
}
```

## 3.5 Collecting output (`collect_output_from_container`, `:1812-1887`)

Order of operations (verified against source, `:1812-1881`):

1. `docker cp /tmp/openclaw/.` → `task_output/` (`:1844`) — agent session logs.
2. `docker cp /root/.openclaw/agents/main/sessions/.` → `sessions/`
   (`:1851-1856`) — the native multi-agent session store (parent + spawned
   children), which would otherwise be lost on teardown.
3. `_sweep_root_deliverables_to_workspace` (`:1858`) — sweep deliverable-shaped
   files an agent left at top-level `/root/` back into `/tmp_workspace/`.
4. `docker cp /tmp_workspace/.` → **`workspace_full/`** (`:1868-1871`) — the
   complete forensic copy of the workspace.
5. `_copy_changed_workspace_outputs_from_container` → **`artifacts/`**
   (`:1873-1878`) — the baseline-diff, agent-attributed set.
6. Write `artifacts_excluded.json` (`:1880`) if anything was withheld.

> Note: an alternate `include_workspace_changes=True` mode (`:1860-1866`) copies
> the raw workspace into `task_output/workspace/` and returns early *without*
> computing the baseline-diff; the default path above (`include_workspace_changes
> = False`) is what produces `artifacts/` + `workspace_full/`.

The copy helpers `_copy_dir` / `_copy_file_from_container` (`:2233-2254`) are
plain `docker cp` shellouts (no Docker SDK). `run_batch.py` wraps this as
`collect_task_output` (`:490-511`, passing
`inject_timeline = output_dir/inject_timeline.jsonl`), invoked in the main
pipeline at `:2324-2331`.

### `artifacts/` vs. `workspace_full/`

| Dir | Contents | Used by |
|---|---|---|
| `artifacts/` | **Only what the agent produced** (baseline-diff, minus excludes/harness/injected). | Judges & bundle — the *deliverable*. |
| `workspace_full/` | The entire `/tmp_workspace`. | **Forensic only** — fallback and debugging. |

`_pick_evidence_dir` (`:1672-1681`) chooses `artifacts/` when it exists and is
non-empty, else falls back to `workspace_full/`. The grader
(`grading.py:409`) and S3 uploader (`s3_artifacts.py:33`) share
`_DELIVERABLE_DIR_NAMES = ('results', 'deliverables', 'output', 'out',
'artifacts')` — a dual invariant that must stay in sync.
`s3_artifacts.py:66-69` additionally blocklists template filenames
(`_TEMPLATE_FILE_NAMES`: IDENTITY/BOOTSTRAP/HEARTBEAT/USER/SOUL/AGENTS/TOOLS/
AGENT/MEMORY.md). `upload_output_artifacts` (`:76-179`) uploads to
`s3://<bucket>/<prefix>/output/tasks/<task_id>/<file>`.

## 3.6 Publishing to a delivery bundle

Two byte-equal implementations exist:

- **`src/utils/harbor/bundle.py::write_bundle`** — the library path, with a
  `_KEEP_TOP_LEVEL` allow-list (`:49-50`).
- **`script/repackage_to_bundle.py::convert_task`** — a standalone, stdlib-only
  path used by `deliver.sh`.

Canonical file naming in the bundle:

- **`PROMPT.md`** — sources tried in order (`PROMPT_SOURCE_CANDIDATES`):
  `prompts.json` → `PROMPT.md` → `prompt.txt` → `prompts.txt`. `prompts.json` is
  *rendered*, not byte-copied.
- **`TRUTH.md`** (`GROUND_TRUTH_FILENAME`) — sources: `TRUTH.md` → `GTFA.md` →
  `golden_steer_flow.md` → `ground-truth.md`.

The **current** publish path is a **verbatim `shutil.copy2`** (`:2310-2330`)
regardless of source. The ground-truth **slicer**
`extract_ground_truth_sections` (`:653-693`) is retained for callers and tests
but is **not** on the publish path. Its locked section allow-list
(`GROUND_TRUTH_SECTION_ALIASES`, `:222-229`) is `Focal Event`,
`Canonical Solve Path`, `Value Lock`. Author-private sections
(`Fairness Ledger`, `Signal Set Declaration`, `Poison-Pill Record`,
`Task.py Authoring Notes`, `Phase-2 Fingerprint`) **must never** be widened into
the slice.

**Bundle media reads `artifacts/` only.** `copy_output_media` (`:1070-1090`)
reads only `run_dir/task_output/artifacts/` — `workspace_full/` is **never** read
for a bundle. (Companions: `copy_verifier_logs` `:1005-1030`,
`copy_subagent_artifacts` `:973-1002`, `copy_snapshot` `:1033-1046`; assembly in
`convert_task` `:2270-2399`.) `final_reward` in bundles is a **percentage
(0-100)**, while `score.json.combined_reward` stays `0-1`.

### Media fork (`src/utils/trajectory/`)

Inline base64 media in a trajectory is externalized by **exactly one** of:

- `local_media.py::replace_inline_media_with_files` — extracts base64 to
  `out_dir/media/<hash>.<ext>` with a bundle/local path (dev).
- `s3_media.py::replace_inline_media_with_s3` — uploads to S3 (production).

Pick one; never mix. `run_batch.py::_build_trajectory` (~`:1210`) calls the local
variant with `artifacts_dir`.

---

# PART 4 — Guarantees

## 4.1 Why an empty `artifacts/` is a real signal

An empty `artifacts/` directory means **the agent wrote nothing under
`/root/workspace/`** — it is *not* a harness failure. This holds because:

1. The **baseline is taken after all input staging**, so pre-existing inputs
   never count as agent output.
2. The diff is metadata-based (size + mtime_ns + symlink), catching any real new
   or modified file.
3. Scratch/dependency/host-only dirs are excluded by a fixed list.
4. Injector drops are subtracted via byte-identity comparison.
5. Harness bookkeeping files are filtered by name.
6. `artifacts_excluded.json` records exactly what was withheld and why.
7. `workspace_full/` remains as a forensic fallback if you need to inspect the
   whole tree.
8. Judges read `artifacts/` first (falling back to `workspace_full/`).

So an empty `artifacts/` after all of that is a genuine "no deliverable"
verdict.

## 4.2 The no-data-leakage guarantee

The agent's only channel to task data is **HTTP to the mock APIs**. It never
receives:

- mock **seed files** (bind-mounted into the *mock* container, not the agent),
- `rubric.json`, `test_outputs.py` / `test_weights.json`,
- `golden_steer_flow.md` / any ground-truth,
- the host-side `data/environment/` mirror or its `_overlay_manifest.json`.

Ground-truth values live only inside the mock's responses, so the agent must
*earn* them by calling the API — exactly what the benchmark measures.

---

# Appendix — file:line cross-reference

### Mock API plane (`environment/`, `src/utils/mock_stack.py`)

| Symbol / behavior | Location |
|---|---|
| In-memory `Table` / `Document` / `Store` / `get_store` | `environment/_mutable_store.py` |
| CSV-overlay-wins invariant (`read_seed_with_ctx`) | `_mutable_store.py` (~`:234-273`, invariant `:235`) |
| Audit plane `install_tracker`, `/audit/*` | `environment/tracking_middleware.py` |
| Admin/drift plane `install_admin_plane`, `/admin/*` | `environment/admin_plane.py` |
| Import gate over all 101 services | `environment/smoke_eager_load.py` |
| Migration recipe | `environment/MIGRATION_RECIPE.md` |
| `MOCK_IMAGE = "kensei3-mocks:v1"` | `mock_stack.py:17` |
| Content-hash label / builder version | `mock_stack.py:19`, `:25` |
| `_compute_mock_content_hash` | `mock_stack.py:28-50` |
| supervisord generator (`MOCK_ENABLED_APIS`) | `mock_stack.py:155-194` |
| Dockerfile generator (digest-pinned `python:3.11-slim`, non-root `app`, `--require-hashes`) | `mock_stack.py:215-265` (base `:244`, USER `:263`, hash install `:250-253`) |
| `build_mock_image_if_needed` | `mock_stack.py:296-366` |
| `start_mock_stack` (overlays, resilient load, dual-homing) | `mock_stack.py:369-450` |
| `get_published_ports` | `mock_stack.py:453+` |

### Data-injection path (`eval/run_batch.py`, `src/utils/`)

| Symbol / behavior | Location |
|---|---|
| `_augment_task_with_mocks` | `run_batch.py:743-786` |
| `_resolve_task_apis` (precedence, overlays) | `run_batch.py:605` (overlays `:656-668`) |
| `infer_required_apis` keyword inference | `run_batch.py:645-650` |
| `_normalize_api_name` | `run_batch.py:596-602` |
| Shared/base mock stack bring-up | `run_batch.py:3221-3247` |
| Per-task mock stack `_start_task_mock_stack` | `run_batch.py:3269` (invoked `:1849-1857`) |
| Native task parse / workspace hint | `task_parser.py:680-731` (hint `:716`) |
| Host-side env mirror `stage_environment_with_overlays` | `src/utils/env_overlay_snapshot.py` |
| Persona/data docker-cp | `docker_utils.py:2124-2147`, `:2150-2178`, `:2181-2230` |
| Runner staging order (baseline last) | `src/agents/openclaw/runner.py:299-368`, snapshot `:387` |

### Filesystem mutation plane (`src/utils/docker_utils.py`)

| Symbol / behavior | Location |
|---|---|
| `TMP_WORKSPACE`, `WORKSPACE_BASELINE_PATH` | `:17-18` |
| `setup_workspace` (symlinks `/root/workspace`) | `:448-489` (symlinks `:472`, `:485-489`) |
| `copy_file_into_workspace` (mid-run drop) | `:156-203` |
| `snapshot_workspace_state` (baseline) | `:1890-1921` |
| baseline callers | openclaw `:387`, codex `:186`, claudecode `:156` |
| `_HARNESS_BOOKKEEPING` | `:1928-1932` |
| `_injected_payloads` / `_injected_paths` | `:1939-1977` / `:1980-1991` |
| `_same_bytes` | `:1994-2007` |
| `_copy_changed_workspace_outputs_from_container` (diff) | `:2021-2121` (excludes `:2034-2038`, change test `:2056`) |
| `collect_output_from_container` | `:1812-1887` (`workspace_full` `:1868`, `artifacts` `:1873`) |
| `artifacts_excluded.json` | `:1879-1887` |
| `_pick_evidence_dir` | `:1672-1681` |
| `_copy_dir` / `_copy_file_from_container` | `:2233-2254` |
| `collect_task_output` wrapper | `run_batch.py:490-511` (invoked `:2324-2331`) |

### Grading / delivery

| Symbol / behavior | Location |
|---|---|
| `_DELIVERABLE_DIR_NAMES` (grader) | `grading.py:409` |
| `_DELIVERABLE_DIR_NAMES` / `_TEMPLATE_FILE_NAMES` / upload | `s3_artifacts.py:33`, `:66-69`, `:76-179` |
| Harbor bundle `write_bundle` (`_KEEP_TOP_LEVEL`) | `src/utils/harbor/bundle.py:49-50` |
| Standalone bundler `convert_task` | `script/repackage_to_bundle.py:2270-2399` |
| `PROMPT.md` sources | `repackage_to_bundle.py:194-200` |
| `TRUTH.md` sources / filename | `repackage_to_bundle.py:187`, `:210-215` |
| Verbatim publish copy | `repackage_to_bundle.py:2310-2330` |
| Ground-truth slicer + locked aliases | `repackage_to_bundle.py:653-693`, aliases `:222-229` |
| Bundle media reads `artifacts/` only | `repackage_to_bundle.py:1070-1090` |
| Media fork (local vs S3) | `src/utils/trajectory/local_media.py`, `s3_media.py` |

> Line numbers are anchored to source as read during authoring. If a symbol has
> moved, search by name — the *names* and *behaviors* are the stable contract;
> the exact line is a convenience.
