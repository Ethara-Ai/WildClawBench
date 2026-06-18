#!/usr/bin/env python3
"""CLI helper for the PagerDuty API (Mock) mock API.

Generated read/write helper: one flag per endpoint. Base URL comes from
$PAGERDUTY_API_URL (override with --url). POST/PUT/PATCH bodies are read from --data
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
    p = argparse.ArgumentParser(description="Query the PagerDuty API (Mock) mock API")
    p.add_argument("--get-services", action="store_true", help="GET /services")
    p.add_argument("--get-services-service-id", metavar="SERVICE_ID", nargs=1, help="GET /services/{service_id}")
    p.add_argument("--get-incidents", action="store_true", help="GET /incidents")
    p.add_argument("--get-incidents-incident-id", metavar="INCIDENT_ID", nargs=1, help="GET /incidents/{incident_id}")
    p.add_argument("--post-incidents", action="store_true", help="POST /incidents")
    p.add_argument("--put-incidents-incident-id", metavar="INCIDENT_ID", nargs=1, help="PUT /incidents/{incident_id}")
    p.add_argument("--get-incidents-notes-incident-id", metavar="INCIDENT_ID", nargs=1, help="GET /incidents/{incident_id}/notes")
    p.add_argument("--post-incidents-notes-incident-id", metavar="INCIDENT_ID", nargs=1, help="POST /incidents/{incident_id}/notes")
    p.add_argument("--get-oncalls", action="store_true", help="GET /oncalls")
    p.add_argument("--get-schedules", action="store_true", help="GET /schedules")
    p.add_argument("--get-escalation-policies", action="store_true", help="GET /escalation_policies")
    p.add_argument("--get-users", action="store_true", help="GET /users")
    p.add_argument("--data", metavar="JSON", help="Request body as a JSON string (POST/PUT/PATCH)")
    p.add_argument("--data-file", metavar="PATH", help="Request body from a JSON file (POST/PUT/PATCH)")
    p.add_argument("--url", default=os.environ.get("PAGERDUTY_API_URL", "http://localhost:8040"),
                   help="API base URL (default: $PAGERDUTY_API_URL or http://localhost:8040)")
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
    if args.get_services:
        return show(api_get(base, "/services"))
    if args.get_services_service_id:
        return show(api_get(base, _fill('/services/{service_id}', args.get_services_service_id)))
    if args.get_incidents:
        return show(api_get(base, "/incidents"))
    if args.get_incidents_incident_id:
        return show(api_get(base, _fill('/incidents/{incident_id}', args.get_incidents_incident_id)))
    if args.post_incidents:
        return show(api_send(base, '/incidents', 'POST', _body(args)))
    if args.put_incidents_incident_id:
        return show(api_send(base, _fill('/incidents/{incident_id}', args.put_incidents_incident_id), 'PUT', _body(args)))
    if args.get_incidents_notes_incident_id:
        return show(api_get(base, _fill('/incidents/{incident_id}/notes', args.get_incidents_notes_incident_id)))
    if args.post_incidents_notes_incident_id:
        return show(api_send(base, _fill('/incidents/{incident_id}/notes', args.post_incidents_notes_incident_id), 'POST', _body(args)))
    if args.get_oncalls:
        return show(api_get(base, "/oncalls"))
    if args.get_schedules:
        return show(api_get(base, "/schedules"))
    if args.get_escalation_policies:
        return show(api_get(base, "/escalation_policies"))
    if args.get_users:
        return show(api_get(base, "/users"))
    print("No endpoint flag provided. Use -h to list available endpoints.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
