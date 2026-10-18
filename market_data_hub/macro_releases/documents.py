"""Content-addressed raw transport evidence; no document duplication in readings."""
import hashlib


def put_document(store, document):
    identity = document.identity_hash
    store.conn.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        (identity, hashlib.sha256(document.body).hexdigest(), document.url,
         document.retrieved_at, document.body, "transport-tokens:v1"))
    return identity
