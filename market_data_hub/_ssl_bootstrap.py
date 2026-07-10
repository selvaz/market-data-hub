# -*- coding: utf-8 -*-
"""
_ssl_bootstrap.py — fixes SSL verification on networks with corporate MITM/proxy.

On the user's PC the system Python cannot verify the certificates
(`unable to get local issuer certificate`) because the HTTPS traffic is signed
by a corporate root CA present in the Windows cert store but absent from
certifi. yfinance uses curl_cffi (not the stdlib ssl), so truststore alone is
not enough: an explicit PEM bundle pointed to via env var is required.

Strategy: we build a bundle once = certifi + Windows root/CA and expose it to
all the libraries (requests, curl_cffi, urllib/stdlib) via
SSL_CERT_FILE / REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE.

Import-safe and idempotent: call ensure_ssl() at the startup of the entry
points, BEFORE importing yfinance/requests.
"""
from __future__ import annotations

import os
import ssl
from pathlib import Path

_BUNDLE = Path(__file__).parent.parent / "ca_bundle.pem"


def _build_bundle() -> bool:
    try:
        import certifi
    except Exception:
        return False
    try:
        parts = [certifi.contents()]
        for store in ("ROOT", "CA"):
            try:
                # ssl.enum_certificates only exists on Windows; typeshed
                # doesn't declare it for other platforms. Already guarded by
                # the surrounding try/except for non-Windows runtimes.
                for cert, enc, _trust in ssl.enum_certificates(store):  # type: ignore[attr-defined]
                    if enc == "x509_asn":
                        parts.append(ssl.DER_cert_to_PEM_cert(cert))
            except Exception:
                pass
        _BUNDLE.write_text("\n".join(parts), encoding="utf-8")
        return True
    except Exception:
        return False


def ensure_ssl(force_rebuild: bool = False) -> str | None:
    """Ensure a valid CA bundle and configure the env vars. Returns the path."""
    # only Windows has enum_certificates; elsewhere certifi is usually enough
    if force_rebuild or not _BUNDLE.exists():
        if not _build_bundle() and not _BUNDLE.exists():
            return None

    path = str(_BUNDLE)
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        os.environ.setdefault(var, path)

    # belt-and-suspenders: also use the Windows cert store for the stdlib ssl
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass

    return path
