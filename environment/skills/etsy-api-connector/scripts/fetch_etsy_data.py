#!/usr/bin/env python3
"""CLI helper for reading Etsy shop data — listings, receipts, reviews, and shop info."""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse

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




def api_get(base_url, path, params=None):
    url = f"{base_url}{path}"
    if params:
        filtered = {k: v for k, v in params.items() if v is not None}
        if filtered:
            url += "?" + urllib.parse.urlencode(filtered)
    req = urllib.request.Request(url)
    with _safe_urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def print_json(data):
    print(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Query an Etsy Open API v3 service")
    parser.add_argument("--shop", metavar="SHOP_ID",
                        help="Fetch shop profile")
    parser.add_argument("--sections", metavar="SHOP_ID",
                        help="List shop sections")
    parser.add_argument("--listings", metavar="SHOP_ID",
                        help="List active listings for a shop")
    parser.add_argument("--listing", metavar="LISTING_ID",
                        help="Fetch details for a specific listing")
    parser.add_argument("--receipts", metavar="SHOP_ID",
                        help="List receipts/orders for a shop")
    parser.add_argument("--receipt", metavar="RECEIPT_ID",
                        help="Fetch details for a specific receipt (requires --shop-id)")
    parser.add_argument("--reviews", metavar="SHOP_ID",
                        help="List reviews for a shop")
    parser.add_argument("--listing-reviews", metavar="LISTING_ID",
                        help="List reviews for a specific listing")
    parser.add_argument("--images", metavar="LISTING_ID",
                        help="List images for a listing")
    parser.add_argument("--shipping-profiles", metavar="SHOP_ID",
                        help="List shipping profiles")
    parser.add_argument("--shop-id", metavar="SHOP_ID",
                        help="Shop ID (used with --receipt)")
    parser.add_argument("--state",
                        help="Filter listings by state (active, draft, sold_out, expired)")
    parser.add_argument("--status",
                        help="Filter receipts by status (paid, shipped, completed, cancelled)")
    parser.add_argument("--q", metavar="QUERY",
                        help="Search query for listings")
    parser.add_argument("--limit", type=int,
                        help="Maximum results to return")

    args = parser.parse_args()
    base_url = os.environ.get("ETSY_API_URL", "http://localhost:8001")

    try:
        if args.shop:
            print_json(api_get(base_url, f"/v3/application/shops/{args.shop}"))
            return

        if args.sections:
            print_json(api_get(base_url, f"/v3/application/shops/{args.sections}/sections"))
            return

        if args.listings:
            params = {}
            if args.state:
                params["state"] = args.state
            if args.q:
                params["q"] = args.q
            if args.limit:
                params["limit"] = str(args.limit)
            print_json(api_get(base_url, f"/v3/application/shops/{args.listings}/listings", params or None))
            return

        if args.listing:
            print_json(api_get(base_url, f"/v3/application/listings/{args.listing}"))
            return

        if args.receipts:
            params = {}
            if args.status:
                params["status"] = args.status
            if args.limit:
                params["limit"] = str(args.limit)
            print_json(api_get(base_url, f"/v3/application/shops/{args.receipts}/receipts", params or None))
            return

        if args.receipt:
            shop_id = args.shop_id or "29457183"
            print_json(api_get(base_url, f"/v3/application/shops/{shop_id}/receipts/{args.receipt}"))
            return

        if args.reviews:
            params = {}
            if args.limit:
                params["limit"] = str(args.limit)
            print_json(api_get(base_url, f"/v3/application/shops/{args.reviews}/reviews", params or None))
            return

        if args.listing_reviews:
            print_json(api_get(base_url, f"/v3/application/listings/{args.listing_reviews}/reviews"))
            return

        if args.images:
            print_json(api_get(base_url, f"/v3/application/listings/{args.images}/images"))
            return

        if args.shipping_profiles:
            print_json(api_get(base_url, f"/v3/application/shops/{args.shipping_profiles}/shipping-profiles"))
            return

        parser.print_help()

    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"Connection error: {exc.reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
