"""One sequential probe on a read-only SQLite backup, never the live database."""
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCE = Path(r'C:\ProgramData\InvestmentCommittee\live\db\macro_releases.sqlite')
COPY = ROOT / '.tmp' / 'macro-history-probe.sqlite'
UK = 'uk_gdp,uk_cpi,uk_labour_market,uk_total_pay,uk_retail_sales'
EU = 'ea_industrial_production,ea_retail_sales'

def coverage():
    with sqlite3.connect(COPY) as db:
        rows = db.execute("SELECT indicator_id, COUNT(DISTINCT reference_period), MIN(reference_period), MAX(reference_period) FROM value_vintages WHERE indicator_id LIKE 'uk_%' OR indicator_id IN ('ea_industrial_production_mm','ea_retail_sales_mm') GROUP BY indicator_id").fetchall()
        return {r[0]: list(r[1:]) for r in rows}

def main():
    from market_data_hub.macro_releases import collect
    from market_data_hub.macro_releases.http import HttpClient

    COPY.parent.mkdir(exist_ok=True)
    if COPY.exists():
        raise SystemExit('Probe copy already exists; refusing to overwrite it')
    with sqlite3.connect(SOURCE.as_uri() + '?mode=ro', uri=True) as source:
        with sqlite3.connect(COPY) as target:
            source.backup(target)
    with sqlite3.connect(COPY) as db:
        print('copy quick_check:', db.execute('PRAGMA quick_check').fetchone()[0], flush=True)
    print('before:', json.dumps(coverage(), sort_keys=True), flush=True)
    original = HttpClient.request
    counters = {}
    def counted(self, provider, url, **kwargs):
        old = self.session.request
        def request(*a, **kw):
            counters[provider] = counters.get(provider, 0) + 1
            return old(*a, **kw)
        self.session.request = request
        try:
            return original(self, provider, url, **kwargs)
        finally:
            self.session.request = old
    HttpClient.request = counted
    for label, families in [('UK', UK), ('Eurostat', EU)]:
        start = time.monotonic()
        before_counts = counters.copy()
        status = collect.main(['--db', str(COPY), '--families', families, '--since', '2018-01'])
        print(label, 'exit:', status, 'seconds:', round(time.monotonic()-start, 2), 'requests:', json.dumps({k: v-before_counts.get(k, 0) for k,v in counters.items() if v-before_counts.get(k, 0)}), flush=True)
        print('after ' + label + ':', json.dumps(coverage(), sort_keys=True), flush=True)
    with sqlite3.connect(COPY) as db:
        print('vintage kinds:', json.dumps(db.execute("SELECT indicator_id, revision_kind, COUNT(*) FROM value_vintages WHERE indicator_id LIKE 'uk_%' OR indicator_id IN ('ea_industrial_production_mm','ea_retail_sales_mm') GROUP BY indicator_id, revision_kind ORDER BY 1,2").fetchall()), flush=True)

if __name__ == '__main__':
    main()
