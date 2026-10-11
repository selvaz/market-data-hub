# F05 free macro and commodity sources

The configured universe adds six verified FRED IDs, five Yahoo futures aliases,
and the NY Fed ACM ten-year **monthly** series. Configuration is not evidence
that production history has been loaded. F05 validation uses recorded responses
and throwaway databases only.

## FRED metadata and publication lag

All six IDs returned HTTP 200 with matching titles on 2026-10-11. Published
units, frequency and seasonal adjustment are recorded in
`tests/fixtures/fred_f05_series_metadata.json`. These entries use the existing
`macro_series.yaml → run_fred → macro_series` path. The FRED adapter preserves
their declared native frequency; older entries without one keep their existing
behavior. Values retain the provider's units without scaling.

| ID / official metadata | Meaning | Unit | Native frequency | max_lag_days |
|---|---|---|---|---|
| [THREEFYTP10](https://fred.stlouisfed.org/series/THREEFYTP10) | Kim-Wright ten-year zero-coupon term premium | Percent | D | 10 |
| [BAMLEMCBPIOAS](https://fred.stlouisfed.org/series/BAMLEMCBPIOAS) | ICE BofA EM Corporate Plus OAS | Percent | D | Daily default: 3 |
| [BAMLEMHBHYCRPIOAS](https://fred.stlouisfed.org/series/BAMLEMHBHYCRPIOAS) | ICE BofA high-yield EM Corporate Plus OAS | Percent | D | Daily default: 3 |
| [CES0500000003](https://fred.stlouisfed.org/series/CES0500000003) | Average hourly earnings, all employees, total private | Dollars per Hour | M | 70 |
| [GASDESW](https://fred.stlouisfed.org/series/GASDESW) | US retail diesel sales price | Dollars per Gallon | W, ending Monday | 10 |
| [DDFUELNYH](https://fred.stlouisfed.org/series/DDFUELNYH) | NY Harbor ultra-low-sulfur No. 2 diesel spot price | Dollars per Gallon | D | 10 |

CES0500000003 is seasonally adjusted; the other five are not. Earnings are a
wage **level**, not a growth rate. EM OAS and term premia are percent values:
1.58 represents 158 basis points, not 1.58 basis points. Corporate Plus covers
non-sovereign corporate and quasi-government debt; these are not country EMBI
sovereign spreads. `area: Emerging Markets` describes the index exposure;
`country: US` retains FRED's US series classification.

The ten-day override allows the weekly publication cadence of the
[Kim-Wright estimates](https://www.federalreserve.gov/econres/economic-research-data.htm),
weekly diesel survey and weekly publication of
[EIA spot prices](https://www.eia.gov/dnav/pet/pet_pri_spt_s1_d.htm), while retaining
their observation frequencies. The 70-day earnings limit accounts for the
month-start observation label, publication early in the following month, and
the next monthly release cycle. Limits are calendar days: exactly the limit
is healthy; one day beyond it is stalled; missing history remains stalled.

Both ICE pages explicitly limit available FRED history to **three years from
April 2026**. A request starting in 2000 cannot obtain the older observations
from FRED. Long-horizon percentiles/backtests must use the actual stored span
and disclose this limitation; longer history is an ICE sourcing candidate.

The earnings CSV fixture is an actual public FRED response for July–September
2026, recorded through `fredgraph.csv` with `id`, `cosd` and `coed` parameters.

## Yahoo continuous futures

These use the same `tickers.yaml → run_yahoo → yahoo_direct → prices_daily`
path as `^VIX`, `DX-Y.NYB` and `USDJPY=X`. Each is an instrument with
`asset_class: COMMODITIES`, `instrument_type: futures_continuous` and daily
frequency. Quote metadata is explicit in config; the TTF listing identity uses
EUR rather than the curated universe's usual USD default.

| Alias | Exposure | Quote unit |
|---|---|---|
| BZ=F | Brent crude | USD per barrel |
| CL=F | WTI crude | USD per barrel |
| GC=F | Gold | USD per troy ounce |
| TTF=F | Dutch TTF natural gas | EUR per MWh |
| NG=F | Henry Hub natural gas | USD per MMBtu |

Yahoo chart-v8 returned all five aliases on 2026-10-11. Provider currencies and
names are recorded in `tests/fixtures/yahoo_f05_series_metadata.json`, and the
CL=F chart response for 2026-10-05 through 2026-10-09 is recorded separately.
Yahoo labels GC=F `ALTSYMBOL` in this response and the others `FUTURE`; the
configured futures classification does not depend on that inconsistent label.
The [CME TTF contract rules](https://www.cmegroup.com/rulebook/NYMEX/11/1161.pdf)
confirm EUR per MWh for the underlying TTF calendar-month futures.

These are provider-selected front-month aliases. The active contract can
change on a roll, producing price discontinuities. They are not fixed maturity
contracts, a full futures curve, a documented back-adjusted continuous series,
or total-return indices. `adj_close` follows Yahoo's payload (or the existing
close fallback) and does not guarantee that roll jumps have been removed.
Returns across rolls can combine a market move with a contract switch; curve,
carry and roll-return studies need individual contracts and an explicit roll
rule. Futures prices can be negative, so do not apply positive-price assumptions
to WTI history.

## NY Fed ACM: official monthly CSV

The [NY Fed term-premia page](https://www.newyorkfed.org/research/data_indicators/term-premia-tabs)
loads a simple public
[chart CSV](https://www.newyorkfed.org/medialibrary/media/research/data_indicators/acmPlot_data.csv).
It returned HTTP 200 on 2026-10-11 with columns `RunDates,TERMYld,ACMFITYld,GSWYld`
and monthly observations through 2026-09-30. The
[official chart code](https://www.newyorkfed.org/medialibrary/research/interactives/term-premia/js/main-es2015.js)
maps `TERMYld` to the ten-year ACM term premium and labels the axis Percent.
The fixture `nyfed_acm_metadata.json` records this evidence; the CSV fixture
contains the original header and the last three provider rows.

`NYFED_ACM_TP10_MONTHLY` is a **local hub ID**, not a FRED ID. Its config lives
under `nyfed_acm` in `macro_series.yaml`. The `nyfed_acm` source participates in
normal daily/backfill orchestration, uses the existing macro upsert, vintage,
download-log and coverage paths, and is selectable with `--sources nyfed_acm`.
It preserves last-business-day dates and monthly frequency, records
`source: nyfed_acm`, and uses `max_lag_days: 45`. The small complete CSV is
downloaded once per run; rows are filtered to the requested incremental window
with the existing 95-day macro revision tail. A changed/missing CSV value column
raises a fault rather than silently ingesting a fitted yield as a term premium.

The separate official
[ACM Excel download](https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls)
also returned HTTP 200 with an XLS signature. It remains a candidate for daily
ACM and the other maturities: those are not supplied by this monthly chart CSV.
Model estimates can be revised and differ from Kim-Wright; keep their identities
separate. Availability of future updates to this chart endpoint is not guaranteed.
