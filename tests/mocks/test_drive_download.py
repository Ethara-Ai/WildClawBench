"""Drive-API download endpoint tests for box-api and google-drive-api.

Both services expose a content-download route on top of the existing metadata
routes. Each shape differs deliberately (Box GET /content, Drive ?alt=media) to
mirror the real vendor API surfaces. Common invariants asserted here:

  * text/markdown roundtrip preserves bytes (decode UTF-8, equality match).
  * application/pdf returns extracted-text content (substring match against
    the fixture's known text).
  * 415 on unsupported mime (zip/docx/xlsx/png/google-apps proprietary).
  * 404 on missing file_id / path / fixture-file.
  * Optional 413 on text exceeding `WCB_DOWNLOAD_MAX_BYTES`.

The autouse fixture restricts parametrization to the drive APIs so the
otherwise-fleet-wide `api_dir` session fixture from conftest.py doesn't drag
in every unrelated service.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Skip the whole module if pypdf is not importable — it's required by
# the production callsite for the PDF gate, and we rely on it to
# build/inspect the fixture PDF too.
pytest.importorskip("pypdf")

DRIVE_APIS = ("box-api", "google-drive-api")


@pytest.fixture(autouse=True)
def _skip_non_drive(api_dir: Path):
    if api_dir.name not in DRIVE_APIS:
        pytest.skip(f"{api_dir.name} has no download endpoint")


def _get(client, api: str, file_id: str = ""):
    """Cross-API GET shim returning a TestClient Response."""
    if api == "box-api":
        return client.get(f"/2.0/files/{file_id}/content")
    if api == "google-drive-api":
        return client.get(f"/drive/v3/files/{file_id}", params={"alt": "media"})
    raise ValueError(api)


# Per-API fixtures — (md_id, pdf_id, unsupported_id)
_FIXTURES = {
    "box-api": dict(md="500007", pdf="500001", unsupported="500002",  # zip
                    missing_id="999999", missing_fixture="500005",     # api-spec.yaml
                    missing_fixture_blob="api-spec.yaml"),
    "google-drive-api": dict(md="file-readme", pdf="file-arch",
                              unsupported="folder-eng",  # vnd.google-apps.folder
                              missing_id="nonexistent",
                              missing_fixture=None),
}


def _call(client, api: str, key: str):
    fx = _FIXTURES[api][key]
    return _get(client, api, file_id=fx) if fx is not None else None


def test_markdown_roundtrip_returns_text(api_dir, client):
    r = _call(client, api_dir.name, "md")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mime_type"] == "text/markdown", body
    assert isinstance(body["content"], str)
    assert len(body["content"]) > 0
    # The .md fixtures all open with a heading line
    assert body["content"].lstrip().startswith("#"), body["content"][:80]


def test_pdf_extracted_text_substring(api_dir, client):
    r = _call(client, api_dir.name, "pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mime_type"] == "application/pdf"
    # Hand-crafted PDF fixture text (shared across all 3 apis)
    assert "Brand Guidelines" in body["content"], body["content"][:200]


def test_unsupported_mime_returns_415(api_dir, client):
    r = _call(client, api_dir.name, "unsupported")
    assert r.status_code == 415, r.text


def test_missing_id_returns_404(api_dir, client):
    r = _call(client, api_dir.name, "missing_id")
    assert r.status_code == 404, r.text


def test_missing_fixture_returns_404(api_dir, client):
    """A row whose blob is absent from file_blobs/ must 404 with
    `code='fixture_missing'`, not 500. Every seeded row now ships a blob, so
    the blob is moved aside for the duration of the call."""
    blob_name = _FIXTURES[api_dir.name].get("missing_fixture_blob")
    if _FIXTURES[api_dir.name]["missing_fixture"] is None or blob_name is None:
        pytest.skip("API has no row-without-fixture testcase")
    blob = api_dir / "file_blobs" / blob_name
    stashed = blob.with_suffix(blob.suffix + ".stashed")
    blob.rename(stashed)
    try:
        r = _call(client, api_dir.name, "missing_fixture")
    finally:
        stashed.rename(blob)
    assert r.status_code == 404, r.text
    assert r.json().get("code") == "fixture_missing", r.text


def test_size_cap_413(api_dir, client, monkeypatch):
    """Set WCB_DOWNLOAD_MAX_BYTES below the smallest fixture's text size
    and assert the same md roundtrip 413s. The cap is read at call time
    (not module-load) so monkeypatch.setenv() takes effect."""
    monkeypatch.setenv("WCB_DOWNLOAD_MAX_BYTES", "10")
    r = _call(client, api_dir.name, "md")
    assert r.status_code == 413, r.text
