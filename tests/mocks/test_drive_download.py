"""RETIRED WITH ITS SERVICES: the drive-API content-download endpoint tests.

box-api and google-drive-api were the fleet's only content-download services
(Box `GET /content`, Drive `?alt=media`) and both left in the newreq
convergence. Every invariant this module asserted -- UTF-8 byte roundtrip,
pypdf text extraction, 415 on an unsupported mime, 404 on a missing id, 404
`fixture_missing` on an absent blob, 413 over `WCB_DOWNLOAD_MAX_BYTES` -- was a
property of `_mutable_store.extract_file_content_text`, which no converged
service calls any more. The class has no subject, so it is retired rather than
re-pointed.

What is NOT acceptable is the way it retired itself: the module is parametrized
by conftest's fleet-wide `api_dir` and skipped every service not in a two-name
tuple, so losing both names turned 300 assertions into 300 silent skips that
still read as a green suite. The tripwire below is the successor. It fails the
moment the helper acquires a caller or a service ships a `file_blobs/`
directory, which is exactly when these tests need to come back -- and it names
this file so whoever trips it knows where to look.
"""
from __future__ import annotations

import re
from pathlib import Path

ENV_DIR = Path(__file__).resolve().parents[2] / "environment"

#: The blob-download helper the departed services called. Left in
#: `_mutable_store` on purpose when the fleet swapped (it is shared
#: infrastructure, not per-service code), so it is callable but uncalled.
BLOB_HELPER = "extract_file_content_text"

_HELPER_CALL = re.compile(rf"\b{BLOB_HELPER}\s*\(")


def test_no_converged_service_serves_blob_downloads():
    callers = sorted(
        p.parent.name for p in ENV_DIR.glob("*-api/*.py")
        if _HELPER_CALL.search(p.read_text(encoding="utf-8", errors="replace"))
    )
    blob_dirs = sorted(p.parent.name for p in ENV_DIR.glob("*-api/file_blobs"))
    assert not callers and not blob_dirs, (
        f"a converged service serves blob downloads again "
        f"(helper callers={callers}, file_blobs dirs={blob_dirs}). "
        f"Restore this module's download coverage for it: the retired suite is "
        f"in git history at tests/mocks/test_drive_download.py, and the "
        f"invariants to re-point are listed in this module's docstring."
    )


def test_the_blob_helper_is_still_there_to_come_back_to():
    """The tripwire above is only meaningful while the helper exists. If it is
    ever deleted, this fails and the retirement note stops describing reality.
    """
    source = (ENV_DIR / "_mutable_store.py").read_text(encoding="utf-8")
    assert f"def {BLOB_HELPER}(" in source
