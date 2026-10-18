"""Bounded HTTP and a provider-wide, process-safe quota ledger. No polling."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

import certifi
from filelock import FileLock
import requests


class FetchError(RuntimeError):
    """Safe to report: never includes credential-bearing URLs or bodies."""


def api_key(name: str) -> str:
    value = os.environ.get(name)
    if not value and os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value = winreg.QueryValueEx(key, name)[0]
        except OSError:
            pass
    if not value:
        raise FetchError(f"Missing credential: {name}")
    return value


@dataclass(frozen=True)
class Document:
    body: bytes
    url: str
    retrieved_at: str
    document_hash: str

    def json(self):
        return json.loads(self.body)


class RateLimiter:
    """One named ledger per provider, shared even when databases differ."""

    def __init__(self, provider, directory=None, *, interval=1.0, daily_limit=None,
                 persistent=True):
        self.path = None
        if persistent:
            root = Path(directory or Path(tempfile.gettempdir()) / "mdh-macro-rates")
            root.mkdir(parents=True, exist_ok=True)
            self.path = root / (provider + ".json")
        self.state = {}
        self.interval = interval
        self.daily_limit = daily_limit

    def acquire(self):
        with FileLock(str(self.path) + ".lock", timeout=15) if self.path else nullcontext():
            now = time.time()
            state = (json.loads(self.path.read_text()) if self.path.exists() else {}) if self.path else self.state
            day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
            count = state.get("count", 0) if state.get("day") == day else 0
            if self.daily_limit is not None and count >= self.daily_limit:
                raise FetchError("Provider daily quota exhausted; try the next daily run")
            delay = max(0, state.get("last", 0) + self.interval - now)
            if delay > 5:
                raise FetchError("Provider rate ledger clock is ahead; no wait loop")
            if delay:
                time.sleep(delay)
            self.state = {"day": day, "count": count + 1, "last": time.time()}
            if self.path:
                self.path.write_text(json.dumps(self.state))


class HttpClient:
    def __init__(self, *, rate_directory=None, session=None, before_request=None, persistent_rates=True):
        self.session = session or requests.Session()
        self.before_request = before_request
        # Use certifi explicitly: the Windows system store is broken here.
        self.session.trust_env = False
        self.limiters = {p: RateLimiter(p, rate_directory, daily_limit=500 if p == "bls" else None,
                                     persistent=persistent_rates)
                         for p in ("bls", "bea", "fred", "eurostat", "ons")}

    def request(self, provider, url, *, params=None, payload=None) -> Document:
        safe_url = urlsplit(url)
        if safe_url.scheme != "https" or safe_url.query or safe_url.fragment:
            raise ValueError("HTTPS endpoint without query required; use params")
        # Exactly two possible attempts, including timeouts. No requests adapter retries.
        for attempt in range(2):
            if self.before_request:
                self.before_request()
            self.limiters[provider].acquire()
            try:
                response = self.session.request(
                    "POST" if payload is not None else "GET", url,
                    params=params, json=payload, timeout=(10, 45),
                    verify=certifi.where(), allow_redirects=False,
                )
            except requests.exceptions.SSLError:
                raise FetchError(f"{provider}: TLS verification failed") from None
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise FetchError(f"{provider}: transport failed after two attempts") from None
            except requests.RequestException:
                raise FetchError(f"{provider}: non-transient request failure") from None
            if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(1)
                continue
            if response.status_code != 200:
                raise FetchError(f"{provider}: HTTP {response.status_code}")
            raw = response.content
            digest = hashlib.sha256(raw).hexdigest()
            # BEA echoes its key. Hash original bytes, retain only redacted content.
            for field, value in {**(params or {}), **(payload or {})}.items():
                if field.lower() in {"userid", "api_key", "registrationkey"} and value:
                    raw = raw.replace(str(value).encode(), b"REDACTED")
            return Document(raw, url, datetime.now(timezone.utc).isoformat(), digest)
        raise AssertionError("unreachable")

    def get(self, provider, url, params=None):
        return self.request(provider, url, params=params)


def require_complete(payload, rows, *, count_key="count", offset_key="offset"):
    """Fail explicitly instead of silently accepting a truncated response."""
    if payload.get(offset_key, 0) or payload.get(count_key, len(rows)) > len(rows):
        raise FetchError("Provider response truncated; narrow --since")


def json_payload(document: Document):
    try:
        return document.json()
    except (ValueError, UnicodeError):
        raise FetchError("Provider returned invalid JSON") from None
