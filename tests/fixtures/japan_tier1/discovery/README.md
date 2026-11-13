# Japan discovery-page fixtures

Live issuer pages captured on 2026-11-13 (UTC) for edition discovery tests:
latest-release pages (period + issuer release date/time) and annual release
schedules. Sources:

| File | URL |
|---|---|
| meti_iip_latest.html | https://www.meti.go.jp/english/statistics/tyo/iip/index.html |
| meti_iip_data_page.html | https://www.meti.go.jp/english/statistics/tyo/iip/b2020_result-2.html |
| stat_lfs_latest.html | https://www.stat.go.jp/english/data/roudou/results/month/index.html |
| stat_lfs_schedule.html | https://www.stat.go.jp/english/data/roudou/1543.html |
| stat_fies_latest.html | https://www.stat.go.jp/english/data/kakei/156.html |
| stat_cpi_latest.html | https://www.stat.go.jp/english/data/cpi/1581-z.html |
| stat_cpi_schedule.html | https://www.stat.go.jp/english/data/cpi/1582.html |
| esri_gdp_schedule.html | https://www.esri.cao.go.jp/en/sna/kouhyou/kouhyou_top.html |
| boj_tankan_listing.html | https://www.boj.or.jp/en/statistics/tk/index.htm |
| oecd_cli_last1.json | OECD SDMX DF_CLI JPN, lastNObservations=1, format=jsondata |

METI returns HTTP 403 to non-browser user agents; the collector must send a
browser-like User-Agent and Accept-Language: en.
