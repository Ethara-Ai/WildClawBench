"""Codex ChatGPT-plan OAuth support for wildclawbench.

Runs codex on a ChatGPT/Codex subscription instead of an OpenRouter API key by
placing a MITM forward proxy between the codex CLI and ``chatgpt.com``. Codex
0.121 routes all traffic through ``HTTPS_PROXY`` and trusts a system-installed
CA, but ``chatgpt_base_url`` does not redirect its inference endpoints -- so a
plain base-url bridge (as used for Claude) cannot intercept it. The proxy
terminates TLS for ``chatgpt.com``, swaps the container's stub Bearer for a
pooled account's real token, and rotates accounts on a 429 subscription cap.

See ``docs/CODEX_OAUTH.md`` for setup, wiring, and ToS caveats.
"""

from .ca import CertAuthority, LeafCert
from .credentials import (
    CodexCredentialProvider,
    CodexCredentials,
    CredentialsError,
    MultiAccountCredentialProvider,
    load_account_pool,
    load_codex_credentials,
    refresh_codex_credentials,
)
from .errors import ClassifiedError, ErrorKind, classify_openai_error, extract_retry_after
from .proxy import CodexOAuthProxy

__all__ = [
    "CertAuthority",
    "ClassifiedError",
    "CodexCredentialProvider",
    "CodexCredentials",
    "CodexOAuthProxy",
    "CredentialsError",
    "ErrorKind",
    "LeafCert",
    "MultiAccountCredentialProvider",
    "classify_openai_error",
    "extract_retry_after",
    "load_account_pool",
    "load_codex_credentials",
    "refresh_codex_credentials",
]
