# -*- coding: utf-8 -*-
"""Binary artifact identity and transport for the ecosystem.

The ecosystem produces binary artifacts (regime charts in a LazyStats depot,
images/tables scraped by LazyCrawler, charts rendered on demand from hub
series) in *separate* stores, each with its own native key. ``ArtifactRef``
gives all of them one canonical, namespaced name — exactly the same shape as
:class:`~market_data_hub.lazydatacore.identity.InstrumentId`:

    ``"<scheme>:<key>"``          e.g. ``regimes:plot_ab12cd``
                                       ``crawler:9f8e7d6c...`` (content hash)
                                       ``file:reports/fig1.png``

A ref is a *name*, not the bytes: resolving it to content is the consumer's
job (LazyTools' report module keeps a registry of per-scheme resolvers). The
scheme set is deliberately **open** — a new producer registers a new scheme
without a contract major bump; a typo'd scheme fails loudly at resolve time
(no resolver registered) rather than at parse time. The well-known schemes
are documented in :data:`WELL_KNOWN_ARTIFACT_SCHEMES`.

``ResolvedArtifact`` is the transport envelope a resolver returns: the ref,
the actual MIME type and the payload as base64 text, so it round-trips
through ``model_dump(mode="json")`` / ``lazybridge.Store`` like every other
model in this contract (raw ``bytes`` would not).

Only the first ``:`` separates scheme from key, so keys may themselves
contain ``:`` (Windows paths under ``file:``).
"""
from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from market_data_hub.lazydatacore.result import LazyDataModel

# Schemes with an agreed meaning across the ecosystem. Informative, not
# exhaustive — see the module docstring for why the set is open.
WELL_KNOWN_ARTIFACT_SCHEMES = (
    "regimes",  # PNG blob in a LazyStats regime depot; key = plot_key
    "crawler",  # blob in a LazyCrawler artifacts table; key = content_hash
    "chart",    # chart rendered on demand from hub series; key = chart spec id
    "file",     # local file; key = path (may contain ':' on Windows)
    "bytes",    # inline payload; key = base64-encoded bytes
)

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class ArtifactRef(BaseModel):
    """An immutable, namespaced name for a binary artifact.

    Construct from parts or parse from the canonical string with :meth:`parse`.
    Serialises to that same canonical string, so it round-trips through JSON
    and ``lazybridge.Store`` as a plain string field — the same shape as
    :class:`InstrumentId`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scheme: str = Field(pattern=_SCHEME_RE.pattern)
    key: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        # Accept the canonical string anywhere an ArtifactRef is expected.
        if isinstance(data, str):
            if ":" not in data:
                raise ValueError(
                    f"not a namespaced artifact ref: {data!r} "
                    "(expected 'scheme:key', e.g. 'regimes:plot_ab12cd')"
                )
            scheme, key = data.split(":", 1)
            return {"scheme": scheme, "key": key}
        return data

    @model_serializer
    def _serialize(self) -> str:
        return str(self)

    def __str__(self) -> str:
        return f"{self.scheme}:{self.key}"

    @classmethod
    def parse(cls, value: "str | ArtifactRef") -> "ArtifactRef":
        """Parse a canonical ``"scheme:key"`` string.

        Already-constructed :class:`ArtifactRef` values pass through, so this
        is safe to call on mixed input.
        """
        if isinstance(value, ArtifactRef):
            return value
        return cls.model_validate(value)


class ResolvedArtifact(LazyDataModel):
    """A resolved artifact: the ref plus its actual content.

    The payload travels as base64 text (``data_b64``) so the envelope stays
    JSON-serialisable; use :meth:`from_bytes` / :attr:`data` at the byte
    boundary. ``mime`` is the *actual* content type as determined by the
    resolver, not a hint.
    """

    ref: ArtifactRef
    mime: str = Field(pattern=r"^[\w.+-]+/[\w.+-]+$")  # e.g. "image/png"
    data_b64: str
    meta: Dict[str, str] = Field(default_factory=dict)

    @field_validator("data_b64")
    @classmethod
    def _check_base64(cls, v: str) -> str:
        try:
            base64.b64decode(v, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"data_b64 is not valid base64: {exc}") from exc
        return v

    @property
    def data(self) -> bytes:
        """The decoded payload bytes."""
        return base64.b64decode(self.data_b64)

    @classmethod
    def from_bytes(
        cls,
        ref: "str | ArtifactRef",
        mime: str,
        data: bytes,
        *,
        meta: Dict[str, str] | None = None,
    ) -> "ResolvedArtifact":
        """Build an envelope from raw bytes, encoding them to base64."""
        return cls(
            ref=ArtifactRef.parse(ref),
            mime=mime,
            data_b64=base64.b64encode(data).decode("ascii"),
            meta=meta or {},
        )
