# tests — PYTEST SUITE

Two layers: harness invariants (top-level) + parametrized mock-API integrity (`mocks/`).

## LAYOUT
```
test_drift_plane_smoke.py        # drift/admin plane smoke
test_judge_budget_invariant.py   # grading judge evidence-budget invariant
mocks/
  conftest.py    # session fixtures: discovers every environment/<api>, loads its app via TestClient
  _helpers.py    # discover_api_dirs / load_app / read_service_toml / harvest_ids / list_routes
  test_smoke.py  test_data_integrity.py  test_uniqueness.py
```

## CONVENTIONS
- `mocks/` is **parametrized over all 101 APIs**: `api_dir` fixture params = `discover_api_dirs()`,
  ids = api folder names. Each API gets its own `xdist_group` so parallel runs don't share state.
- `pytest.importorskip("fastapi"/"httpx")` guards — mock tests skip if web deps absent.
- Apps loaded via FastAPI `TestClient` from each `environment/<api>/server.py` (no live containers).

## ANTI-PATTERNS
- Don't hardcode an API list — rely on `discover_api_dirs()` so new `environment/*-api/` dirs
  are auto-covered.
- Don't assume container networking here; these are in-process TestClient tests.
