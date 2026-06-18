#!/usr/bin/env python3
"""CLI helper for the GitHub REST API (Mock) mock API.

Generated read/write helper: one flag per endpoint. Base URL comes from
$GITHUB_API_URL (override with --url). POST/PUT/PATCH bodies are read from --data
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
    p = argparse.ArgumentParser(description="Query the GitHub REST API (Mock) mock API")
    p.add_argument("--get-user", action="store_true", help="GET /user")
    p.add_argument("--get-users-repos-owner", metavar="OWNER", nargs=1, help="GET /users/{owner}/repos")
    p.add_argument("--get-orgs-repos-owner", metavar="OWNER", nargs=1, help="GET /orgs/{owner}/repos")
    p.add_argument("--get-repos-owner-repo", metavar="OWNER/REPO", nargs=2, help="GET /repos/{owner}/{repo}")
    p.add_argument("--get-repos-issues-owner-repo", metavar="OWNER/REPO", nargs=2, help="GET /repos/{owner}/{repo}/issues")
    p.add_argument("--get-repos-issues-owner-repo-number", metavar="OWNER/REPO/NUMBER", nargs=3, help="GET /repos/{owner}/{repo}/issues/{number}")
    p.add_argument("--post-repos-issues-owner-repo", metavar="OWNER/REPO", nargs=2, help="POST /repos/{owner}/{repo}/issues")
    p.add_argument("--patch-repos-issues-owner-repo-number", metavar="OWNER/REPO/NUMBER", nargs=3, help="PATCH /repos/{owner}/{repo}/issues/{number}")
    p.add_argument("--get-repos-pulls-owner-repo", metavar="OWNER/REPO", nargs=2, help="GET /repos/{owner}/{repo}/pulls")
    p.add_argument("--get-repos-pulls-owner-repo-number", metavar="OWNER/REPO/NUMBER", nargs=3, help="GET /repos/{owner}/{repo}/pulls/{number}")
    p.add_argument("--put-repos-pulls-merge-owner-repo-number", metavar="OWNER/REPO/NUMBER", nargs=3, help="PUT /repos/{owner}/{repo}/pulls/{number}/merge")
    p.add_argument("--get-repos-issues-comments-owner-repo-number", metavar="OWNER/REPO/NUMBER", nargs=3, help="GET /repos/{owner}/{repo}/issues/{number}/comments")
    p.add_argument("--post-repos-issues-comments-owner-repo-number", metavar="OWNER/REPO/NUMBER", nargs=3, help="POST /repos/{owner}/{repo}/issues/{number}/comments")
    p.add_argument("--data", metavar="JSON", help="Request body as a JSON string (POST/PUT/PATCH)")
    p.add_argument("--data-file", metavar="PATH", help="Request body from a JSON file (POST/PUT/PATCH)")
    p.add_argument("--url", default=os.environ.get("GITHUB_API_URL", "http://localhost:8019"),
                   help="API base URL (default: $GITHUB_API_URL or http://localhost:8019)")
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
    if args.get_user:
        return show(api_get(base, "/user"))
    if args.get_users_repos_owner:
        return show(api_get(base, _fill('/users/{owner}/repos', args.get_users_repos_owner)))
    if args.get_orgs_repos_owner:
        return show(api_get(base, _fill('/orgs/{owner}/repos', args.get_orgs_repos_owner)))
    if args.get_repos_owner_repo:
        return show(api_get(base, _fill('/repos/{owner}/{repo}', args.get_repos_owner_repo)))
    if args.get_repos_issues_owner_repo:
        return show(api_get(base, _fill('/repos/{owner}/{repo}/issues', args.get_repos_issues_owner_repo)))
    if args.get_repos_issues_owner_repo_number:
        return show(api_get(base, _fill('/repos/{owner}/{repo}/issues/{number}', args.get_repos_issues_owner_repo_number)))
    if args.post_repos_issues_owner_repo:
        return show(api_send(base, _fill('/repos/{owner}/{repo}/issues', args.post_repos_issues_owner_repo), 'POST', _body(args)))
    if args.patch_repos_issues_owner_repo_number:
        return show(api_send(base, _fill('/repos/{owner}/{repo}/issues/{number}', args.patch_repos_issues_owner_repo_number), 'PATCH', _body(args)))
    if args.get_repos_pulls_owner_repo:
        return show(api_get(base, _fill('/repos/{owner}/{repo}/pulls', args.get_repos_pulls_owner_repo)))
    if args.get_repos_pulls_owner_repo_number:
        return show(api_get(base, _fill('/repos/{owner}/{repo}/pulls/{number}', args.get_repos_pulls_owner_repo_number)))
    if args.put_repos_pulls_merge_owner_repo_number:
        return show(api_send(base, _fill('/repos/{owner}/{repo}/pulls/{number}/merge', args.put_repos_pulls_merge_owner_repo_number), 'PUT', _body(args)))
    if args.get_repos_issues_comments_owner_repo_number:
        return show(api_get(base, _fill('/repos/{owner}/{repo}/issues/{number}/comments', args.get_repos_issues_comments_owner_repo_number)))
    if args.post_repos_issues_comments_owner_repo_number:
        return show(api_send(base, _fill('/repos/{owner}/{repo}/issues/{number}/comments', args.post_repos_issues_comments_owner_repo_number), 'POST', _body(args)))
    print("No endpoint flag provided. Use -h to list available endpoints.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
