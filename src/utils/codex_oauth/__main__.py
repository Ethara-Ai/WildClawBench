"""CLI entry: ``python -m codex_oauth [--port 8788] [--check]``."""

from __future__ import annotations

import argparse
import logging
import os
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m codex_oauth")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8788)
    p.add_argument("--log-level", default="info")
    p.add_argument("--check", action="store_true",
                   help="Verify Codex credentials load, then exit.")
    args = p.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Anti-SSRF gate (AGENTS.md HARD invariant): the deployable bridge REFUSES
    # to start unauthenticated. Port 8788 is routinely forwarded into Docker
    # networks; without the secret ANY local process could spend the ChatGPT
    # subscription. --check is exempt (a local credential probe that never
    # serves traffic). build_app / _client_authorized keep their in-process
    # fail-open-with-warning path for embedded/test callers.
    if not args.check and not os.environ.get("KAIJU_CODEX_BRIDGE_SECRET", "").strip():
        print("[codex-bridge] refusing to start: KAIJU_CODEX_BRIDGE_SECRET is not "
              "set. This is the anti-SSRF gate — set it (and give clients the same "
              "value as OPENAI_API_KEY) before serving.", file=sys.stderr)
        return 2

    from .credentials import CredentialProvider, CredentialsError

    try:
        provider = CredentialProvider()
        token = provider.get_access_token()
    except CredentialsError as e:
        print(f"[codex-bridge] credentials error: {e}", file=sys.stderr)
        return 2

    print(f"[codex-bridge] credentials OK (token prefix: {token[:12]}..., "
          f"account: {provider.account_id[:8]}...)")
    if args.check:
        return 0

    import uvicorn
    from .bridge import build_app

    print(f"[codex-bridge] listening on http://{args.host}:{args.port}")
    print("[codex-bridge] point clients at:")
    print(f"           export OPENAI_BASE_URL=http://{args.host}:{args.port}")
    print("           export OPENAI_API_KEY=$KAIJU_CODEX_BRIDGE_SECRET   # (or a stub)")
    uvicorn.run(build_app(provider), host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
