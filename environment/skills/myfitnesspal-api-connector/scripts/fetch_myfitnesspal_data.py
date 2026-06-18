#!/usr/bin/env python3
"""CLI helper for reading MyFitnessPal user data — diary, foods, exercises, weight, and water."""

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
    parser = argparse.ArgumentParser(description="Query a MyFitnessPal API service")
    parser.add_argument("--profile", action="store_true",
                        help="Fetch user profile")
    parser.add_argument("--goals", action="store_true",
                        help="Fetch daily nutrition goals")
    parser.add_argument("--diary", metavar="DATE",
                        help="Get food diary for a date (YYYY-MM-DD)")
    parser.add_argument("--diary-range", nargs=2, metavar=("START", "END"),
                        help="Get diary for date range")
    parser.add_argument("--nutrition", metavar="DATE",
                        help="Get daily nutrition totals for a date")
    parser.add_argument("--weekly", metavar="END_DATE",
                        help="Get weekly nutrition summary ending on date")
    parser.add_argument("--progress", action="store_true",
                        help="Get calorie/macro progress over time")
    parser.add_argument("--foods", metavar="QUERY",
                        help="Search food database")
    parser.add_argument("--food", metavar="FOOD_ID",
                        help="Get details for a specific food")
    parser.add_argument("--exercises", action="store_true",
                        help="List exercise log")
    parser.add_argument("--exercise", metavar="EXERCISE_ID",
                        help="Get details for a specific exercise")
    parser.add_argument("--exercise-types", action="store_true",
                        help="List exercise types database")
    parser.add_argument("--weight", action="store_true",
                        help="List weight log entries")
    parser.add_argument("--water", metavar="DATE",
                        help="Get water intake for a date")
    parser.add_argument("--meal", metavar="MEAL",
                        help="Filter diary by meal (Breakfast, Lunch, Dinner, Snacks)")
    parser.add_argument("--category", metavar="CATEGORY",
                        help="Filter exercise types by category (cardio, strength, flexibility)")
    parser.add_argument("--start-date", metavar="DATE",
                        help="Filter exercises from date")
    parser.add_argument("--end-date", metavar="DATE",
                        help="Filter exercises to date")
    parser.add_argument("--days", type=int,
                        help="Number of days for progress (default 30)")
    parser.add_argument("--limit", type=int,
                        help="Maximum results to return")

    args = parser.parse_args()
    base_url = os.environ.get("MYFITNESSPAL_API_URL", "http://localhost:8005")

    try:
        if args.profile:
            print_json(api_get(base_url, "/v1/user/profile"))
            return

        if args.goals:
            print_json(api_get(base_url, "/v1/user/goals"))
            return

        if args.diary:
            params = {}
            if args.meal:
                params["meal"] = args.meal
            print_json(api_get(base_url, f"/v1/user/diary/{args.diary}", params or None))
            return

        if args.diary_range:
            params = {"start_date": args.diary_range[0], "end_date": args.diary_range[1]}
            print_json(api_get(base_url, "/v1/user/diary", params))
            return

        if args.nutrition:
            print_json(api_get(base_url, f"/v1/user/nutrition/{args.nutrition}"))
            return

        if args.weekly:
            print_json(api_get(base_url, f"/v1/user/nutrition/weekly/{args.weekly}"))
            return

        if args.progress:
            params = {}
            if args.days:
                params["days"] = str(args.days)
            print_json(api_get(base_url, "/v1/user/progress", params or None))
            return

        if args.foods:
            params = {"q": args.foods}
            if args.limit:
                params["limit"] = str(args.limit)
            print_json(api_get(base_url, "/v1/foods/search", params))
            return

        if args.food:
            print_json(api_get(base_url, f"/v1/foods/{args.food}"))
            return

        if args.exercises:
            params = {}
            if args.start_date:
                params["start_date"] = args.start_date
            if args.end_date:
                params["end_date"] = args.end_date
            if args.limit:
                params["limit"] = str(args.limit)
            print_json(api_get(base_url, "/v1/user/exercises", params or None))
            return

        if args.exercise:
            print_json(api_get(base_url, f"/v1/user/exercises/{args.exercise}"))
            return

        if args.exercise_types:
            params = {}
            if args.category:
                params["category"] = args.category
            if args.limit:
                params["limit"] = str(args.limit)
            print_json(api_get(base_url, "/v1/exercises/types", params or None))
            return

        if args.weight:
            params = {}
            if args.limit:
                params["limit"] = str(args.limit)
            print_json(api_get(base_url, "/v1/user/weight", params or None))
            return

        if args.water:
            print_json(api_get(base_url, f"/v1/user/water/{args.water}"))
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
