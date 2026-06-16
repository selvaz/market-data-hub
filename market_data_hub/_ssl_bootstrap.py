# -*- coding: utf-8 -*-
"""
_ssl_bootstrap.py — risolve la verifica SSL su reti con MITM/proxy aziendale.

Sul PC dell'utente il Python di sistema non riesce a verificare i certificati
(`unable to get local issuer certificate`) perche' il traffico HTTPS e' firmato
da una root CA aziendale presente nel cert store di Windows ma assente da
certifi. yfinance usa curl_cffi (non la stdlib ssl), quindi non basta
truststore: serve un bundle PEM esplicito puntato via env var.

Strategia: costruiamo una sola volta un bundle = certifi + root/CA di Windows e
lo esponiamo a tutte le librerie (requests, curl_cffi, urllib/stdlib) tramite
SSL_CERT_FILE / REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE.

Import-safe e idempotente: chiamare ensure_ssl() all'avvio degli entry point,
PRIMA di importare yfinance/requests.
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
                for cert, enc, _trust in ssl.enum_certificates(store):
                    if enc == "x509_asn":
                        parts.append(ssl.DER_cert_to_PEM_cert(cert))
            except Exception:
                pass
        _BUNDLE.write_text("\n".join(parts), encoding="utf-8")
        return True
    except Exception:
        return False


def ensure_ssl(force_rebuild: bool = False) -> str | None:
    """Garantisce un CA bundle valido e configura le env var. Ritorna il path."""
    # solo su Windows c'e' enum_certificates; altrove certifi basta di solito
    if force_rebuild or not _BUNDLE.exists():
        if not _build_bundle() and not _BUNDLE.exists():
            return None

    path = str(_BUNDLE)
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        os.environ.setdefault(var, path)

    # belt-and-suspenders: usa anche il cert store di Windows per la stdlib ssl
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass

    return path
