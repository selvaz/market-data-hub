"""Content-addressed raw transport evidence; no document duplication in readings."""
import hashlib


def extract_document_text(row):
    """Use the issuer parsers' PDF/HTML extraction without network or writes.

    Documents currently retain original bodies only, with no extracted-text
    column or response headers. Preserve metadata even for unreadable bodies.
    """
    from .http import Document
    from .tier1_publications import publication_text

    body = bytes(row["body"])
    stripped = body.lstrip()
    content_type = ("application/pdf" if body.startswith(b"%PDF") else
                    "text/html" if stripped.startswith(b"<") else
                    "application/json" if stripped.startswith((b"{", b"[")) else "text/plain")
    try:
        text, _ = publication_text(Document(body, row["url"], row["retrieved_at"], row["document_hash"]))
    except Exception:
        # A corrupt PDF/unsupported encoding must not hide other saved bodies.
        return "", content_type, "text_extraction_failed"
    return text, content_type, None if text.strip() else "no_extractable_text"


def put_document(store, document):
    identity = document.identity_hash
    store.conn.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        (identity, hashlib.sha256(document.body).hexdigest(), document.url,
         document.retrieved_at, document.body, "transport-tokens:v1"))
    return identity
