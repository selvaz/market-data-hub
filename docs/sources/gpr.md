# Caldara–Iacoviello Geopolitical Risk index

The [authors' GPR page](https://www.matteoiacoviello.com/gpr.htm) describes
this newspaper-based index and links to the underlying research. Credit Dario
Caldara and Matteo Iacoviello and cite their 2022 paper, the source page and
the download date when using the data. The page states that its material is
open access under [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/):
provide appropriate credit, a licence link and an indication of changes.

The recent index uses ten newspapers and starts in 1985. The daily workbook
provides `GPRD`, `GPRD_ACT` and `GPRD_THREAT`; the monthly workbook provides
`GPR`, `GPRA` and `GPRT` (acts and threats are collected if present). Monthly
dates are the first day of the reference month, as published. These are global
indices, not country-specific measures.

The authors update daily data on Mondays and monthly data at the start of each
month, with federal-holiday deferrals. Recent readings are preliminary and
older values can also be revised. The collector refreshes the complete published
workbooks and never fills missing dates. It stores observations in `macro_series`
with source `gpr`, retrieval time in `updated_at`, and hub-observed changes in
`macro_series_vintage`. Vintage dates are ingestion dates, not source publication
dates; the files do not establish when each historical value became public, so
these data alone do not support a point-in-time backtest.

Downloads: [daily workbook](https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls)
and [monthly workbook](https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls).
Run `python run_gpr.py --db <absolute-path>`; the registrar
`setup_gpr_scheduler.ps1 -Database <absolute-path> -Python <python>` creates a
disabled daily 14:10 Pacific task. Registration and enabling require owner approval.
