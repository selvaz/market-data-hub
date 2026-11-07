"""Append-only BOJ release-clock evidence, independent of the statement PDF."""
import json

from .models import utc

RSS = "https://www.boj.or.jp/en/rss/whatsnew.xml"
TIME_FIELDS = ("issuer_published_at", "publication_precision", "issuer_local_time",
               "issuer_timezone", "policy_decision_json")


def decision_facts(payload):
    """Exclude only the release clock and its matching BOJ RSS attribution."""
    facts = json.loads(payload)
    if not isinstance(facts, dict):
        raise ValueError("BOJ decision facts required")
    facts = {k: v for k, v in facts.items()
             if k not in {"published_at", "publication_precision", "time_missing_reason"}}
    facts["supporting_sources"] = [s for s in facts.get("supporting_sources", [])
        if not (s.get("source_url") == RSS and s.get("evidence") == "issuer_publication_timestamp")]
    return facts


def record_time(conn, previous, incoming):
    """Monotonic evidence: date-only rereads cannot retract an exact clock."""
    if incoming["publication_precision"] != "exact":
        return
    facts = json.loads(incoming["policy_decision_json"])
    sources = [s for s in facts.get("supporting_sources", [])
        if s.get("source_url") == RSS and s.get("evidence") == "issuer_publication_timestamp"]
    if (not sources or not facts.get("published_at")
            or utc(facts["published_at"]) != incoming["issuer_published_at"]):
        raise ValueError("BOJ exact time requires matching issuer RSS evidence")
    saved = conn.execute(
        "SELECT * FROM boj_publication_times WHERE value_vintage_id=? ORDER BY id LIMIT 1",
        (previous["id"],)).fetchone()
    exact = saved or (previous if previous["publication_precision"] == "exact" else None)
    if exact is not None:
        if any(exact[k] != incoming[k] for k in TIME_FIELDS[:-1]):
            raise ValueError("same BOJ publication has conflicting exact time evidence")
        return
    recorded_at = max(incoming["retrieved_at"], *(utc(s.get("retrieved_at") or incoming["retrieved_at"])
                                                for s in sources))
    columns = ("value_vintage_id", *TIME_FIELDS, "recorded_at")
    conn.execute("INSERT INTO boj_publication_times (" + ",".join(columns) + ") VALUES (" +
        ",".join("?" for _ in columns) + ")",
        (previous["id"], *(incoming[k] for k in TIME_FIELDS), recorded_at))
