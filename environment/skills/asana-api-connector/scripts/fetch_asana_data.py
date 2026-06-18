#!/usr/bin/env python3
"""CLI helper for the Asana API (Mock) mock API.

Generated read/write helper: one flag per endpoint. Base URL comes from
$ASANA_API_URL (override with --url). POST/PUT/PATCH bodies are read from --data
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
    p = argparse.ArgumentParser(description="Query the Asana API (Mock) mock API")
    p.add_argument("--get-api-1-0-workspaces", action="store_true", help="GET /api/1.0/workspaces")
    p.add_argument("--get-api-1-0-users", action="store_true", help="GET /api/1.0/users")
    p.add_argument("--get-api-1-0-projects", action="store_true", help="GET /api/1.0/projects")
    p.add_argument("--get-api-1-0-projects-project-gid", metavar="PROJECT_GID", nargs=1, help="GET /api/1.0/projects/{project_gid}")
    p.add_argument("--get-api-1-0-projects-sections-project-gid", metavar="PROJECT_GID", nargs=1, help="GET /api/1.0/projects/{project_gid}/sections")
    p.add_argument("--get-api-1-0-projects-tasks-project-gid", metavar="PROJECT_GID", nargs=1, help="GET /api/1.0/projects/{project_gid}/tasks")
    p.add_argument("--get-api-1-0-tasks", action="store_true", help="GET /api/1.0/tasks")
    p.add_argument("--post-api-1-0-tasks", action="store_true", help="POST /api/1.0/tasks")
    p.add_argument("--get-api-1-0-tasks-task-gid", metavar="TASK_GID", nargs=1, help="GET /api/1.0/tasks/{task_gid}")
    p.add_argument("--put-api-1-0-tasks-task-gid", metavar="TASK_GID", nargs=1, help="PUT /api/1.0/tasks/{task_gid}")
    p.add_argument("--data", metavar="JSON", help="Request body as a JSON string (POST/PUT/PATCH)")
    p.add_argument("--data-file", metavar="PATH", help="Request body from a JSON file (POST/PUT/PATCH)")
    p.add_argument("--url", default=os.environ.get("ASANA_API_URL", "http://localhost:8031"),
                   help="API base URL (default: $ASANA_API_URL or http://localhost:8031)")
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
    if args.get_api_1_0_workspaces:
        return show(api_get(base, "/api/1.0/workspaces"))
    if args.get_api_1_0_users:
        return show(api_get(base, "/api/1.0/users"))
    if args.get_api_1_0_projects:
        return show(api_get(base, "/api/1.0/projects"))
    if args.get_api_1_0_projects_project_gid:
        return show(api_get(base, _fill('/api/1.0/projects/{project_gid}', args.get_api_1_0_projects_project_gid)))
    if args.get_api_1_0_projects_sections_project_gid:
        return show(api_get(base, _fill('/api/1.0/projects/{project_gid}/sections', args.get_api_1_0_projects_sections_project_gid)))
    if args.get_api_1_0_projects_tasks_project_gid:
        return show(api_get(base, _fill('/api/1.0/projects/{project_gid}/tasks', args.get_api_1_0_projects_tasks_project_gid)))
    if args.get_api_1_0_tasks:
        return show(api_get(base, "/api/1.0/tasks"))
    if args.post_api_1_0_tasks:
        return show(api_send(base, '/api/1.0/tasks', 'POST', _body(args)))
    if args.get_api_1_0_tasks_task_gid:
        return show(api_get(base, _fill('/api/1.0/tasks/{task_gid}', args.get_api_1_0_tasks_task_gid)))
    if args.put_api_1_0_tasks_task_gid:
        return show(api_send(base, _fill('/api/1.0/tasks/{task_gid}', args.put_api_1_0_tasks_task_gid), 'PUT', _body(args)))
    print("No endpoint flag provided. Use -h to list available endpoints.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
