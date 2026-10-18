# Sovereign layer fixtures (captured 2026-10-18)

All network captures used Python requests. No databases were opened by capture
scripts. Treasury files cover each year 1990-2026 from the annual daily par-curve
CSV route; mof_history.csv and mof_current.csv are the MoF official archive and
current-month files. boc_*.json contain the full Valet observation exports.

boe_10Y.csv is the earlier HTTP 403 response captured with requests' default
User-Agent. The review-fix capture used the hub's market-data-hub/0.1 User-Agent
and each series' first-observation date. All three requests returned HTTP 200.
boe_5Y_live.csv has 8,305 rows from 1993-12-01, boe_10Y_live.csv has 8,327 rows
from 1993-11-01, and boe_20Y_live.csv has 6,768 rows from 2000-01-04. Each ends
on 2026-10-14. These are the positive live fixtures used for ingestion tests.
boe_synthetic.csv is a hand-authored, **synthetic** positive parser fixture. Its
values and dates are not live evidence and must never enter backfill row counts.
BoE's catalogue verifies IUDSNPY=5y, IUDMNPY=10y, IUDLNPY=20y; no 2y/30y daily
IADB par code was verified. See docs/sovereign-layers.md for source links.

tv_close.json records discovery of 02Y/05Y/10Y/30Y across 30 markets (Argentina
returned no data). tv_snapshot.json records close/time/timezone; the daily time
is a bar OPEN and cannot safely date US closes. tv_snapshot_minute.json records
close/time|1/timezone and is the ingestion fixture. tv_*.json preserve further
date-field probes, including unsupported fields returning null. ID10Y retains
its stale 2026-10-15 date rather than being relabelled Friday.

The fixture inventory has 99,824 Treasury, 164,002 MoF and 25,789 BoC distinct
non-missing observations, plus 106 current snapshots and 23,400 live BoE
observations. These are empty-destination counts, not production deltas.
