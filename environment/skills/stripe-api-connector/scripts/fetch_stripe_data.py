#!/usr/bin/env python3
"""CLI helper for the Stripe API (Mock) mock API.

Generated read/write helper: one flag per endpoint. Base URL comes from
$STRIPE_API_URL (override with --url). POST/PUT/PATCH bodies are read from --data
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
    p = argparse.ArgumentParser(description="Query the Stripe API (Mock) mock API")
    p.add_argument("--get-customers", action="store_true", help="GET /v1/customers")
    p.add_argument("--get-customers-customer-id", metavar="CUSTOMER_ID", nargs=1, help="GET /v1/customers/{customer_id}")
    p.add_argument("--post-customers", action="store_true", help="POST /v1/customers")
    p.add_argument("--get-products", action="store_true", help="GET /v1/products")
    p.add_argument("--get-prices", action="store_true", help="GET /v1/prices")
    p.add_argument("--post-payment-intents", action="store_true", help="POST /v1/payment_intents")
    p.add_argument("--get-payment-intents-pi-id", metavar="PI_ID", nargs=1, help="GET /v1/payment_intents/{pi_id}")
    p.add_argument("--get-charges", action="store_true", help="GET /v1/charges")
    p.add_argument("--get-charges-charge-id", metavar="CHARGE_ID", nargs=1, help="GET /v1/charges/{charge_id}")
    p.add_argument("--post-charges", action="store_true", help="POST /v1/charges")
    p.add_argument("--post-refunds", action="store_true", help="POST /v1/refunds")
    p.add_argument("--get-invoices", action="store_true", help="GET /v1/invoices")
    p.add_argument("--get-invoices-invoice-id", metavar="INVOICE_ID", nargs=1, help="GET /v1/invoices/{invoice_id}")
    p.add_argument("--post-invoices", action="store_true", help="POST /v1/invoices")
    p.add_argument("--get-subscriptions", action="store_true", help="GET /v1/subscriptions")
    p.add_argument("--get-subscriptions-sub-id", metavar="SUB_ID", nargs=1, help="GET /v1/subscriptions/{sub_id}")
    p.add_argument("--post-subscriptions", action="store_true", help="POST /v1/subscriptions")
    p.add_argument("--get-balance", action="store_true", help="GET /v1/balance")
    p.add_argument("--data", metavar="JSON", help="Request body as a JSON string (POST/PUT/PATCH)")
    p.add_argument("--data-file", metavar="PATH", help="Request body from a JSON file (POST/PUT/PATCH)")
    p.add_argument("--url", default=os.environ.get("STRIPE_API_URL", "http://localhost:8021"),
                   help="API base URL (default: $STRIPE_API_URL or http://localhost:8021)")
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
    if args.get_customers:
        return show(api_get(base, "/v1/customers"))
    if args.get_customers_customer_id:
        return show(api_get(base, _fill('/v1/customers/{customer_id}', args.get_customers_customer_id)))
    if args.post_customers:
        return show(api_send(base, '/v1/customers', 'POST', _body(args)))
    if args.get_products:
        return show(api_get(base, "/v1/products"))
    if args.get_prices:
        return show(api_get(base, "/v1/prices"))
    if args.post_payment_intents:
        return show(api_send(base, '/v1/payment_intents', 'POST', _body(args)))
    if args.get_payment_intents_pi_id:
        return show(api_get(base, _fill('/v1/payment_intents/{pi_id}', args.get_payment_intents_pi_id)))
    if args.get_charges:
        return show(api_get(base, "/v1/charges"))
    if args.get_charges_charge_id:
        return show(api_get(base, _fill('/v1/charges/{charge_id}', args.get_charges_charge_id)))
    if args.post_charges:
        return show(api_send(base, '/v1/charges', 'POST', _body(args)))
    if args.post_refunds:
        return show(api_send(base, '/v1/refunds', 'POST', _body(args)))
    if args.get_invoices:
        return show(api_get(base, "/v1/invoices"))
    if args.get_invoices_invoice_id:
        return show(api_get(base, _fill('/v1/invoices/{invoice_id}', args.get_invoices_invoice_id)))
    if args.post_invoices:
        return show(api_send(base, '/v1/invoices', 'POST', _body(args)))
    if args.get_subscriptions:
        return show(api_get(base, "/v1/subscriptions"))
    if args.get_subscriptions_sub_id:
        return show(api_get(base, _fill('/v1/subscriptions/{sub_id}', args.get_subscriptions_sub_id)))
    if args.post_subscriptions:
        return show(api_send(base, '/v1/subscriptions', 'POST', _body(args)))
    if args.get_balance:
        return show(api_get(base, "/v1/balance"))
    print("No endpoint flag provided. Use -h to list available endpoints.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
