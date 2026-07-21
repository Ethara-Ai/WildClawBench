"""Ephemeral CA + per-host leaf certificates for the codex OAuth MITM proxy.

The proxy terminates TLS for ``chatgpt.com`` so it can swap the stub Bearer for
a pooled account's real token. To make codex (rustls / native-tls, which honor
the system trust store) accept the forged certificate, the harness installs the
CA cert produced here into the agent container's trust store
(``update-ca-certificates``) -- see ``src.agents.codex.backend.install_ca_in_container``.

A fresh CA is generated per proxy start (in-memory private key; only the public
cert is ever written to disk / copied into the container), so a leaked CA cert
cannot be used to sign anything after the run ends.
"""

from __future__ import annotations

import datetime
import ipaddress
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_ONE_DAY = datetime.timedelta(days=1)


@dataclass
class LeafCert:
    cert_pem: bytes
    key_pem: bytes


class CertAuthority:
    """A throwaway certificate authority that mints per-host leaf certs."""

    def __init__(self, common_name: str = "WCB Codex OAuth Proxy CA") -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        # Validity must bracket the real wall clock -- codex verifies notAfter,
        # so a fixed past epoch would be rejected as "certificate has expired".
        now = datetime.datetime.now(datetime.timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        self._cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self._key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _ONE_DAY)
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, key_cert_sign=True, crl_sign=True,
                    key_encipherment=False, content_commitment=False,
                    data_encipherment=False, key_agreement=False,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .sign(self._key, hashes.SHA256())
        )

    @property
    def cert_pem(self) -> bytes:
        return self._cert.public_bytes(serialization.Encoding.PEM)

    def issue_leaf(self, host: str) -> LeafCert:
        """Mint a leaf cert (key + chain) for ``host``, signed by this CA."""
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.datetime.now(datetime.timezone.utc)
        try:
            san: x509.GeneralName = x509.IPAddress(ipaddress.ip_address(host))
        except ValueError:
            san = x509.DNSName(host)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(self._cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _ONE_DAY)
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName([san]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(self._key, hashes.SHA256())
        )
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        # Leaf + CA so the client receives the full chain.
        chain_pem = cert.public_bytes(serialization.Encoding.PEM) + self.cert_pem
        return LeafCert(cert_pem=chain_pem, key_pem=key_pem)
