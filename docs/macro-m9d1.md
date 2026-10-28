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

NBS economic release editions and `published_at` use the first official
publication date and time in China Standard Time (`Asia/Shanghai`). For the
calendar-covered monthly series this is the official NBS release calendar,
consistently for past and future releases. English translations may carry a
PubDate on that date or one day later; both are validated against the same
calendar edition and clock. Earlier or more delayed pages require review.
The original English PubDate remains in the saved source document; retrieval
time and URL filenames never establish the economic release date.

| Family | Collected measure | Issuer / source class | Reviewed editions | Golden publications |
|---|---|---|---|---|
| cn_cpi | National CPI y/y, NSA, % | NBS English / official | Sep 9, Oct 14; Nov 9, Dec 9 scheduled | Aug and Sep 2026 |
| cn_nbs_pmi | Official manufacturing PMI, SA, index (50 neutral) | NBS English / official | Aug 31, Sep 30; Oct 31, Nov 30, Dec 31 scheduled | Aug and Sep 2026 |
| cn_activity | Industrial value-added output and consumer-goods retail sales, monthly y/y, NSA, % | NBS English national economy / official | Sep 15, Oct 19; Nov 16, Dec 15 scheduled | Aug and Sep 2026 (4 cases) |
| cn_gdp | Real GDP cumulative y/y, NSA, % | NBS English national economy and original Chinese accounting tables / official | Jul 16 (Q2), Oct 19 first Q3 announcement, Oct 20 Q3 accounting; next edition January 2027 | Q2 4.7%, Q3 4.6% on both Oct 19 and 20 (3 cases) |
| jp_gdp | Real GDP q/q, SA, % | Cabinet Office ESRI bilingual rate CSV / official | Aug 17 preliminary, Sep 8 revised; Nov 16 preliminary, Dec 8 revised scheduled | Q2 first 0.3%, second 0.4% |
| jp_cpi | National headline and ex-fresh-food CPI y/y, NSA, % | Statistics Bureau / e-Stat English monthly reports / official | Sep 18, Oct 23; Nov 20, Dec 18 scheduled | Aug and Sep 2026 (4 cases) |
| jp_tokyo_cpi | Ku-area of Tokyo ex-fresh-food CPI y/y, NSA, % | Statistics Bureau / e-Stat English monthly reports / official | Aug 28, Oct 2 preliminary; Oct 30, Nov 27, Dec 25 scheduled | Aug 1.8%, Sep 2.7% |
| jp_tankan | Large manufacturers current business-conditions DI, percentage points | BoJ English summary / official | Jul 1, Oct 1; Dec 14 scheduled | Q2 22, Q3 24 |
| jp_trade_balance | Customs-cleared goods balance, NSA, billion JPY | MOF / Japan Customs English XML releases / official | Sep 29 revised, Oct 21 preliminary; Oct 29, Nov 18/27, Dec 16/25 scheduled | Aug -1,111.913; Sep -536.558 |

Customs prints the balance in millions of JPY; division by 1,000 is only a unit
conversion to the catalogue's billions. Exports minus imports is never computed.
The September 29 report explicitly says exports detailed / imports provisional
(HS-code basis), so it is `revised`, not the catalogue's generic `final` stage.
The calendar's detailed releases receive this same honest stage; fully final
imports/balances are not certified from a partially provisional publication.

The official XML and CSV formats avoid the optional AES backend required by
the issuers' encrypted PDF editions. ESRI's CSV has English measure headers;
its exact link is checked against the English edition menu, which is bound to
the date and stage in the issuer's annual release archive. The CSV must print
real, seasonally adjusted, non-annualized quarter-on-quarter growth, and its
last quarter must match the reviewed edition. No rate is calculated.

MOF's English XML retains the printed release date, reference month and stage.
Its native tag names map `sashihiki/sogakutonen` to the current headline balance
and `chiikikunisogakuinfo/sashihikikagakue` to each area's balance. The parser
requires the unique `Grand Total` row (area code `0`) and agreement with the
headline balance; the triangle deficit sign is normalized to minus. The
publisher's linked `d1201e.xsl` stylesheet is separately checked for millions
of JPY and the non-seasonally-adjusted Census basis. The schedule PDF remains
saved with a hashed text transcript linked to its original SHA-256, allowing
offline calendar checks without an optional PDF decryption dependency.

Tankan is an official BoJ survey and is in scope separately from policy
decisions. The source table does not label seasonal adjustment; its metadata
therefore says `not-stated`, rather than certifying the catalogue's unconfirmed
`SA` flag. The printed actual DI is collected directly, never the forecast or
the change column. This source/catalogue metadata difference is explicit.

Tokyo tables include the next month's mid-month preliminary figure in a report
listed under the preceding national survey month. The English note explicitly
labels the latest month preliminary; the parser requires that note and the
exact Tokyo table header. Tokyo core reuses the approved `jp_tokyo_cpi` concept.

e-Stat CPI files are bound to reviewed monthly-report file IDs. Collection also
checks the issuer listing's exact geography, monthly table, survey month,
reviewed publication counter (national 1, Tokyo preliminary addition 2) and
calendar-matching date. An update timestamp alone
does not authorize an edition. Only the explicitly printed annual-change sheet
is read; no rates are computed from index levels. The 2025-base revision is
explicit in both the source listing and table headers.

The brief explicitly requests preliminary Q3 GDP. Its stage is `preliminary`,
correcting the external catalogue's generic `final` stage without changing the
indicator identity. The catalogue's **cumulative** definition is preserved:
Q3's 4.6% is January–September, distinct from standalone Q3's 4.5%.
The original Chinese GDP table is used to preserve the October 20 edition;
the English accounting translation is dated October 21. The first GDP
announcement in the October 19 English national-economy release is retained
as its own earlier edition, with the same printed cumulative 4.6%. Header mapping:
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
Calendar: <https://www.stat.go.jp/english/data/cpi/1582.htm>.

## Verification on 2026-10-27

`scripts/verify_macro_m9d1.py` returned `ok` on a stable production-byte snapshot
and SQLite backup under `D:\Temp\mdh-m9d1-formats-live-20261027\mdh-m9d1-ie3hvmmo`.
It added 23 vintages across all nine families; the second collection added zero.
The source main/WAL/SHM fingerprints and all pre-existing vintage fingerprints
were unchanged; the copy's SQLite integrity check returned `ok`.

The 2026-10-19..27 UTC read window contained official cards for China industrial
output (5.5%) and retail sales (1.1%), China cumulative Q3 GDP (4.6%, October 19
first announcement and October 20 preliminary accounting), Japan trade
(-536.558 billion JPY), and Japan national headline
and core CPI (both 2.1%). Japan's October 23 08:30 JST CPI release appears at
October 22 23:30 UTC; it is national CPI, separate from Tokyo preliminary CPI.

Approved-card coverage is 93/93, with `unmapped: []` and no card gaps. The
blocked METI collection and absent catalogue rows above remain explicit scope
limitations. Future permissions extend through December 31; any as-yet
unreviewed table format is rejected even if a calendar date has arrived.

## Edition discovery and run outcomes (review P2-2)

New publication IDs are discovered from issuer listings: MOF's dated English
press-release rows, e-Stat's monthly-report index and file listing, ESRI's dated
annual archive and English CSV menu, and the NBS English release listing.
Discovery never guesses an ID or uses a calendar as numeric evidence. Links
must remain on the expected issuer path and carry the expected period, stage
and format. The publication parser independently validates the actual table.
Runtime URL bindings are local to the fetch; reviewed registries are unchanged.

A missing listing entry during the six hours after the scheduled release is
reported as `scheduled` with reason `not_yet_published`. The same grace applies
when a discovered edition-specific URL returns HTTP 404. Beyond grace, an
edition whose URL cannot be discovered is `scheduled_unreviewed`; a discovered
publication with invalid metadata/format, or an expired 404, is `needs_review`.
These edition outcomes appear in the run report's `scheduled_editions` list.
Rejected editions produce no observations, while valid editions in the same
family are still persisted. Transport/authentication failures still stop the
affected issuer family. Unknown NBS native GDP templates remain unreviewed;
the next quarterly edition is outside the 2026 calendar horizon.

Quarterly `since` comparisons use the quarter's ending month. MOF XML must
reference the same `d1201e.xsl` stylesheet whose units and NSA basis are checked.

## NBS publication convention (review P2-1)

The reviewed CPI releases are September 9 and October 14 at 09:30 CST; their
English PubDates are September 10 and October 15. Manufacturing PMI releases
are August 31 and September 30 at 09:30 CST; their English PubDates are
September 1 and October 1. All four now use the calendar dates as their
edition and `published_at` (01:30 UTC). The November/December CPI and
October–December PMI permissions use the identical rule. A one-day English
listing/page lag is accepted only with matching period, measure and clock;
the parser never treats that lag as an additional vintage/edition. Native GDP
accounting publications retain their separately printed first-publication
dates and clocks, distinct from the national-economy announcement edition.
