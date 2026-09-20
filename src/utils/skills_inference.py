"""Required-API + distractor inference.

The catalog of available APIs is discovered DYNAMICALLY from the `environment/`
folder (every `<name>-api/` dir with a `service.toml`) — not hardcoded. Keyword
matching is derived from each API's slug, with an optional curated keyword/tag
map (`_CURATED_*`) layered on top for the flagship APIs to improve recall.

Two catalogs, deliberately distinct:

* `catalog_apis()` — DISK TRUTH. Every `service.toml`-bearing dir, no keyword
  filtering and NO curated fallback; empty when the environment dir is absent.
  This is "what can actually be mounted", and it is the set the mock image
  bakes (mirrors mock_stack.read_api_ports / docker_utils.discover_services and
  repackage_to_bundle._discover_service_catalog).
* `available_apis()` — the KEYWORD-MATCHABLE catalog, which falls back to the
  curated table when nothing is discoverable so inference still does something
  useful in a stripped checkout.

The curated `_CURATED_*` / `_GENERIC_*` tables below are recall enrichment, not
a service registry: they name flagship APIs that may be pruned from the fleet at
any time. Rather than hand-pruning the tables on every fleet change,
`infer_required_apis` filters its matches against `catalog_apis()` so the tables
can never emit a service that is not on disk.
"""

from __future__ import annotations

import random
import re
from functools import lru_cache
from pathlib import Path

# src/utils/skills_inference.py -> parents[2] == repo root
DEFAULT_ENVIRONMENT_DIR = Path(__file__).resolve().parents[2] / "environment"

# Optional curated enrichment (NOT the source of truth for which APIs exist).
# These add domain-specific terms that don't appear in the slug (e.g. greenhouse
# -> "applicant tracking", "ats") and coarse domain tags used for distractor
# selection. Every entry is re-pointed at a service in the converged 50: the ten
# the newreq convergence took away were also this module's missing-environment
# fallback catalogue, so emptying the table would have emptied that fallback.
_CURATED_KEYWORDS = {
    "bamboohr-api":         ("bamboo hr", "hris", "time off", "pto", "employee directory"),
    "greenhouse-api":       ("greenhouse", "applicant tracking", "ats", "candidate pipeline"),
    "gusto-api":            ("gusto", "payroll run", "paystub"),
    "klaviyo-api":          ("klaviyo", "email marketing", "subscriber profile"),
    "kraken-api":           ("kraken", "order book", "spot trading"),
    "microsoft-teams-api":  ("microsoft teams", "ms teams", "channel message"),
    "paypal-api":           ("paypal", "payout", "checkout order"),
    "plaid-api":            ("plaid", "bank link", "ach", "routing number"),
    "posthog-api":          ("posthog", "feature flag", "product analytics"),
    "sentry-api":           ("sentry", "stack trace", "crash report", "exception"),
}

_GENERIC_KEYWORDS = {
    "greenhouse-api":       ("candidate", "applicant", "job", "interview", "offer", "hiring"),
    "gusto-api":            ("payroll", "salary", "benefits", "contractor", "wage"),
    "bamboohr-api":         ("employee", "leave", "vacation", "headcount", "onboarding"),
    "paypal-api":           ("invoice", "payment", "refund", "capture"),
    "plaid-api":            ("bank", "account balance", "transaction", "institution"),
    "sentry-api":           ("error", "issue", "release", "regression"),
    "posthog-api":          ("event", "cohort", "funnel", "experiment"),
}

_CURATED_TAGS = {
    "bamboohr-api":         ("hr", "saas"),
    "greenhouse-api":       ("hr", "recruiting", "saas"),
    "gusto-api":            ("hr", "finance", "saas"),
    "klaviyo-api":          ("marketing", "commerce"),
    "kraken-api":           ("finance", "crypto"),
    "microsoft-teams-api":  ("communication", "productivity"),
    "paypal-api":           ("finance", "commerce"),
    "plaid-api":            ("finance", "saas"),
    "posthog-api":          ("analytics", "saas"),
    "sentry-api":           ("observability", "devtools"),
}

# Back-compat alias (nothing external relies on this, but keep it stable).
DOMAIN_TAGS = _CURATED_TAGS

# Distractor set policy: ALL discovered APIs minus the required ones. This is a
# 2026-06-02 user-mandated change from the original 4-API curated-tag selection
# (see b46, b58/m1296). Rationale: every TestNegativeWeight* guardrail should be
# exercisable on every possible reach-for-distractor, not just 4 curated picks.
# Implication: 49 connectors injected per task and harbor bundle ships all 50
# API dirs. DISTRACTOR_COUNT retained as a non-binding hint for legacy callers
# that pass `count=` explicitly; the default policy ignores it.
DISTRACTOR_COUNT = 4
_MIN_TOKEN_LEN = 3
# Slug tokens that are too generic to be reliable match keys on their own.
_TOKEN_STOPWORDS = {"api", "the", "and", "for", "app", "web", "dev", "data"}


def _env_dir(environment_dir=None) -> Path:
    return Path(environment_dir) if environment_dir else DEFAULT_ENVIRONMENT_DIR


def _slug_keywords(api: str) -> tuple[set[str], set[str]]:
    """Derive (token_keywords, phrase_keywords) from an api slug.

    e.g. 'microsoft-teams-api' -> tokens {'microsoft','teams'},
                                  phrases {'microsoft teams','microsoftteams'}
    """
    base = api[:-4] if api.endswith("-api") else api  # strip trailing '-api'
    parts = [p for p in base.split("-") if p]
    tokens = {p for p in parts if len(p) >= _MIN_TOKEN_LEN and p not in _TOKEN_STOPWORDS}
    phrases: set[str] = set()
    spaced = base.replace("-", " ")
    solid = base.replace("-", "")
    if " " in spaced:
        phrases.add(spaced)        # multi-word, specific -> safe as substring
        if solid and solid != spaced:
            phrases.add(solid)
    return tokens, phrases


def _compile_pattern(keys: set[str]) -> "re.Pattern | None":
    cleaned = {k.strip() for k in keys if k and k.strip()}
    if not cleaned:
        return None
    alts = "|".join(re.escape(k) for k in sorted(cleaned))
    return re.compile(rf"\b(?:{alts})\b")


def _compile_matchers(api: str) -> "tuple[re.Pattern | None, re.Pattern | None]":
    """Return (strong, generic) matchers for an API.

    A `strong` hit alone is enough to mark the API as required: slug tokens
    (e.g. `\\bnotion\\b`, `\\bstripe\\b`) and brand-locked curated keywords
    (`\\bquickbooks\\b`, `ring doorbell`).

    A `generic` hit alone is NOT enough: these are domain words that fire on
    common English ("post", "channel", "running", "board"). They only count
    if a second hit from the same API (strong OR generic) also matches.
    """
    tokens, phrases = _slug_keywords(api)
    strong: set[str] = set(tokens) | set(phrases) | set(_CURATED_KEYWORDS.get(api, ()))
    generic: set[str] = set(_GENERIC_KEYWORDS.get(api, ()))
    return _compile_pattern(strong), _compile_pattern(generic)


@lru_cache(maxsize=8)
def _build_catalog(env_str: str) -> "dict[str, tuple[re.Pattern | None, re.Pattern | None]]":
    env = Path(env_str)
    catalog: dict[str, tuple[re.Pattern | None, re.Pattern | None]] = {}
    if env.is_dir():
        for d in sorted(env.iterdir()):
            if not (d.is_dir() and d.name.endswith("-api") and (d / "service.toml").is_file()):
                continue
            strong, generic = _compile_matchers(d.name)
            if strong is not None or generic is not None:
                catalog[d.name] = (strong, generic)
    if not catalog:
        for api in _CURATED_KEYWORDS:
            strong, generic = _compile_matchers(api)
            if strong is not None or generic is not None:
                catalog[api] = (strong, generic)
    return catalog


def available_apis(environment_dir=None) -> list[str]:
    return sorted(_build_catalog(str(_env_dir(environment_dir))).keys())


@lru_cache(maxsize=8)
def _disk_services(env_str: str) -> tuple[str, ...]:
    env = Path(env_str)
    if not env.is_dir():
        return ()
    return tuple(sorted(
        d.name for d in env.iterdir()
        if d.is_dir() and (d / "service.toml").is_file()
    ))


def catalog_apis(environment_dir=None) -> list[str]:
    """The standardized fleet: every service that actually exists on disk.

    A service is any `<env>/<name>/service.toml`-bearing dir — the same rule the
    mock image's baked port manifest uses, so this set is exactly what the stack
    can serve. Unlike `available_apis` there is NO curated fallback and no
    keyword filtering: an absent/empty environment dir yields `[]`, which callers
    must read as "no catalog to validate against", never as "everything is
    missing".
    """
    return list(_disk_services(str(_env_dir(environment_dir))))


def infer_required_apis(prompt: str, environment_dir=None) -> list[str]:
    """Return APIs whose keywords appear in the prompt with word-boundary matching.

    Strong matches (slug + brand-locked curated terms) qualify on a single hit.
    Generic matches (domain words like "invoice", "calorie", "doorbell") require
    a second hit from the same API to qualify, eliminating the false-positive
    storm from common-English words.

    Matches are then intersected with `catalog_apis()` so the curated keyword
    tables — which outlive any given fleet composition — can never infer a
    service that has been pruned off disk. The intersection is skipped when the
    disk catalog is empty (nothing to validate against), preserving the curated
    fallback for stripped checkouts.
    """
    if not prompt:
        return []
    pl = prompt.lower()
    catalog = _build_catalog(str(_env_dir(environment_dir)))
    matched: list[str] = []
    for api, (strong, generic) in catalog.items():
        strong_hits = len(strong.findall(pl)) if strong is not None else 0
        generic_hits = len(generic.findall(pl)) if generic is not None else 0
        total_hits = strong_hits + generic_hits
        if strong_hits >= 1:
            matched.append(api)
        elif generic_hits >= 2 and total_hits >= 2:
            matched.append(api)
    on_disk = set(catalog_apis(environment_dir))
    if on_disk:
        matched = [api for api in matched if api in on_disk]
    return sorted(matched)


def compute_distractor_skills(required_apis: list[str], task_id: str,
                              count: int | None = None,
                              environment_dir=None) -> list[str]:
    """Return the standardized fleet minus the required APIs.

    The pool is `catalog_apis()` (disk truth), not `available_apis()`: a
    distractor has to be a service the stack can actually boot, and a
    keyword-less slug is still a perfectly mountable service. An absent
    environment dir therefore yields `[]` rather than the curated fallback —
    we never advertise a connector with no server behind it.

    Result is sorted (deterministic, no shuffle). `task_id` and `count` are
    accepted for backward compatibility; pass `count=N` to truncate explicitly
    or leave None for the full complement (the default policy).
    """
    required_set = set(required_apis)
    pool = [api for api in catalog_apis(environment_dir) if api not in required_set]
    if count is None or count <= 0 or count >= len(pool):
        return pool
    rng = random.Random(task_id or "wildclaw-default")
    rng.shuffle(pool)
    return sorted(pool[:count])
