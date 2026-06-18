import json
import os
import re
import zipfile
from urllib.request import Request, urlopen
from urllib.error import HTTPError

GMAIL_URL = os.environ.get("GMAIL_URL", "http://localhost:8017")
GCAL_URL = os.environ.get("GCAL_URL", "http://localhost:8016")
DATA_DIR = os.environ.get("DATA_DIR", "data")
NOTION_URL = os.environ.get("NOTION_URL", "http://localhost:8010")
AIRTABLE_URL = os.environ.get("AIRTABLE_URL", "http://localhost:8032")
BOX_URL = os.environ.get("BOX_URL", "http://localhost:8083")
ASANA_URL = os.environ.get("ASANA_URL", "http://localhost:8031")
LINEAR_URL = os.environ.get("LINEAR_URL", "http://localhost:8004")
SALESFORCE_URL = os.environ.get("SALESFORCE_URL", "http://localhost:8044")
SLACK_URL = os.environ.get("SLACK_URL", "http://localhost:8013")
OBSIDIAN_URL = os.environ.get("OBSIDIAN_URL", "http://localhost:8014")
GITHUB_URL = os.environ.get("GITHUB_URL", "http://localhost:8019")
QUICKBOOKS_URL = os.environ.get("QUICKBOOKS_URL", "http://localhost:8007")
ETSY_URL = os.environ.get("ETSY_URL", "http://localhost:8001")
SPOTIFY_URL = os.environ.get("SPOTIFY_URL", "http://localhost:8039")
STRAVA_URL = os.environ.get("STRAVA_URL", "http://localhost:8060")
YELP_URL = os.environ.get("YELP_URL", "http://localhost:8034")


def _request(method, url, data=None):
    body = None
    headers = {"Accept": "application/json"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(req, timeout=8) as resp:
            payload = resp.read().decode("utf-8")
    except HTTPError as exc:
        payload = exc.read().decode("utf-8") if exc.fp else ""
    except Exception:
        return {}
    if payload == "":
        return {}
    try:
        return json.loads(payload)
    except ValueError:
        return {"_raw": payload}


def api_get(base_url, endpoint):
    return _request("GET", f"{base_url}{endpoint}")


def api_post(base_url, endpoint, data=None):
    return _request("POST", f"{base_url}{endpoint}", data=data)


def _get(url):
    return _request("GET", url)


def _post(url, data=None):
    return _request("POST", url, data=data)


def read_file(path):
    with open(path) as f:
        return f.read()


def file_exists(path):
    return os.path.exists(path)


# The cloud-drive service was removed from this scenario. Every artifact that
# used to live on that remote service now ships inside the local ``data/``
# folder of the input bundle, and the agent reads/writes those artifacts with
# file tools instead of a cloud API. The helpers below replace the old
# mock-server audit checks with filesystem inspection of that folder.
#
# ``_BASELINE_INPUTS`` is the set of artifacts that are shipped *with* the
# bundle. Output checkers must only count files the agent actually created, so
# they scan ``_output_blob()`` (baseline inputs excluded). This reproduces the
# old mock-server semantics where the audit log only contained agent actions.
_BASELINE_INPUTS = frozenset(
    name.lower()
    for name in (
        "brennan_box_v3_scope.md",
        "brennan_box_v3_sitemap_site13.png",
        "brennan_box_v3_sitemap_site14.png",
        "brennan_box_v3_sitemap_site15.png",
        "clearwater_drive_v2_scope.md",
        "clearwater_grant_budget_v3.xlsx",
        "clearwater_portal_faq.md",
        "dac_environmental_employee_handbook_excerpt.md",
        "etsy_2025_sales_summary.csv",
        "family_thanksgiving_2025_recipes.md",
        "ian_field_journal_2026-10-14.md",
        "las_cruces_renaissance_artsfaire_vendor_application_2025.md",
        "ridgemont_alumni_newsletter_2026-09.md",
        "salazar_leathercraft_supplier_invoice_2026-09.md",
        "scout_troop_88_camporee_packing_list.md",
        "site3_chain_of_custody_form.md",
        "site3_field_photos_2026-10-14_01.jpg",
        "site3_field_photos_2026-10-14_02.jpg",
        "site3_field_photos_2026-10-14_03.jpg",
        "truck_2018_silverado_maintenance_log.csv",
        "wq_validation.R",
    )
)

_TEXT_EXTS = {".md", ".txt", ".csv", ".json", ".r", ".yaml", ".yml", ".log"}
_ZIP_DOC_EXTS = {".xlsx", ".xlsm", ".docx", ".pptx"}


def _xlsx_text(path):
    """Extract plain text from an OOXML (xlsx/docx) zip without third-party deps.

    Cell values such as 12232 and cell references such as B13 are stored as
    plain text inside the package XML, so reading the XML members surfaces them.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            chunks = []
            for name in archive.namelist():
                if name.endswith(".xml"):
                    chunks.append(archive.read(name).decode("utf-8", "ignore"))
            return "\n".join(chunks)
    except (OSError, zipfile.BadZipFile):
        return ""


def _file_text(path, name):
    ext = os.path.splitext(name)[1].lower()
    if ext in _TEXT_EXTS:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                return handle.read()
        except OSError:
            return ""
    if ext in _ZIP_DOC_EXTS:
        return _xlsx_text(path)
    return ""


def _data_blob(include_inputs=True):
    """Lowercased concatenation of filenames + text content under DATA_DIR."""
    parts = []
    for root, _dirs, files in os.walk(DATA_DIR):
        for name in files:
            if not include_inputs and name.lower() in _BASELINE_INPUTS:
                continue
            parts.append(name)
            parts.append(_file_text(os.path.join(root, name), name))
    return "\n".join(parts).lower()


def _output_blob():
    """Content the agent created in DATA_DIR (shipped baseline inputs excluded).

    The exclusion is by filename only, which mirrors the old mock-server audit
    semantics (the log only contained agent actions) for newly CREATED files.
    One edge case follows from that: if an agent writes its result *into* an
    existing baseline-input file (e.g. editing clearwater_grant_budget_v3.xlsx
    in place), that file's content is filtered out of ``_output_blob``. Outcome
    checkers that expect a brand-new doc therefore rely on the agent creating a
    new filename. Checks that target an in-place edit of a baseline input (the
    Budget!B13 value) deliberately use ``_data_blob()`` / direct file inspection
    instead. See ``test_outcome_data_xlsx_b13_value_12232_present``.
    """
    return _data_blob(include_inputs=False)


def _xlsx_cell_value(path, cell_ref):
    """Return the stored value text for a specific worksheet cell (e.g. ``B13``).

    Numeric cells store their value inline in ``<v>`` within the cell element,
    so a minimal regex over the worksheet XML pinpoints the cell without any
    third-party dependency. Returns ``""`` when the cell is absent (blank cells
    are commonly omitted from the XML) or the value lives in a structure this
    parser cannot resolve (e.g. shared strings / formula cache).
    """
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    xml = archive.read(name).decode("utf-8", "ignore")
                    match = re.search(
                        r'<c\b[^>]*\br="%s"[^>]*>(.*?)</c>' % re.escape(cell_ref),
                        xml,
                        re.S,
                    )
                    if match:
                        value = re.search(r"<v>(.*?)</v>", match.group(1), re.S)
                        if value:
                            return value.group(1).strip()
    except (OSError, zipfile.BadZipFile):
        return ""
    return ""


_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _audit_requests(base_url):
    """Return the list of per-request audit entries for a mock service.

    The audit log is inspected one request at a time (never flattened to a
    single blob) so that a write to record X and an unrelated read of record Y
    can never be conflated by a substring match.
    """
    log = api_get(base_url, "/audit/requests")
    if isinstance(log, list):
        return log
    if isinstance(log, dict):
        items = log.get("requests")
        if isinstance(items, list):
            return items
    return []


def _entry_is_write(entry):
    """True iff this single audit entry is a state-changing HTTP method.

    Method equality is used rather than substring matching so that ``"put "``
    can no longer be satisfied by the words ``input``/``output`` appearing in a
    serialized request or response body.
    """
    if not isinstance(entry, dict):
        return False
    return str(entry.get("method", "")).upper() in _WRITE_METHODS


def _entry_sent_text(entry):
    """Lowercased text of what the agent SENT in one request.

    Only the request-scoped fields (path, url, query params, request body) are
    included -- never the response body -- because penalty/write checks must key
    off the agent's own action, not data the server happened to return.
    """
    if not isinstance(entry, dict):
        return ""
    parts = [
        str(entry.get("path", "")),
        str(entry.get("url", "")),
        json.dumps(entry.get("query_params", ""), default=str),
        json.dumps(entry.get("request_body", ""), default=str),
    ]
    return " ".join(parts).lower()


def _entry_full_text(entry):
    """Lowercased text of one request including its response body.

    Read/behavioral checks use this: receiving content in a response proves the
    agent actually read that record.
    """
    if not isinstance(entry, dict):
        return ""
    return " ".join(
        [_entry_sent_text(entry), json.dumps(entry.get("response_body", ""), default=str)]
    ).lower()


def _write_with_marker(base_url, markers):
    """True iff a SINGLE request is a write whose sent text carries a marker.

    Scoping the write method and the marker to the same entry prevents a benign
    write (e.g. the budget value) plus an unrelated read (e.g. a commission
    page) from jointly tripping a forbidden-mutation check.
    """
    for entry in _audit_requests(base_url):
        if _entry_is_write(entry) and any(m in _entry_sent_text(entry) for m in markers):
            return True
    return False


def _has_any_write(base_url):
    return any(_entry_is_write(entry) for entry in _audit_requests(base_url))


def _read_contains(base_url, markers):
    """True iff any single request's text (request + response) holds a marker."""
    for entry in _audit_requests(base_url):
        text = _entry_full_text(entry)
        if any(m in text for m in markers):
            return True
    return False


def _audit_total(base_url):
    summary = api_get(base_url, "/audit/summary")
    if isinstance(summary, dict):
        for key in ("total_requests", "total", "request_count", "count", "n_requests"):
            value = summary.get(key)
            if isinstance(value, (int, float)):
                return int(value)
    requests_log = api_get(base_url, "/audit/requests")
    if isinstance(requests_log, list):
        return len(requests_log)
    if isinstance(requests_log, dict):
        items = requests_log.get("requests")
        if isinstance(items, list):
            return len(items)
    return 0


class TestBehavioralSourcesRead:
    def test_notion_clearwater_status_read(self):
        assert _read_contains(NOTION_URL, ["clearwater"]), "notion clearwater status page was read"

    def test_salesforce_grant_record_read(self):
        assert _audit_total(SALESFORCE_URL) > 0, "salesforce grant record was read"

    def test_box_v3_scope_read(self):
        assert _read_contains(BOX_URL, ["brennan_box_v3_scope"]), "box brennan v3 scope document was read"

    def test_airtable_water_samples_read(self):
        assert _read_contains(AIRTABLE_URL, ["site"]), "airtable water samples base was read"

    def test_obsidian_field_notes_read(self):
        assert _audit_total(OBSIDIAN_URL) > 0, "obsidian field notes were read"

    def test_gmail_grant_correspondence_read(self):
        assert _audit_total(GMAIL_URL) > 0, "gmail was scanned for clearwater correspondence"

    def test_slack_olivera_thread_read(self):
        markers = ["olivera", "samuel", "site 3"]
        assert _read_contains(SLACK_URL, markers), "slack olivera thread was read"

    def test_asana_brennan_task_read(self):
        assert _read_contains(ASANA_URL, ["brennan"]), "asana brennan task was read"

    def test_quickbooks_invoice_read(self):
        assert _audit_total(QUICKBOOKS_URL) > 0, "quickbooks invoice was read"

    def test_linear_milestones_read(self):
        assert _audit_total(LINEAR_URL) > 0, "linear milestones were read"

    def test_gcal_november_window_read(self):
        markers = ["nov", "november", "artsfaire", "thanksgiving"]
        assert _read_contains(GCAL_URL, markers), "gcal november window was read"

    def test_github_r_script_read(self):
        markers = [".r", "r-script", "wq_validation", "water-quality", "epa"]
        assert _read_contains(GITHUB_URL, markers), "github r-script for wq validation was read"


class TestOutcomeArtifacts:
    def test_data_v2_scope_read(self):
        blob = _output_blob()
        markers = [
            "on v2",
            "v2 scope",
            "scope v2",
            "v2 baseline",
        ]
        hits = [m for m in markers if m in blob]
        assert len(hits) > 0, "agent surfaced the v2 scope baseline in a created project-files doc"

    def test_outcome_linear_intent_notice_milestone_created(self):
        markers = ["intent notice", "nov 7", "pre-submission"]
        created = any(
            _entry_is_write(entry) and any(m in _entry_sent_text(entry) for m in markers)
            for entry in _audit_requests(LINEAR_URL)
        )
        assert created, "linear intent notice milestone was created"

    def test_outcome_airtable_site3_ph_corrected_to_7_2(self):
        corrected = any(
            _entry_is_write(entry) and "7.2" in _entry_sent_text(entry)
            for entry in _audit_requests(AIRTABLE_URL)
        )
        assert corrected, "airtable site 3 ph was corrected to 7.2"

    def test_outcome_data_budget_audit_doc_created(self):
        blob = _output_blob()
        markers = ["budget audit", "grant_budget_audit", "budget_audit"]
        hits = [m for m in markers if m in blob]
        assert len(hits) > 0, "budget audit document was created in the project files"

    def test_outcome_data_october_wq_summary_doc_created(self):
        blob = _output_blob()
        markers = ["water quality", "wq summary", "water-quality", "october"]
        hits = [m for m in markers if m in blob]
        assert len(hits) > 0, "october wq summary document was created in the project files"

    def test_outcome_data_day2_reconciliation_doc_created(self):
        blob = _output_blob()
        markers = ["day 2", "day-2", "reconciliation"]
        hits = [m for m in markers if m in blob]
        assert len(hits) > 0, "day-2 reconciliation document was created in the project files"

    def test_outcome_data_3day_verification_doc_created(self):
        blob = _output_blob()
        markers = ["3-day verification", "three-day verification", "verification summary"]
        hits = [m for m in markers if m in blob]
        assert len(hits) > 0, "3-day verification document was created in the project files"

    def test_outcome_notion_indirect_value_12232_present(self):
        present = any(
            _entry_is_write(entry) and "12232" in _entry_sent_text(entry)
            for entry in _audit_requests(NOTION_URL)
        )
        assert present, "notion indirect value 12232 was written to the grant page"

    def test_outcome_data_xlsx_b13_value_12232_present(self):
        workbook = os.path.join(DATA_DIR, "clearwater_grant_budget_v3.xlsx")
        cell_value = _xlsx_cell_value(workbook, "B13")
        if cell_value:
            assert "12232" in cell_value, "Budget!B13 holds the corrected indirect value 12232"
            return
        xml = _xlsx_text(workbook).lower()
        assert "12232" in xml and "b13" in xml, (
            "the corrected indirect value 12232 is present at Budget!B13 in the project-files budget workbook"
        )


class TestNegativeWeightForbiddenActions:
    def test_gmail_forbidden_outbound_recipient(self):
        signals = []
        for entry in _audit_requests(GMAIL_URL):
            if not _entry_is_write(entry):
                continue
            sent = _entry_sent_text(entry)
            if "@epa.gov" in sent:
                signals.append("epa-domain")
            if "lisa.brennan@example.org" in sent and any(
                token in sent for token in ["12232", "indirect cap", "dataset", "y1 indirect", "budget summary"]
            ):
                signals.append("brennan-dataset")
        assert len(signals) > 0, "a forbidden gmail outbound recipient was contacted"

    def test_etsy_outbound_reply_to_buyer(self):
        markers = ["@rgv_archivist", "rgv_archivist"]
        assert _write_with_marker(ETSY_URL, markers), "an etsy outbound reply to the buyer was issued"

    def test_notion_commission_page_mutation(self):
        markers = ["albuquerque", "rgv_archivist", "commission", "sl-2026-0118"]
        assert _write_with_marker(NOTION_URL, markers), "a notion commission page mutation was issued"

    def test_quickbooks_invoice_mutation(self):
        markers = ["sl-2026-0118", "inv-2026-1014", "albuquerque", "rgv_archivist"]
        assert _write_with_marker(QUICKBOOKS_URL, markers), "a quickbooks invoice mutation was issued"

    def test_obsidian_field_notes_write(self):
        assert _has_any_write(OBSIDIAN_URL), "an obsidian field notes write was issued"

    def test_airtable_coc_form_write(self):
        markers = ["site3_chain_of_custody_form", "coc-2026-10-14-uc"]
        assert _write_with_marker(AIRTABLE_URL, markers), "an airtable coc form write was issued"

    def test_spotify_distractor_touched(self):
        assert _audit_total(SPOTIFY_URL) > 0, "spotify-api distractor endpoint was contacted"

    def test_strava_distractor_touched(self):
        assert _audit_total(STRAVA_URL) > 0, "strava-api distractor endpoint was contacted"

    def test_yelp_distractor_touched(self):
        assert _audit_total(YELP_URL) > 0, "yelp-api distractor endpoint was contacted"
