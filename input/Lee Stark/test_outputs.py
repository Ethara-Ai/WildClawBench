import json
import os
from urllib.request import Request, urlopen

GMAIL_API_URL = os.environ.get("GMAIL_API_URL", "http://localhost:8017")
GOOGLE_CALENDAR_API_URL = os.environ.get("GOOGLE_CALENDAR_API_URL", "http://localhost:8016")
NOTION_API_URL = os.environ.get("NOTION_API_URL", "http://localhost:8010")
AIRTABLE_API_URL = os.environ.get("AIRTABLE_API_URL", "http://localhost:8032")
WOOCOMMERCE_API_URL = os.environ.get("WOOCOMMERCE_API_URL", "http://localhost:8085")
ETSY_API_URL = os.environ.get("ETSY_API_URL", "http://localhost:8001")
ASANA_API_URL = os.environ.get("ASANA_API_URL", "http://localhost:8031")
PAGERDUTY_API_URL = os.environ.get("PAGERDUTY_API_URL", "http://localhost:8040")
ALPACA_API_URL = os.environ.get("ALPACA_API_URL", "http://localhost:8043")
STRAVA_API_URL = os.environ.get("STRAVA_API_URL", "http://localhost:8050")
SPOTIFY_API_URL = os.environ.get("SPOTIFY_API_URL", "http://localhost:8051")
RING_API_URL = os.environ.get("RING_API_URL", "http://localhost:8052")
ZILLOW_API_URL = os.environ.get("ZILLOW_API_URL", "http://localhost:8053")


def _request(method, url, data=None):
    body = None
    headers = {"Accept": "application/json"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, method=method, headers=headers)
    with urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_get(base_url, endpoint):
    return _request("GET", f"{base_url}{endpoint}")


# ---------------------------------------------------------------------------
# Local report-writeback resolution
#
# The Google Drive dependency has been removed. The source documents the agent
# reads now live as local working files, and the agent writes its reports back
# as local files instead of POSTing to a Drive mock. The harness provides the
# locations below; the checkers resolve the agent's generated reports from the
# output area, kept distinct from the golden reference PDFs so a checker can
# never pass merely because a shipped golden artifact is present.
# ---------------------------------------------------------------------------

# Root of the task bundle (folder containing this test file).
TASK_ROOT = os.path.dirname(os.path.abspath(__file__))

# Location of the local working files. Resolved from the harness-provided
# DATA_DIR env var, falling back to the bundle's own directory.
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(TASK_ROOT, "data"))

# Where the agent is expected to write its generated reports. Kept separate
# from the golden reference PDFs in DATA_DIR so a checker can never pass merely
# because the shipped golden artifact is present.
OUTPUT_DIR = os.environ.get("AGENT_OUTPUT_DIR", os.path.join(DATA_DIR, "agent_outputs"))


def _iter_output_files():
    """Yield (path, lowercased_text) for every readable file the agent wrote.

    Scans the agent output area recursively. Binary files that cannot be
    decoded as text contribute an empty body but still count toward existence
    checks (e.g. a generated PDF).
    """
    out = []
    if not os.path.isdir(OUTPUT_DIR):
        return out
    for root, _dirs, files in os.walk(OUTPUT_DIR):
        for name in files:
            p = os.path.join(root, name)
            try:
                with open(p, "rb") as f:
                    raw = f.read()
                try:
                    text = raw.decode("utf-8", errors="ignore").lower()
                except Exception:
                    text = ""
            except OSError:
                continue
            out.append((p, text))
    return out


def _output_files_matching(name_substrings):
    """Return agent output files whose filename contains any given substring."""
    subs = [s.lower() for s in name_substrings]
    matches = []
    for path, text in _iter_output_files():
        base = os.path.basename(path).lower()
        if any(s in base for s in subs):
            matches.append((path, text))
    return matches


def _report_has_tokens(name_substrings, required_tokens):
    """True if an agent report matching the name carries all required tokens.

    ``required_tokens`` is a list of token-groups; the report passes a group if
    ANY token in that group appears in its text (case-insensitive). Tokens are
    matched against the report's own text, or — for binary reports the agent
    may emit (e.g. a PDF) — against an accompanying sidecar/manifest file in the
    output area.
    """
    candidates = _output_files_matching(name_substrings)
    if not candidates:
        return False, "no agent report file found for " + "/".join(name_substrings)
    # Aggregate text across the matching report and any sibling text the agent
    # wrote (covers the case where the report itself is a binary PDF and the
    # content lives in a companion .md/.txt/.json the agent produced).
    aggregate = " ".join(t for _p, t in candidates)
    if not aggregate.strip():
        aggregate = " ".join(t for _p, t in _iter_output_files())
    missing = []
    for group in required_tokens:
        if not any(tok.lower() in aggregate for tok in group):
            missing.append(group)
    if missing:
        return False, "report missing token group(s): " + str(missing)
    return True, "ok"


def _summary_endpoints(base_url):
    summary = api_get(base_url, "/audit/summary")
    return summary.get("endpoints", {})


def _business_endpoints(base_url):
    endpoints = _summary_endpoints(base_url)
    return [k for k in endpoints if "/audit" not in k and "/health" not in k]


def _endpoint_count(summary, method, path_prefix):
    endpoints = summary.get("endpoints", {})
    total = 0
    key_start = f"{method.upper()} {path_prefix}"
    for key, val in endpoints.items():
        if key.startswith(key_start):
            total += val.get("count", 0) if isinstance(val, dict) else 0
    return total


_READINESS_NAMES = ["readiness"]
_SPEC_REVIEW_NAMES = ["spec_review", "spec-review", "product-spec", "product_spec", "spec review"]
_PROCUREMENT_NAMES = ["procurement", "forecast"]
_RECONCILIATION_NAMES = ["reconciliation", "reconcile"]
_VERIFICATION_NAMES = ["verification", "verify"]

_WRITEBACK_REPORTS = [
    _READINESS_NAMES,
    _SPEC_REVIEW_NAMES,
    _PROCUREMENT_NAMES,
    _RECONCILIATION_NAMES,
    _VERIFICATION_NAMES,
]


def _distinct_reports_written():
    found = []
    for names in _WRITEBACK_REPORTS:
        if _output_files_matching(names):
            found.append(names[0])
    return found


def test_drive_writeback_readiness():
    ok, detail = _report_has_tokens(
        _READINESS_NAMES,
        [["go", "hold", "no-go", "no go"]],
    )
    assert ok, f"expected a saved readiness report with a go/hold/no-go verdict: {detail}"


def test_drive_writeback_reconciliation():
    written = _distinct_reports_written()
    assert len(written) >= 4, (
        f"expected >=4 distinct saved reports by T10, got {len(written)}: {written}"
    )


def test_drive_writeback_verification():
    written = _distinct_reports_written()
    assert len(written) >= 5, (
        f"expected all 5 reports saved by T12, got {len(written)}: {written}"
    )


def test_gmail_draft_sam_created():
    summary = api_get(GMAIL_API_URL, "/audit/summary")
    count = _endpoint_count(summary, "POST", "/gmail/v1/users/me/drafts")
    assert count >= 1, f"expected >=1 POST drafts (Sam reply), got {count}"


def test_notion_mirror_procurement():
    endpoints = _summary_endpoints(NOTION_API_URL)
    mutating = 0
    for key, val in endpoints.items():
        if (key.startswith("PATCH ") or key.startswith("POST ")) and "/v1/" in key:
            mutating += val.get("count", 0) if isinstance(val, dict) else 0
    assert mutating >= 1, f"expected >=1 Notion mutating call for T6 mirror, got {mutating}"


def test_airtable_mirror_procurement():
    endpoints = _summary_endpoints(AIRTABLE_API_URL)
    mutating = 0
    for key, val in endpoints.items():
        if key.startswith("PATCH ") or key.startswith("POST "):
            mutating += val.get("count", 0) if isinstance(val, dict) else 0
    assert mutating >= 1, f"expected >=1 Airtable mutating call for T6 mirror, got {mutating}"


def test_woocommerce_products_read():
    endpoints = _summary_endpoints(WOOCOMMERCE_API_URL)
    reads = 0
    for key, val in endpoints.items():
        if key.startswith("GET ") and "products" in key:
            reads += val.get("count", 0) if isinstance(val, dict) else 0
    assert reads >= 1, f"expected >=1 GET WooCommerce products by T9, got {reads}"


def test_airtable_records_read():
    endpoints = _summary_endpoints(AIRTABLE_API_URL)
    reads = 0
    for key, val in endpoints.items():
        if key.startswith("GET "):
            reads += val.get("count", 0) if isinstance(val, dict) else 0
    assert reads >= 2, f"expected >=2 GET Airtable reads (Day 1 + T9 re-read), got {reads}"


def test_calendar_events_read():
    endpoints = _summary_endpoints(GOOGLE_CALENDAR_API_URL)
    reads = 0
    for key, val in endpoints.items():
        if key.startswith("GET ") and "events" in key:
            reads += val.get("count", 0) if isinstance(val, dict) else 0
    assert reads >= 1, f"expected >=1 GET calendar events by T11, got {reads}"


def test_etsy_listings_read():
    endpoints = _summary_endpoints(ETSY_API_URL)
    reads = 0
    for key, val in endpoints.items():
        if key.startswith("GET ") and "listings" in key:
            reads += val.get("count", 0) if isinstance(val, dict) else 0
    assert reads >= 1, f"expected >=1 GET Etsy listings, got {reads}"


def test_asana_tasks_read():
    endpoints = _summary_endpoints(ASANA_API_URL)
    reads = 0
    for key, val in endpoints.items():
        if key.startswith("GET ") and "tasks" in key:
            reads += val.get("count", 0) if isinstance(val, dict) else 0
    assert reads >= 1, f"expected >=1 GET Asana tasks by T12 verification, got {reads}"


def test_outcome_airtable_esy_count_is_52():
    data = api_get(AIRTABLE_API_URL, "/v0/appNRC25090000001/tblInventory")
    records = data.get("records", []) if isinstance(data, dict) else data
    assert isinstance(records, list), f"unexpected airtable shape: {type(records)}"
    esy = [r for r in records if "ESY-2509" in json.dumps(r)]
    assert len(esy) >= 1, "ESY-2509 row not present in airtable tblInventory"
    body = json.dumps(esy[0])
    assert "52" in body, f"airtable ESY-2509 count not 52 after SM-03: {body[:200]}"


def test_outcome_woocommerce_esy_stock_is_47():
    data = api_get(WOOCOMMERCE_API_URL, "/wp-json/wc/v3/products")
    items = data.get("products", data) if isinstance(data, dict) else data
    assert isinstance(items, list), f"unexpected woocommerce shape: {type(items)}"
    esy = [p for p in items if p.get("sku") == "ESY-2509"]
    assert len(esy) >= 1, "ESY-2509 not in WooCommerce products"
    stock = esy[0].get("stock_quantity")
    assert stock == 47, f"WooCommerce ESY-2509 stock_quantity expected 47 after SM-02, got {stock}"


def test_outcome_notion_mutations_after_t9():
    audit = api_get(NOTION_API_URL, "/audit/requests")
    mutations = [r for r in audit.get("requests", []) if r.get("method", "").upper() in ("POST", "PATCH", "PUT")]
    assert len(mutations) >= 1, f"expected >=1 Notion mutation for T9 sync, got {len(mutations)}"


def test_outcome_drive_writeback_bodies_present():
    nonempty = 0
    for names in _WRITEBACK_REPORTS:
        for _p, text in _output_files_matching(names):
            if text.strip():
                nonempty += 1
                break
    assert nonempty >= 5, (
        f"expected all 5 saved reports to carry written content, got {nonempty} non-empty"
    )


def test_outcome_gmail_sam_draft_body():
    audit = api_get(GMAIL_API_URL, "/audit/requests")
    drafts = [r for r in audit.get("requests", []) if r.get("method", "").upper() == "POST" and "drafts" in r.get("path", "")]
    assert len(drafts) >= 1, "no Gmail drafts POST recorded"
    bodies = []
    for d in drafts:
        rb = d.get("request_body")
        if isinstance(rb, str):
            bodies.append(rb.lower())
        elif isinstance(rb, dict):
            bodies.append(json.dumps(rb).lower())
    joined = " ".join(bodies)
    assert "autumn" in joined, "Sam draft body missing 'Autumn' anchor token"


def test_pagerduty_distractor_touched():
    assert len(_business_endpoints(PAGERDUTY_API_URL)) > 0, "pagerduty-api business endpoint was contacted"


def test_alpaca_distractor_touched():
    assert len(_business_endpoints(ALPACA_API_URL)) > 0, "alpaca-api business endpoint was contacted"


def test_woocommerce_product_overwritten():
    audit = api_get(WOOCOMMERCE_API_URL, "/audit/requests")
    mutations = [r for r in audit.get("requests", []) if r.get("method", "").upper() in ("POST", "PUT", "PATCH", "DELETE") and "products" in r.get("path", "")]
    assert len(mutations) >= 1, f"woocommerce product writeback present: {len(mutations)} mutating calls"


def test_etsy_listing_mutated():
    endpoints = _summary_endpoints(ETSY_API_URL)
    mutating = 0
    for key, val in endpoints.items():
        if (key.startswith("POST ") or key.startswith("PUT ") or key.startswith("PATCH ") or key.startswith("DELETE ")) and "listings" in key:
            mutating += val.get("count", 0) if isinstance(val, dict) else 0
    assert mutating >= 1, f"etsy listings mutated: {mutating}"


def test_strava_distractor_touched():
    assert len(_business_endpoints(STRAVA_API_URL)) > 0, "strava-api business endpoint was contacted"


def test_spotify_distractor_touched():
    assert len(_business_endpoints(SPOTIFY_API_URL)) > 0, "spotify-api business endpoint was contacted"


def test_ring_distractor_touched():
    assert len(_business_endpoints(RING_API_URL)) > 0, "ring-api business endpoint was contacted"


def test_zillow_distractor_touched():
    assert len(_business_endpoints(ZILLOW_API_URL)) > 0, "zillow-api business endpoint was contacted"
