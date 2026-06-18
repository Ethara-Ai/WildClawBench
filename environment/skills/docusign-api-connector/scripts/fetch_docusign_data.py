#!/usr/bin/env python3
"""CLI helper for the DocuSign eSignature API (Mock) mock API.

Generated read/write helper: one flag per endpoint. Base URL comes from
$DOCUSIGN_API_URL (override with --url). POST/PUT/PATCH bodies are read from --data
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
    p = argparse.ArgumentParser(description="Query the DocuSign eSignature API (Mock) mock API")
    p.add_argument("--get-restapi-v2-1-accounts-envelopes-accountid", metavar="ACCOUNTID", nargs=1, help="GET /restapi/v2.1/accounts/{accountId}/envelopes")
    p.add_argument("--post-restapi-v2-1-accounts-envelopes-accountid", metavar="ACCOUNTID", nargs=1, help="POST /restapi/v2.1/accounts/{accountId}/envelopes")
    p.add_argument("--get-restapi-v2-1-accounts-envelopes-accountid-envelopeid", metavar="ACCOUNTID/ENVELOPEID", nargs=2, help="GET /restapi/v2.1/accounts/{accountId}/envelopes/{envelopeId}")
    p.add_argument("--put-restapi-v2-1-accounts-envelopes-accountid-envelopeid", metavar="ACCOUNTID/ENVELOPEID", nargs=2, help="PUT /restapi/v2.1/accounts/{accountId}/envelopes/{envelopeId}")
    p.add_argument("--get-restapi-v2-1-accounts-envelopes-recipients-accountid-envelopeid", metavar="ACCOUNTID/ENVELOPEID", nargs=2, help="GET /restapi/v2.1/accounts/{accountId}/envelopes/{envelopeId}/recipients")
    p.add_argument("--get-restapi-v2-1-accounts-envelopes-documents-accountid-envelopeid", metavar="ACCOUNTID/ENVELOPEID", nargs=2, help="GET /restapi/v2.1/accounts/{accountId}/envelopes/{envelopeId}/documents")
    p.add_argument("--get-restapi-v2-1-accounts-templates-accountid", metavar="ACCOUNTID", nargs=1, help="GET /restapi/v2.1/accounts/{accountId}/templates")
    p.add_argument("--data", metavar="JSON", help="Request body as a JSON string (POST/PUT/PATCH)")
    p.add_argument("--data-file", metavar="PATH", help="Request body from a JSON file (POST/PUT/PATCH)")
    p.add_argument("--url", default=os.environ.get("DOCUSIGN_API_URL", "http://localhost:8053"),
                   help="API base URL (default: $DOCUSIGN_API_URL or http://localhost:8053)")
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
    if args.get_restapi_v2_1_accounts_envelopes_accountid:
        return show(api_get(base, _fill('/restapi/v2.1/accounts/{accountId}/envelopes', args.get_restapi_v2_1_accounts_envelopes_accountid)))
    if args.post_restapi_v2_1_accounts_envelopes_accountid:
        return show(api_send(base, _fill('/restapi/v2.1/accounts/{accountId}/envelopes', args.post_restapi_v2_1_accounts_envelopes_accountid), 'POST', _body(args)))
    if args.get_restapi_v2_1_accounts_envelopes_accountid_envelopeid:
        return show(api_get(base, _fill('/restapi/v2.1/accounts/{accountId}/envelopes/{envelopeId}', args.get_restapi_v2_1_accounts_envelopes_accountid_envelopeid)))
    if args.put_restapi_v2_1_accounts_envelopes_accountid_envelopeid:
        return show(api_send(base, _fill('/restapi/v2.1/accounts/{accountId}/envelopes/{envelopeId}', args.put_restapi_v2_1_accounts_envelopes_accountid_envelopeid), 'PUT', _body(args)))
    if args.get_restapi_v2_1_accounts_envelopes_recipients_accountid_envelopeid:
        return show(api_get(base, _fill('/restapi/v2.1/accounts/{accountId}/envelopes/{envelopeId}/recipients', args.get_restapi_v2_1_accounts_envelopes_recipients_accountid_envelopeid)))
    if args.get_restapi_v2_1_accounts_envelopes_documents_accountid_envelopeid:
        return show(api_get(base, _fill('/restapi/v2.1/accounts/{accountId}/envelopes/{envelopeId}/documents', args.get_restapi_v2_1_accounts_envelopes_documents_accountid_envelopeid)))
    if args.get_restapi_v2_1_accounts_templates_accountid:
        return show(api_get(base, _fill('/restapi/v2.1/accounts/{accountId}/templates', args.get_restapi_v2_1_accounts_templates_accountid)))
    print("No endpoint flag provided. Use -h to list available endpoints.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
