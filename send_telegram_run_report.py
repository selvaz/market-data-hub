# -*- coding: utf-8 -*-
"""Send a detailed market_data_hub run report to Telegram.

Uses LazyTools' Telegram connector. Configuration comes from environment:

    TELEGRAM_BOT_TOKEN   Bot token from BotFather
    TELEGRAM_CHAT_ID     Target chat id or @channel username

Examples:
    python send_telegram_run_report.py
    python send_telegram_run_report.py --run-id backfill_abcd1234
    python send_telegram_run_report.py --db C:\path\market_data.duckdb --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from lazytools.connectors.telegram import TelegramClient  # noqa: E402
from market_data_hub.config_loader import get_settings  # noqa: E402
from market_data_hub.db.connection import get_conn  # noqa: E402


ROOT = Path(__file__).resolve().parent


def _base_report_dir() -> Path:
    cfg = get_settings().get("reports", {})
    path = Path(cfg.get("dir") or "reports")
    if not path.is_absolute():
        path = ROOT / path
    return path


REPORT_DIR = _base_report_dir() / "telegram"


def _fmt_int(value: Any) -> str:
    if value is None or pd.isna(value):
        return "0"
    return f"{int(value):,}"


def _fmt_float(value: Any, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):.{digits}f}"


def _md_table(df: pd.DataFrame, columns: list[str], *, max_rows: int = 20) -> str:
    if df.empty:
        return "(none)"
    view = df.loc[:, columns].head(max_rows).copy()
    for col in view.columns:
        view[col] = view[col].map(lambda x: "" if pd.isna(x) else str(x))
    widths = {col: max(len(col), *(len(v) for v in view[col].astype(str))) for col in columns}
    header = " | ".join(col.ljust(widths[col]) for col in columns)
    sep = " | ".join("-" * widths[col] for col in columns)
    rows = [" | ".join(str(row[col]).ljust(widths[col]) for col in columns) for _, row in view.iterrows()]
    more = f"\n... +{len(df) - max_rows} rows" if len(df) > max_rows else ""
    return "```\n" + "\n".join([header, sep, *rows]) + more + "\n```"


def _latest_run_id(con) -> str | None:
    row = con.execute(
        """
        SELECT run_id
        FROM download_log
        GROUP BY run_id
        ORDER BY min(started_at) DESC
        LIMIT 1
        """
    ).fetchone()
    return row[0] if row else None


def _single_row(con, sql: str, params: list[Any] | None = None) -> dict[str, Any]:
    df = con.execute(sql, params or []).fetch_df()
    return {} if df.empty else dict(df.iloc[0])


def _safe_df(con, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
    try:
        return con.execute(sql, params or []).fetch_df()
    except Exception as exc:
        return pd.DataFrame({"error": [str(exc)]})


def collect_report(db_path: str | None, run_id: str | None) -> tuple[str, str]:
    con = get_conn(db_path, read_only=True)
    try:
        run_id = run_id or _latest_run_id(con)
        if not run_id:
            return "market_data_hub: no runs", "No run recorded in download_log yet."

        summary = _single_row(
            con,
            """
            SELECT
                run_id,
                min(started_at) AS started_at,
                max(coalesce(ended_at, started_at)) AS ended_at,
                count(*) AS log_rows,
                count(DISTINCT source) AS sources,
                sum(coalesce(rows_added, 0)) AS rows_added,
                sum(coalesce(rows_updated, 0)) AS rows_updated,
                sum(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok,
                sum(CASE WHEN status = 'empty' THEN 1 ELSE 0 END) AS empty,
                sum(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                sum(coalesce(duration_sec, 0)) AS duration_sec_sum
            FROM download_log
            WHERE run_id = ?
            GROUP BY run_id
            """,
            [run_id],
        )

        by_source = _safe_df(
            con,
            """
            SELECT
                source,
                count(*) AS items,
                sum(coalesce(rows_added, 0)) AS added,
                sum(coalesce(rows_updated, 0)) AS updated,
                sum(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok,
                sum(CASE WHEN status = 'empty' THEN 1 ELSE 0 END) AS empty,
                sum(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                round(sum(coalesce(duration_sec, 0)), 1) AS duration_s
            FROM download_log
            WHERE run_id = ?
            GROUP BY source
            ORDER BY errors DESC, source
            """,
            [run_id],
        )

        errors = _safe_df(
            con,
            """
            SELECT source, symbol, status, left(coalesce(error_msg, ''), 220) AS error
            FROM download_log
            WHERE run_id = ? AND status = 'error'
            ORDER BY source, symbol
            LIMIT 25
            """,
            [run_id],
        )

        slowest = _safe_df(
            con,
            """
            SELECT source, symbol, status, round(coalesce(duration_sec, 0), 1) AS seconds,
                   rows_added AS added, rows_updated AS updated
            FROM download_log
            WHERE run_id = ?
            ORDER BY coalesce(duration_sec, 0) DESC
            LIMIT 10
            """,
            [run_id],
        )

        coverage = _safe_df(
            con,
            """
            SELECT
                count(*) AS series,
                round(avg(coverage_score), 1) AS avg_score,
                sum(CASE WHEN stalled THEN 1 ELSE 0 END) AS stalled,
                sum(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS coverage_errors,
                sum(coalesce(obs_count, 0)) AS observations
            FROM coverage_report
            """,
        )

        stalled = _safe_df(
            con,
            """
            SELECT symbol, source, last_date, lag_days, freq_detected AS freq,
                   round(coverage_score, 1) AS score, status
            FROM coverage_report
            WHERE stalled = TRUE
            ORDER BY lag_days DESC, coverage_score ASC
            LIMIT 20
            """,
        )

        macro_cov = _safe_df(
            con,
            """
            SELECT
                count(*) AS indicators,
                round(avg(coverage_pct), 1) AS avg_country_coverage_pct,
                sum(CASE WHEN stalled THEN 1 ELSE 0 END) AS stalled,
                sum(coalesce(obs_count, 0)) AS observations
            FROM macro_panel_coverage
            """,
        )

        totals = _safe_df(
            con,
            """
            SELECT 'prices_daily' AS table_name, count(*) AS rows, count(DISTINCT symbol) AS series,
                   min(date)::VARCHAR AS first_date, max(date)::VARCHAR AS last_date
            FROM prices_daily
            UNION ALL
            SELECT 'macro_series', count(*), count(DISTINCT series_id), min(date)::VARCHAR, max(date)::VARCHAR
            FROM macro_series
            UNION ALL
            SELECT 'crypto_ohlcv', count(*), count(DISTINCT symbol || ':' || timeframe), min(ts)::VARCHAR, max(ts)::VARCHAR
            FROM crypto_ohlcv
            UNION ALL
            SELECT 'macro_panel', count(*), count(DISTINCT country_iso3 || ':' || indicator_id), min(date)::VARCHAR, max(date)::VARCHAR
            FROM macro_panel
            UNION ALL
            SELECT 'factor_returns', count(*), count(DISTINCT factor_set || ':' || factor), min(date)::VARCHAR, max(date)::VARCHAR
            FROM factor_returns
            ORDER BY table_name
            """,
        )

        title = f"market_data_hub run {run_id}"
        started = summary.get("started_at", "n/a")
        ended = summary.get("ended_at", "n/a")
        errors_n = int(summary.get("errors") or 0)
        status_icon = "OK" if errors_n == 0 else "ATTENTION"

        cov = dict(coverage.iloc[0]) if not coverage.empty else {}
        mcov = dict(macro_cov.iloc[0]) if not macro_cov.empty else {}

        parts = [
            f"{status_icon} {title}",
            "",
            "Run summary",
            f"- Started: {started}",
            f"- Ended: {ended}",
            f"- Sources: {_fmt_int(summary.get('sources'))} | log rows: {_fmt_int(summary.get('log_rows'))}",
            f"- Rows added: {_fmt_int(summary.get('rows_added'))} | updated: {_fmt_int(summary.get('rows_updated'))}",
            f"- OK: {_fmt_int(summary.get('ok'))} | empty: {_fmt_int(summary.get('empty'))} | errors: {_fmt_int(summary.get('errors'))}",
            f"- Sum fetch/write duration: {_fmt_float(summary.get('duration_sec_sum'))}s",
            "",
            "By source",
            _md_table(by_source, ["source", "items", "added", "updated", "ok", "empty", "errors", "duration_s"]),
            "",
            "Coverage snapshot",
            f"- Series: {_fmt_int(cov.get('series'))} | avg score: {_fmt_float(cov.get('avg_score'))}",
            f"- Stalled: {_fmt_int(cov.get('stalled'))} | coverage errors: {_fmt_int(cov.get('coverage_errors'))}",
            f"- Observations: {_fmt_int(cov.get('observations'))}",
            f"- Macro panel indicators: {_fmt_int(mcov.get('indicators'))} | country coverage avg: {_fmt_float(mcov.get('avg_country_coverage_pct'))}% | stalled: {_fmt_int(mcov.get('stalled'))}",
            "",
            "Database totals",
            _md_table(totals, ["table_name", "rows", "series", "first_date", "last_date"]),
            "",
            "Slowest items",
            _md_table(slowest, ["source", "symbol", "status", "seconds", "added", "updated"], max_rows=10),
        ]

        if errors_n:
            parts.extend(["", "Errors", _md_table(errors, ["source", "symbol", "status", "error"], max_rows=25)])
        else:
            parts.extend(["", "Errors", "(none)"])

        if not stalled.empty:
            parts.extend(["", "Top stalled series", _md_table(stalled, ["symbol", "source", "last_date", "lag_days", "freq", "score", "status"], max_rows=20)])

        parts.extend(["", f"Generated at: {datetime.now().isoformat(timespec='seconds')}"])
        return title, "\n".join(parts)
    finally:
        con.close()


def save_report(title: str, content: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(c if c.isalnum() or c in "._-" else "_" for c in title.lower())
    safe_title = safe_title.strip("._-") or "market_data_hub_report"
    out = REPORT_DIR / f"{safe_title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out.write_text(content, encoding="utf-8")
    return out


def send_report_document(file_path: Path, *, token: str, chat_id: str, caption: str) -> None:
    blob = file_path.read_bytes()
    with TelegramClient.from_token(token) as client:
        client.send_document(
            chat_id=chat_id,
            document=blob,
            filename=file_path.name,
            caption=caption[:1024],
        )


def main() -> int:
    p = argparse.ArgumentParser(description="Send market_data_hub run report via Telegram")
    p.add_argument("--db", help="DuckDB path; defaults to market_data_hub settings")
    p.add_argument("--run-id", help="Specific run_id; defaults to latest")
    p.add_argument("--dry-run", action="store_true", help="Print and save report, but do not send Telegram message")
    p.add_argument("--save", action="store_true", help="Deprecated: reports are always saved before sending")
    args = p.parse_args()

    title, report = collect_report(args.db, args.run_id)
    out = save_report(title, report)
    print(f"Saved report: {out}")

    if args.dry_run:
        print(report)
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.", file=sys.stderr)
        print(f"Report was saved but not sent: {out}", file=sys.stderr)
        return 2

    send_report_document(out, token=token, chat_id=chat_id, caption=title)
    print(f"Sent Telegram report attachment: {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
