"""CLI entry: ``python -m src.utils.codex_oauth [--port 8770] [--ca-out PATH] [--check]``.

Resolves the account pool from ``WCB_CX_ACCOUNT_POOL`` (colon-separated codex
``auth.json`` paths), generates an ephemeral CA (writes only the public cert to
``--ca-out`` for the harness to install into the agent container's trust store),
and runs the MITM proxy.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .ca import CertAuthority
from .credentials import CredentialsError, load_account_pool
from .proxy import DEFAULT_MITM_HOSTS, CodexOAuthProxy


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.utils.codex_oauth")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.environ.get("WCB_CX_PROXY_PORT", "8770")))
    p.add_argument("--ca-out", default=os.environ.get("WCB_CX_CA_OUT", ""),
                   help="Write the proxy CA certificate (PEM) here for container install.")
    p.add_argument("--pool", default=os.environ.get("WCB_CX_ACCOUNT_POOL", ""),
                   help="Colon-separated codex auth.json paths (else WCB_CX_ACCOUNT_POOL).")
    p.add_argument("--mitm-host", action="append", default=None,
                   help="Host to MITM (repeatable). Default: chatgpt.com.")
    p.add_argument("--log-level", default="info")
    p.add_argument("--check", action="store_true", help="Validate the pool then exit.")
    args = p.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    provider = load_account_pool(args.pool)
    if provider is None:
        print("[codex-oauth] no account pool: set WCB_CX_ACCOUNT_POOL or --pool", file=sys.stderr)
        return 2
    try:
        creds = provider.get_credentials()
    except CredentialsError as e:
        print(f"[codex-oauth] credentials error: {e}", file=sys.stderr)
        return 2
    print(f"[codex-oauth] pool OK (first account token prefix: {creds.access_token[:12]}...)")

    ca = CertAuthority()
    if args.ca_out:
        Path(args.ca_out).expanduser().write_bytes(ca.cert_pem)
        print(f"[codex-oauth] wrote CA cert to {args.ca_out}")

    if args.check:
        return 0

    mitm = tuple(args.mitm_host) if args.mitm_host else DEFAULT_MITM_HOSTS
    proxy = CodexOAuthProxy(provider, ca=ca, mitm_hosts=mitm, host=args.host, port=args.port)
    print(f"[codex-oauth] proxy on http://{args.host}:{args.port} — point codex at it via HTTPS_PROXY")
    try:
        proxy.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
