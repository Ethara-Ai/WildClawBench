"""Codex OAuth MITM proxy: swap the stub Bearer for a pooled account, rotate on cap.

Codex 0.121 sends all its traffic (models HTTPS + the responses WebSocket +
token refresh) through ``HTTPS_PROXY`` and trusts a system-installed CA, but
``chatgpt_base_url`` does NOT redirect the inference endpoints -- so a plain
base-url bridge (as used for Claude) cannot intercept codex. Instead this is a
forward ``CONNECT`` proxy that MITMs ``chatgpt.com`` only: it terminates TLS
with a CA-signed leaf (see ``ca.py``), rewrites the ``authorization`` and
``chatgpt-account-id`` request headers with the currently-selected pooled
account, forwards to the real ChatGPT backend, and rotates accounts when the
upstream returns a 429 subscription cap. Everything else (auth.openai.com,
github.com, ...) is tunneled untouched.

Codex is given a non-expiring *stub* ``auth.json`` so it never refreshes its own
token; the proxy owns the real tokens + refresh + rotation.
"""

from __future__ import annotations

import logging
import select
import socket
import ssl
import threading
from typing import Optional

from .ca import CertAuthority
from .errors import ErrorKind, classify_openai_error

_LOG = logging.getLogger(__name__)

DEFAULT_MITM_HOSTS = ("chatgpt.com",)
_UPSTREAM_PORT = 443


class CodexOAuthProxy:
    def __init__(
        self,
        provider,
        ca: Optional[CertAuthority] = None,
        mitm_hosts: tuple[str, ...] = DEFAULT_MITM_HOSTS,
        host: str = "0.0.0.0",
        port: int = 8770,
    ) -> None:
        self.provider = provider
        self.ca = ca or CertAuthority()
        self.mitm_hosts = set(mitm_hosts)
        self.host = host
        self.port = port
        self._leaf_ctx: dict[str, ssl.SSLContext] = {}
        for h in self.mitm_hosts:
            self._leaf_ctx[h] = self._build_leaf_context(h)
        self._srv: Optional[socket.socket] = None

    # -- TLS contexts --------------------------------------------------------
    def _build_leaf_context(self, host: str) -> ssl.SSLContext:
        leaf = self.ca.issue_leaf(host)
        import tempfile, os

        cert_fh = tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False)
        key_fh = tempfile.NamedTemporaryFile("wb", suffix=".key", delete=False)
        try:
            cert_fh.write(leaf.cert_pem); cert_fh.flush(); cert_fh.close()
            key_fh.write(leaf.key_pem); key_fh.flush(); key_fh.close()
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert_fh.name, key_fh.name)
            try:
                ctx.set_alpn_protocols(["http/1.1"])
            except NotImplementedError:
                pass
            return ctx
        finally:
            for f in (cert_fh.name, key_fh.name):
                try:
                    os.unlink(f)
                except OSError:
                    pass

    # -- lifecycle -----------------------------------------------------------
    def serve_forever(self) -> None:
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(128)
        _LOG.info("codex OAuth proxy listening on %s:%d (MITM: %s)", self.host, self.port, ",".join(self.mitm_hosts))
        while True:
            try:
                client, _ = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def close(self) -> None:
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass

    # -- connection handling -------------------------------------------------
    def _handle(self, client: socket.socket) -> None:
        try:
            client.settimeout(120)
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(4096)
                if not chunk:
                    return
                head += chunk
            first = head.split(b"\r\n", 1)[0].decode("latin1", "replace")
            parts = first.split(" ")
            method = parts[0]
            if method == "CONNECT":
                hostport = parts[1]
                host, _, port_s = hostport.rpartition(":")
                port = int(port_s) if port_s else _UPSTREAM_PORT
                if host in self.mitm_hosts:
                    self._mitm(client, host)
                else:
                    self._tunnel(client, host, port)
            elif method in ("GET", "HEAD") and parts[1] in ("/healthz", "/quota"):
                self._health(client, parts[1])
            else:
                client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
        except Exception as e:  # noqa: BLE001 - never let a bad client kill the proxy
            _LOG.debug("proxy conn error: %s", e)
        finally:
            try:
                client.close()
            except OSError:
                pass

    def _health(self, client: socket.socket, path: str) -> None:
        body = b'{"ok": true}'
        if path == "/quota" and hasattr(self.provider, "snapshot"):
            import json

            body = json.dumps({"accounts": self.provider.snapshot()}).encode()
        client.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )

    def _tunnel(self, client: socket.socket, host: str, port: int) -> None:
        try:
            upstream = socket.create_connection((host, port), timeout=15)
        except OSError as e:
            _LOG.debug("tunnel connect failed %s:%d: %s", host, port, e)
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        self._pipe(client, upstream)

    def _mitm(self, client: socket.socket, host: str) -> None:
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        try:
            tls_client = self._leaf_ctx[host].wrap_socket(client, server_side=True)
        except (ssl.SSLError, OSError) as e:
            _LOG.debug("MITM TLS accept failed for %s: %s", host, e)
            return

        # Read the request header block from codex.
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = tls_client.recv(4096)
            if not chunk:
                return
            head += chunk
        idx = head.index(b"\r\n\r\n")
        header_bytes, body_start = head[:idx], head[idx + 4:]

        try:
            creds = self.provider.get_credentials()
        except Exception as e:  # noqa: BLE001 - pool exhausted / load failure
            _LOG.warning("no codex credentials available: %s", e)
            tls_client.sendall(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
            return

        rewritten = self._rewrite_auth(header_bytes, creds)

        # Only inference endpoints reflect account health. A 403/401 on an
        # auxiliary endpoint (wham/apps, connectors, analytics, MCP) must NOT
        # invalidate the account -- doing so falsely kills the pool.
        req_path = ""
        try:
            req_path = header_bytes.split(b"\r\n", 1)[0].split(b" ")[1].decode("latin1", "replace")
        except (IndexError, UnicodeDecodeError):
            pass
        is_inference = req_path.startswith("/backend-api/codex/responses") or req_path.startswith(
            "/backend-api/codex/models"
        )

        try:
            uctx = ssl.create_default_context()
            try:
                uctx.set_alpn_protocols(["http/1.1"])
            except NotImplementedError:
                pass
            upstream = uctx.wrap_socket(
                socket.create_connection((host, _UPSTREAM_PORT), timeout=15),
                server_hostname=host,
            )
        except (ssl.SSLError, OSError) as e:
            _LOG.warning("MITM upstream connect failed for %s: %s", host, e)
            tls_client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return

        upstream.sendall(rewritten + b"\r\n\r\n" + body_start)
        self._pipe(tls_client, upstream, account_label=creds.account_label, react=is_inference)

    def _rewrite_auth(self, header_bytes: bytes, creds) -> bytes:
        lines = header_bytes.split(b"\r\n")
        request_line = lines[0]
        out: list[bytes] = []
        seen_auth = seen_acct = False
        auth_line = b"authorization: Bearer " + creds.access_token.encode()
        acct_line = (b"chatgpt-account-id: " + creds.account_id.encode()) if creds.account_id else None
        for h in lines[1:]:
            lo = h.lower()
            if lo.startswith(b"authorization:"):
                out.append(auth_line); seen_auth = True
            elif lo.startswith(b"chatgpt-account-id:"):
                if acct_line:
                    out.append(acct_line)
                seen_acct = True
            else:
                out.append(h)
        if not seen_auth:
            out.append(auth_line)
        if not seen_acct and acct_line:
            out.append(acct_line)
        return b"\r\n".join([request_line, *out])

    # -- byte pump with response classification ------------------------------
    def _pipe(self, a: socket.socket, b: socket.socket, account_label: str = "", react: bool = False) -> None:
        """Bidirectionally pump a<->b; if ``react``, sniff b's first bytes to
        classify an inference-endpoint cap / auth failure for account rotation."""
        socks = [a, b]
        classified = False
        while True:
            try:
                readable, _, _ = select.select(socks, [], [], 120)
            except (OSError, ValueError):
                break
            if not readable:
                break
            for s in readable:
                try:
                    data = s.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                other = b if s is a else a
                if s is b and react and not classified:
                    classified = True
                    self._classify_and_react(data, account_label)
                try:
                    other.sendall(data)
                except OSError:
                    return

    def _classify_and_react(self, first_chunk: bytes, account_label: str) -> None:
        try:
            head = first_chunk.split(b"\r\n\r\n", 1)[0].decode("latin1", "replace")
            status_line = head.split("\r\n", 1)[0]
            status = int(status_line.split(" ")[1])
        except (ValueError, IndexError):
            return
        headers: dict[str, str] = {}
        for ln in head.split("\r\n")[1:]:
            if ":" in ln:
                k, _, v = ln.partition(":")
                headers[k.strip().lower()] = v.strip()
        classified = classify_openai_error(status, None, headers)
        if classified.kind == ErrorKind.OK:
            return
        _LOG.info("codex inference %s for account %s (kind=%s)", status, account_label, classified.kind.value)
        if not account_label:
            return
        # Rotate only on the two signals that genuinely mean "use another
        # account": a subscription cap (429) or an invalid/expired token (401).
        # 403/402 are plan/feature errors -- pass them through to codex.
        if classified.kind == ErrorKind.SUBSCRIPTION_CAP and hasattr(self.provider, "mark_account_exhausted"):
            self.provider.mark_account_exhausted(account_label, classified.reset_at_unix or 0.0)
        elif classified.kind == ErrorKind.OAUTH_TOKEN_INVALID and hasattr(self.provider, "mark_account_invalid"):
            self.provider.mark_account_invalid(account_label)
