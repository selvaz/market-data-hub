# Japan and China official publications (M9d-1)

Base: v0.3.9 (`a4d4e6a`). The external catalogue supplied with the brief has
Japan national CPI, GDP, Tankan, industrial production and trade, and China GDP,
CPI, official NBS PMI, industrial production and retail sales. Policy decisions
and Caixin are excluded. Tokyo core CPI is an explicit brief addition. No rows
for Japan labour, household spending or retail, or China PPI, fixed investment or
trade were present in that catalogue; no definitions for those are invented.

The checkout's approved classification is v2.3, including owner-approved v2.1
concepts. Existing exact `cat:` identifiers already present in the approved
table are reused. Missing concepts are reported rather than created.

## Source and edition policy

Dates authorize fetching; a calendar never supplies a numeric observation.
All data are issuer-printed levels or rates, with stages separate from measure
identities. Unknown dates, source URLs, columns, clocks and titles fail closed
with `needs review`. Reviewed calendars end on 2026-12-31.

NBS English translations can post after the domestic release. Saved English
publications retain their own publication dates and clocks. The domestic
calendar supplies future permissions, which still require exact issuer
publication metadata before certification; a delayed unreviewed English edition
is not silently assigned the scheduled domestic date.

| Family | Collected measure | Issuer / source class | Reviewed editions | Golden publications |
|---|---|---|---|---|
| cn_cpi | National CPI y/y, NSA, % | NBS English / official | Sep 10, Oct 15; Nov 9, Dec 9 scheduled | Aug and Sep 2026 |

Calendar: <https://www.stats.gov.cn/english/PressRelease/ReleaseCalendar/202512/t20251226_1962154.html>.
Each saved fixture has its issuer URL, retrieval timestamp and SHA-256.

## Availability findings

METI's catalogue URL returned HTTP 403 from this server on 2026-10-27.
Industrial production collection is stopped for this issuer; no alternative
endpoint, redistributor, browser session or bot-protection bypass is attempted.

The Statistics Bureau CPI calendar lists October Tokyo preliminary CPI for
2026-10-30, not 2026-10-22. A numeric Tokyo card dated October 22 would require
an issuer publication proving that edition.
Calendar: <https://www.stat.go.jp/english/data/cpi/1582.html>.
