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
| cn_nbs_pmi | Official manufacturing PMI, SA, index (50 neutral) | NBS English / official | Sep 1, Oct 1; Oct 31, Nov 30, Dec 31 scheduled | Aug and Sep 2026 |
| cn_activity | Industrial value-added output and consumer-goods retail sales, monthly y/y, NSA, % | NBS English national economy / official | Sep 15, Oct 19; Nov 16, Dec 15 scheduled | Aug and Sep 2026 (4 cases) |
| cn_gdp | Real GDP cumulative y/y, NSA, % | NBS original Chinese accounting table / official | Jul 16 (Q2), Oct 20 (Q3); next edition January 2027 | Q2 4.7%, Q3 4.6% |
| jp_gdp | Real GDP q/q, SA, % | Cabinet Office ESRI English summaries / official | Aug 17 preliminary, Sep 8 revised; Nov 16 preliminary, Dec 8 revised scheduled | Q2 first 0.3%, second 0.4% |
| jp_cpi | National headline and ex-fresh-food CPI y/y, NSA, % | Statistics Bureau / e-Stat English monthly reports / official | Sep 18, Oct 23; Nov 20, Dec 18 scheduled | Aug and Sep 2026 (4 cases) |

e-Stat CPI files are bound to reviewed monthly-report file IDs. Collection also
checks the issuer listing's exact geography, monthly table, survey month,
first-publication counter and calendar-matching date. An update timestamp alone
does not authorize an edition. Only the explicitly printed annual-change sheet
is read; no rates are computed from index levels. The 2025-base revision is
explicit in both the source listing and table headers.

The brief explicitly requests preliminary Q3 GDP. Its stage is `preliminary`,
correcting the external catalogue's generic `final` stage without changing the
indicator identity. The catalogue's **cumulative** definition is preserved:
Q3's 4.6% is January–September, distinct from standalone Q3's 4.5%.
The original Chinese GDP table is used to preserve the October 20 edition;
the English accounting translation is dated October 21. Header mapping:
`绝对额(亿元)` = current-price levels; `比上年同期增长(%)` = annual growth;
`三季度` = standalone Q3; `前三季度` = first three quarters; `上半年` = first half.
Only the last GDP cell (cumulative growth at constant prices) is collected.

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
