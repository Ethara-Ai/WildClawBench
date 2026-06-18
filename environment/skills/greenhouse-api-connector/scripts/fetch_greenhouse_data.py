#!/usr/bin/env python3
"""CLI helper for the Greenhouse Harvest API (Mock) mock API.

Generated read/write helper: one flag per endpoint. Base URL comes from
$GREENHOUSE_API_URL (override with --url). POST/PUT/PATCH bodies are read from --data
(JSON string) or --data-file; DELETE/GET take only path params.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _fill(path, values):
    """Substitute {placeholders} in path order with the provided positional values."""
    import re as _re
    it = iter(values or [])
    return _re.sub(r"\{[^}]+\}", lambda _m: urllib.parse.quote(str(next(it, "")), safe=""), path)

# --- SSRF guard for connector scripts (AUDIT_TRIAGE.md S-002). Inlined per-file
# because the connector skills directory is sandboxed and forbids cross-script
# imports. Rejects non-http(s) schemes, link-local hosts (kills AWS/GCP/Azure
# IMDS at 169.254.169.254), multicast/reserved/unspecified addresses, and
# re-validates every redirect target. Loopback + RFC1918 are intentionally
# allowed because the legitimate mock-API URLs target localhost or a
# docker-internal host. ---
import ipaddress as _ipaddress
import socket as _socket


class _SsrfBlocked(ValueError):
    pass


def _ssrf_check_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise _SsrfBlocked(f"SSRF guard: scheme {parsed.scheme!r} not allowed (url={url!r})")
    host = parsed.hostname
    if not host:
        raise _SsrfBlocked(f"SSRF guard: missing host (url={url!r})")
    try:
        infos = _socket.getaddrinfo(host, None)
    except _socket.gaierror as exc:
        raise _SsrfBlocked(f"SSRF guard: DNS resolution failed for {host!r}: {exc}")
    for info in infos:
        ip_text = info[4][0]
        try:
            ip_obj = _ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if ip_obj.is_loopback:
            continue
        if ip_obj.is_link_local:
            raise _SsrfBlocked(
                f"SSRF guard: host {host!r} resolves to link-local {ip_text} "
                f"(blocks cloud metadata at 169.254.169.254)"
            )
        if ip_obj.is_multicast or ip_obj.is_reserved or ip_obj.is_unspecified:
            raise _SsrfBlocked(
                f"SSRF guard: host {host!r} resolves to disallowed address {ip_text}"
            )


class _SsrfRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _ssrf_check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SSRF_OPENER = urllib.request.build_opener(_SsrfRedirectHandler)


def _safe_urlopen(req_or_url, *, timeout=30):
    url = req_or_url.full_url if isinstance(req_or_url, urllib.request.Request) else req_or_url
    _ssrf_check_url(url)
    return _SSRF_OPENER.open(req_or_url, timeout=timeout)




def _request(base, path, method, body=None):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with _safe_urlopen(req, timeout=30) as resp:
        raw = resp.read().decode()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def api_get(base, path):
    return _request(base, path, "GET")


def api_delete(base, path):
    return _request(base, path, "DELETE")


def api_send(base, path, method, body):
    return _request(base, path, method, body if body is not None else {})


def _body(args):
    if getattr(args, "data_file", None):
        with open(args.data_file, "r", encoding="utf-8") as fh:
            return json.load(fh)
    if getattr(args, "data", None):
        return json.loads(args.data)
    return {}


def show(data):
    print(json.dumps(data, indent=2, ensure_ascii=False) if not isinstance(data, str) else data)
    return 0


def main():
    p = argparse.ArgumentParser(description="Query the Greenhouse Harvest API (Mock) mock API")
    p.add_argument("--get-candidates", action="store_true", help="GET /v1/candidates")
    p.add_argument("--get-candidates-candidate-id", metavar="CANDIDATE_ID", nargs=1, help="GET /v1/candidates/{candidate_id}")
    p.add_argument("--post-candidates", action="store_true", help="POST /v1/candidates")
    p.add_argument("--get-jobs", action="store_true", help="GET /v1/jobs")
    p.add_argument("--get-jobs-job-id", metavar="JOB_ID", nargs=1, help="GET /v1/jobs/{job_id}")
    p.add_argument("--get-applications", action="store_true", help="GET /v1/applications")
    p.add_argument("--get-applications-application-id", metavar="APPLICATION_ID", nargs=1, help="GET /v1/applications/{application_id}")
    p.add_argument("--post-applications-advance-application-id", metavar="APPLICATION_ID", nargs=1, help="POST /v1/applications/{application_id}/advance")
    p.add_argument("--post-applications-reject-application-id", metavar="APPLICATION_ID", nargs=1, help="POST /v1/applications/{application_id}/reject")
    p.add_argument("--get-scorecards", action="store_true", help="GET /v1/scorecards")
    p.add_argument("--data", metavar="JSON", help="Request body as a JSON string (POST/PUT/PATCH)")
    p.add_argument("--data-file", metavar="PATH", help="Request body from a JSON file (POST/PUT/PATCH)")
    p.add_argument("--url", default=os.environ.get("GREENHOUSE_API_URL", "http://localhost:8073"),
                   help="API base URL (default: $GREENHOUSE_API_URL or http://localhost:8073)")
    args = p.parse_args()
    base = args.url.rstrip("/")

    try:
        return _dispatch(args, base)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        return 1


def _dispatch(args, base):
    if args.get_candidates:
        return show(api_get(base, "/v1/candidates"))
    if args.get_candidates_candidate_id:
        return show(api_get(base, _fill('/v1/candidates/{candidate_id}', args.get_candidates_candidate_id)))
    if args.post_candidates:
        return show(api_send(base, '/v1/candidates', 'POST', _body(args)))
    if args.get_jobs:
        return show(api_get(base, "/v1/jobs"))
    if args.get_jobs_job_id:
        return show(api_get(base, _fill('/v1/jobs/{job_id}', args.get_jobs_job_id)))
    if args.get_applications:
        return show(api_get(base, "/v1/applications"))
    if args.get_applications_application_id:
        return show(api_get(base, _fill('/v1/applications/{application_id}', args.get_applications_application_id)))
    if args.post_applications_advance_application_id:
        return show(api_send(base, _fill('/v1/applications/{application_id}/advance', args.post_applications_advance_application_id), 'POST', _body(args)))
    if args.post_applications_reject_application_id:
        return show(api_send(base, _fill('/v1/applications/{application_id}/reject', args.post_applications_reject_application_id), 'POST', _body(args)))
    if args.get_scorecards:
        return show(api_get(base, "/v1/scorecards"))
    print("No endpoint flag provided. Use -h to list available endpoints.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
