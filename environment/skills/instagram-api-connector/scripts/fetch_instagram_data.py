#!/usr/bin/env python3
"""CLI helper for reading Instagram account data — media, comments, insights, stories, and hashtags."""

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
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Query an Instagram Graph API service")
    parser.add_argument("--user", metavar="USER_ID",
                        help="Fetch user/account profile")
    parser.add_argument("--media", metavar="USER_ID",
                        help="List media posts for an account")
    parser.add_argument("--get-media", metavar="MEDIA_ID",
                        help="Fetch details for a specific media post")
    parser.add_argument("--children", metavar="MEDIA_ID",
                        help="List carousel children for a media post")
    parser.add_argument("--comments", metavar="MEDIA_ID",
                        help="List comments on a media post")
    parser.add_argument("--comment", metavar="COMMENT_ID",
                        help="Fetch a single comment by ID")
    parser.add_argument("--replies", metavar="COMMENT_ID",
                        help="List replies to a comment")
    parser.add_argument("--stories", metavar="USER_ID",
                        help="List current stories for an account")
    parser.add_argument("--story", metavar="STORY_ID",
                        help="Fetch a single story by ID")
    parser.add_argument("--user-insights", metavar="USER_ID",
                        help="Fetch account-level insights")
    parser.add_argument("--media-insights", metavar="MEDIA_ID",
                        help="Fetch insights for a specific media post")
    parser.add_argument("--hashtag-search", metavar="QUERY",
                        help="Search for hashtags by name")
    parser.add_argument("--hashtag", metavar="HASHTAG_ID",
                        help="Get hashtag details by ID")
    parser.add_argument("--hashtag-media", metavar="HASHTAG_ID",
                        help="Get recent media for a hashtag (requires --user-id)")
    parser.add_argument("--mentions", metavar="USER_ID",
                        help="List media where user is tagged/mentioned")
    parser.add_argument("--user-id", metavar="USER_ID",
                        help="User ID (used with --hashtag-media)")
    parser.add_argument("--media-type",
                        help="Filter media by type: IMAGE, VIDEO, CAROUSEL_ALBUM")
    parser.add_argument("--metric",
                        help="Comma-separated metrics for insights")
    parser.add_argument("--fields",
                        help="Comma-separated fields to return")
    parser.add_argument("--limit", type=int,
                        help="Maximum results to return")

    args = parser.parse_args()
    base_url = os.environ.get("INSTAGRAM_API_URL", "http://localhost:8003")

    try:
        if args.user:
            params = {}
            if args.fields:
                params["fields"] = args.fields
            print_json(api_get(base_url, f"/{args.user}", params or None))
            return

        if args.media:
            params = {}
            if args.media_type:
                params["media_type"] = args.media_type
            if args.fields:
                params["fields"] = args.fields
            if args.limit:
                params["limit"] = str(args.limit)
            print_json(api_get(base_url, f"/{args.media}/media", params or None))
            return

        if args.get_media:
            params = {}
            if args.fields:
                params["fields"] = args.fields
            print_json(api_get(base_url, f"/media/{args.get_media}", params or None))
            return

        if args.children:
            print_json(api_get(base_url, f"/media/{args.children}/children"))
            return

        if args.comments:
            params = {}
            if args.limit:
                params["limit"] = str(args.limit)
            print_json(api_get(base_url, f"/media/{args.comments}/comments", params or None))
            return

        if args.comment:
            print_json(api_get(base_url, f"/comment/{args.comment}"))
            return

        if args.replies:
            print_json(api_get(base_url, f"/comment/{args.replies}/replies"))
            return

        if args.stories:
            print_json(api_get(base_url, f"/{args.stories}/stories"))
            return

        if args.story:
            print_json(api_get(base_url, f"/stories/{args.story}"))
            return

        if args.user_insights:
            params = {}
            if args.metric:
                params["metric"] = args.metric
            print_json(api_get(base_url, f"/{args.user_insights}/insights", params or None))
            return

        if args.media_insights:
            params = {}
            if args.metric:
                params["metric"] = args.metric
            print_json(api_get(base_url, f"/media/{args.media_insights}/insights", params or None))
            return

        if args.hashtag_search:
            print_json(api_get(base_url, "/ig_hashtag_search", {"q": args.hashtag_search}))
            return

        if args.hashtag:
            print_json(api_get(base_url, f"/hashtag/{args.hashtag}"))
            return

        if args.hashtag_media:
            user_id = args.user_id or "17841400123456789"
            params = {"user_id": user_id}
            if args.limit:
                params["limit"] = str(args.limit)
            print_json(api_get(base_url, f"/hashtag/{args.hashtag_media}/recent_media", params))
            return

        if args.mentions:
            params = {}
            if args.limit:
                params["limit"] = str(args.limit)
            print_json(api_get(base_url, f"/{args.mentions}/tags", params or None))
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
