# Worklist period-format investigation — 19 October 2026 inventory

> Historical first-pass results at bfe09c0. The [independent review round 1](worklist-period-formats-review-round1.md)
> supersedes the survey, window, quarter and full-date inference rules and records
> the revised preview (448 pending; six original mappings now pending).

Read-only SQLite backup of `C:/ProgramData/InvestmentCommittee/live/db/macro_releases.sqlite` to `.tmp/macro_copy.sqlite`. Both CLI dry runs use **2026-10-20T06:26:46.535002+00:00** and a 14-day lateness window. SQLite source opened with `mode=ro`; only the local copy and in-memory previews are writable. No production apply, deployment, scheduled task or network collection was performed.

## Findings

The inventory describes 100 **items** and 83 **raw reading shapes**. Shape counts are reading counts, overlap across items, and must not be summed as unique item counts. The resolver actually uses `raw_period` from sightings and `reference_period` from observations. Its v2 loop lets any nonempty unparseable label veto an independently valid period.

Of the 100, **36** fail solely on recognized yearless labels under the original parser (including labels that have no independently explicit companion). Others need the bounded suffix/punctuation/event/quarter/window formats or a precise non-period reason below. Plain `Sep 2026`, `September 2026`, `agosto de 2026`, `Septiembre de 2026` and `Sept. 2026` already parse in v2. `September, 2026` does not. No fuzzy correction is added for `Septermber`.

The placeholder hypothesis is not the actual cause in this snapshot: **zero** of the 100 decisions passes `legacy-occurrence:N` or literal `missing` to the period parser. The observation placeholder is a stored `period_key`, present on **95** items; the five unlinked sightings have no such observation key. Sighting keys can contain `unknown:missing`. The resolver selects `raw_period` or `reference_period` instead. V3 nevertheless explicitly ignores both labels, with a regression test proving they cannot veto an explicit period. Blank/`d:` labels remain non-periods.

Catalogue frequency and definition govern semantics. Quarterly surveys use the quarter containing the explicitly named survey month. Event/meeting descriptions use the explicitly named event month, including day ranges, rather than their later release date. Monthly policy decisions use their named month. Monthly YTD/three-month measures may use their explicit window endpoint as a reporting index; their measure remains cumulative/rolling. Brazil unemployment lacks a rolling-window definition in this catalogue and remains pending. First-half-month CPI uses the explicit half-month end (15th) only with the corresponding catalogue definition.

## Original 100 outcomes

| Status / reason | Items |
| --- | ---: |
| `complete/catalogue_period_mapped` | 68 |
| `complete/late_value_mapped_unverified` | 9 |
| `pending/overdue` | 2 |
| `pending/period_fiscal_year_ambiguous` | 1 |
| `pending/period_format_unrecognised` | 1 |
| `pending/period_is_rolling_window` | 3 |
| `pending/period_year_missing` | 12 |
| `terminal/period_is_document_identifier` | 3 |
| `terminal/period_is_maturity_date` | 1 |

77 complete mappings, four precise terminals, and 19 pending. Two pending items have explicit periods but retain the unchanged overdue policy. No official values are manufactured.

## Same-cutoff dry-run comparison

| Status / reason | Stored production state | V2 dry run | V3 dry run |
| --- | ---: | ---: | ---: |
| `complete/catalogue_period_mapped` | 332 | 384 | 454 |
| `complete/late_value_mapped_unverified` | 245 | 245 | 254 |
| `complete/official_vintage_linked` | 2 | 2 | 2 |
| `pending/evidence_horizon_before_deadline` | 75 | 76 | 76 |
| `pending/overdue` | 49 | 48 | 50 |
| `pending/period_fiscal_year_ambiguous` | 0 | 0 | 1 |
| `pending/period_format_unrecognised` | 100 | 111 | 7 |
| `pending/period_frequency_mismatch` | 27 | 27 | 27 |
| `pending/period_is_rolling_window` | 0 | 0 | 6 |
| `pending/period_year_missing` | 0 | 0 | 12 |
| `pending/release_in_future` | 447 | 263 | 263 |
| `terminal/catalogue_jurisdiction_mismatch` | 166 | 186 | 186 |
| `terminal/conflicting_reference_periods` | 2 | 2 | 2 |
| `terminal/failed_run` | 607 | 607 | 607 |
| `terminal/outside_catalogue_scope` | 1161 | 1256 | 1256 |
| `terminal/period_is_document_identifier` | 0 | 0 | 3 |
| `terminal/period_is_maturity_date` | 0 | 0 | 1 |
| `terminal/period_not_derivable` | 27 | 33 | 33 |
| **All pending** | **698** | **525** | **442** |

The UTC date is now 20 October (19 October Pacific). At this cutoff 184 formerly future items are due, so the stored 447 future releases become 263 in both previews. The net period-rule improvement is **83 fewer pending** than v2 at the same cutoff. Of the remaining 442, **179 are not future releases**, below the eventual 200 target if the future releases subsequently resolve. The total contract is not yet satisfied: 442 is still above 200, and future resolution has not been simulated.

## Supplied shape → handling → count

Mappings below show representative canonical periods for each observed catalogue context. A partial label is ignored only when it agrees with one independently explicit period on the same item; alone it retains `period_year_missing`. Window mappings require their matching definition. See rule 3 in [macro-releases.md](macro-releases.md).

| Shape | Example | Why v2 fails / already works | V3 handling | Reading count |
| --- | --- | --- | --- | ---: |
| `legacy-occurrence:999` | `legacy-occurrence:103` | not passed to the resolver parser | ignored non-period (not passed to v2 parser in this snapshot) | 254 |
| `Sep 9999` | `Sep 2026` | already parses; a companion label vetoes the item | mapped `2026-09` | 69 |
| `missing` | `missing` | not passed to the resolver parser | ignored non-period (not passed to v2 parser in this snapshot) | 63 |
| `September 9999` | `September 2026` | already parses; a companion label vetoes the item | mapped `2026-09` | 56 |
| `Agosto` | `Agosto` | year absent; v2 treats this as unrecognized | `period_year_missing` | 30 |
| `agosto de 9999` | `agosto de 2026` | already parses; a companion label vetoes the item | mapped `2026-08` | 18 |
| `Agosto de 9999` | `Agosto de 2026` | already parses; a companion label vetoes the item | mapped `2026-08` | 13 |
| `Jan-Aug 9999` | `Jan-Aug 2026` | no full-match rule for this text | mapped `2026-08` | 12 |
| `Sept.` | `Sept.` | year absent; v2 treats this as unrecognized | `period_year_missing` | 12 |
| `September 9999 Survey` | `September 2026 Survey` | no full-match rule for this text | mapped `2026-Q3` | 12 |
| `legacy-occurrence:99` | `legacy-occurrence:13` | not passed to the resolver parser | ignored non-period (not passed to v2 parser in this snapshot) | 11 |
| `Septiembre` | `Septiembre` | year absent; v2 treats this as unrecognized | `period_year_missing` | 10 |
| `September` | `September` | year absent; v2 treats this as unrecognized | `period_year_missing` | 9 |
| `week ending 9999-99-99` | `week ending 2026-10-09` | already parses; a companion label vetoes the item | mapped `2026-10-02`; mapped `2026-10-09` | 8 |
| `January to September 9999` | `January to September 2026` | no full-match rule for this text | mapped `2026-09` | 8 |
| `September, 9999` | `September, 2026` | no full-match rule for this text | mapped `2026-09` | 7 |
| `As on October 9, 9999` | `As on October 2, 2026` | no full-match rule for this text | mapped `2026-10-02`; mapped `2026-10-09` | 7 |
| `October 9, 9999` | `October 2, 2026` | already parses; a companion label vetoes the item | mapped `2026-10-02`; mapped `2026-10-09` | 7 |
| `October 9–9, 9999` | `October 5–7, 2026` | no full-match rule for this text | mapped `2026-10` | 6 |
| `Agosto 9999` | `Agosto 2026` | already parses; a companion label vetoes the item | mapped `2026-08` | 6 |
| `semana al 9 de octubre de 9999` | `semana al 9 de octubre de 2026` | no full-match rule for this text | mapped `2026-10-09` | 6 |
| `Sept. 9999` | `Sept. 2026` | already parses; a companion label vetoes the item | mapped `2026-09` | 6 |
| `Sep 9999 meeting` | `Sep 2026 meeting` | no full-match rule for this text | mapped `2026-09` | 5 |
| `Oct 99, 9999` | `Oct 09, 2026` | already parses; a companion label vetoes the item | mapped `2026-10-09` | 5 |
| `Sep 9999 Survey` | `Sep 2026 Survey` | no full-match rule for this text | mapped `2026-Q3` | 4 |
| `quarter ending Aug 9999` | `quarter ending Aug 2026` | no full-match rule for this text | `period_is_rolling_window` | 4 |
| `Q9 9999 (Sep survey)` | `Q3 2026 (Sep survey)` | no full-match rule for this text | mapped `2026-Q3` | 4 |
| `Oct 9999 MPC meeting` | `Oct 2026 MPC meeting` | no full-match rule for this text | mapped `2026-10` | 4 |
| `Septiembre de 9999` | `Septiembre de 2026` | already parses; a companion label vetoes the item | mapped `2026-09` | 4 |
| `Issue 9999` | `Issue 1413` | no full-match rule for this text | `period_is_document_identifier` | 4 |
| `9 months to Aug 9999` | `3 months to Aug 2026` | no full-match rule for this text | mapped `2026-08` | 4 |
| `August` | `August` | year absent; v2 treats this as unrecognized | `period_year_missing` | 4 |
| `January–September 9999` | `January–September 2026` | no full-match rule for this text | mapped `2026-09` | 4 |
| `Primera quincena de Septiembre de 9999` | `Primera quincena de Septiembre de 2026` | no full-match rule for this text | mapped `2026-09-15` | 3 |
| `Issue Number 999` | `Issue Number 188` | no full-match rule for this text | `period_is_document_identifier` | 3 |
| `three months to August 9999` | `three months to August 2026` | no full-match rule for this text | mapped `2026-08` | 3 |
| `Third Quarter of 9999` | `Third Quarter of 2026` | already parses; a companion label vetoes the item | mapped `2026-Q3` | 3 |
| `January-August 9999` | `January-August 2026` | no full-match rule for this text | mapped `2026-08` | 3 |
| `trimestre móvel Jun-Aug 9999` | `trimestre móvel Jun-Aug 2026` | no full-match rule for this text | `period_is_rolling_window` | 2 |
| `Jun-Aug 9999` | `Jun-Aug 2026` | no full-match rule for this text | `period_is_rolling_window`; mapped `2026-08` | 2 |
| `Sep 9999 survey` | `Sep 2026 survey` | no full-match rule for this text | mapped `2026-Q3` | 2 |
| `Apr-Jun 9999 (Q9 9999)` | `Apr-Jun 2026 (Q2 2026)` | no full-match rule for this text | mapped `2026-Q2` | 2 |
| `Oct 9999 MPC` | `Oct 2026 MPC` | no full-match rule for this text | mapped `2026-10` | 2 |
| `Oct 9999 MPC meeting (Oct 9-9)` | `Oct 2026 MPC meeting (Oct 5-7)` | no full-match rule for this text | mapped `2026-10` | 2 |
| `Oct 9999 MPC Meeting` | `Oct 2026 MPC Meeting` | no full-match rule for this text | mapped `2026-10` | 2 |
| `Sep 9999 FOMC meeting` | `Sep 2026 FOMC meeting` | no full-match rule for this text | mapped `2026-09` | 2 |
| `Sep 99-99, 9999 FOMC meeting` | `Sep 15-16, 2026 FOMC meeting` | no full-match rule for this text | mapped `2026-09` | 2 |
| `al 9 de octubre de 9999` | `al 9 de octubre de 2026` | no full-match rule for this text | mapped `2026-10-09` | 2 |
| `9-99 September 9999` | `9-10 September 2026` | no full-match rule for this text | mapped `2026-09` | 2 |
| `septiembre de 9999` | `septiembre de 2026` | already parses; a companion label vetoes the item | mapped `2026-09` | 2 |
| `Week ending Sep 99` | `Week ending Sep 30` | year absent; v2 treats this as unrecognized | `period_year_missing` | 2 |
| `third quarter` | `third quarter` | year absent; v2 treats this as unrecognized | `period_year_missing` | 2 |
| `week ending October 99` | `week ending October 10` | year absent; v2 treats this as unrecognized | `period_year_missing` | 2 |
| `July 99–99, 9999 meeting` | `July 30–31, 2026 meeting` | no full-match rule for this text | mapped `2026-07` | 1 |
| `Jul 99-99, 9999 meeting` | `Jul 30-31, 2026 meeting` | no full-match rule for this text | mapped `2026-07` | 1 |
| `September 99–99, 9999 meeting` | `September 17–18, 2026 meeting` | no full-match rule for this text | mapped `2026-09` | 1 |
| `9/9999` | `8/2026` | already parses; a companion label vetoes the item | mapped `2026-08` | 1 |
| `quarter ended Aug 9999` | `quarter ended Aug 2026` | no full-match rule for this text | `period_is_rolling_window` | 1 |
| `Jul 9999 meeting` | `Jul 2026 meeting` | no full-match rule for this text | mapped `2026-07` | 1 |
| `bond maturing 9999-99-99` | `bond maturing 2028-11-01` | no full-match rule for this text | `period_is_maturity_date` | 1 |
| `Aug 9999 (advance)` | `Aug 2026 (advance)` | no full-match rule for this text | mapped `2026-08` | 1 |
| `September 99, 9999 decision` | `September 24, 2026 decision` | no full-match rule for this text | mapped `2026-09` | 1 |
| `Sep 99-99, 9999 meeting` | `Sep 15-16, 2026 meeting` | no full-match rule for this text | mapped `2026-09` | 1 |
| `September 99-99, 9999` | `September 15-16, 2026` | no full-match rule for this text | mapped `2026-09` | 1 |
| `Sep 99-99 9999 meeting` | `Sep 16-17 2026 meeting` | no full-match rule for this text | mapped `2026-09` | 1 |
| `Oct 9999 meeting` | `Oct 2026 meeting` | no full-match rule for this text | mapped `2026-10` | 1 |
| `9-9 October 9999` | `6-7 October 2026` | no full-match rule for this text | mapped `2026-10` | 1 |
| `Aug.` | `Aug.` | year absent; v2 treats this as unrecognized | `period_year_missing` | 1 |
| `9-99 Sep 9999 meeting` | `9-10 Sep 2026 meeting` | no full-match rule for this text | mapped `2026-09` | 1 |
| `Meeting of 9-99 Sep 9999` | `Meeting of 9-10 Sep 2026` | no full-match rule for this text | mapped `2026-09` | 1 |
| `9999-99` | `2026-27` | already parses; a companion label vetoes the item; no full-match rule for this text | `period_fiscal_year_ambiguous`; mapped `2026-08`; mapped `2026-09` | 1 |
| `Aug 9999 (P)` | `Aug 2026 (P)` | no full-match rule for this text | mapped `2026-08` | 1 |
| `9 September 9999` | `9 September 2026` | already parses; a companion label vetoes the item | mapped `2026-09` | 1 |
| `9 September 9999 meeting` | `9 September 2026 meeting` | no full-match rule for this text | mapped `2026-09` | 1 |
| `August 9999` | `August 2026` | already parses; a companion label vetoes the item | mapped `2026-08` | 1 |
| `Septermber 9999` | `Septermber 2026` | no full-match rule for this text | `period_format_unrecognised` | 1 |
| `three months to Aug 9999` | `three months to Aug 2026` | no full-match rule for this text | mapped `2026-08` | 1 |
| `QIII 9999` | `QIII 2026` | no full-match rule for this text | mapped `2026-Q3` | 1 |
| `First three quarters of 9999` | `First three quarters of 2026` | no full-match rule for this text | mapped `2026-09` | 1 |
| `Oct 9999 (prelim)` | `Oct 2026 (prelim)` | no full-match rule for this text | mapped `2026-10` | 1 |
| `week ending October 9` | `week ending October 3` | year absent; v2 treats this as unrecognized | `period_year_missing` | 1 |
| `Week ending Oct 99` | `Week ending Oct 07` | year absent; v2 treats this as unrecognized | `period_year_missing` | 1 |
| `FOMC meeting 99-99 Sep 9999` | `FOMC meeting 15-16 Sep 2026` | no full-match rule for this text | mapped `2026-09` | 1 |

## Every original item

All source ids are `lazycommittee_calendar`. Raw text lists below are the **actual fields considered by the resolver**, deduplicated, excluding nulls; stored placeholder labels are described above.

| Item | Indicator suffix | Raw text | V3 status / reason | Canonical period |
| --- | --- | --- | --- | --- |
| `103` | `cn_industrial_profits` | `Jan-Aug 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `110` | `jp_boj_minutes` | `Jul 30-31, 2026 meeting`; `July 30–31, 2026 meeting` | `complete/catalogue_period_mapped` | 2026-07 |
| `111` | `jp_boj_summary_opinions` | `Sep 2026 meeting`; `September 17–18, 2026 meeting` | `complete/catalogue_period_mapped` | 2026-09 |
| `116` | `jp_tankan` | `Sep 2026 Survey` | `complete/catalogue_period_mapped` | 2026-Q3 |
| `117` | `jp_tankan_nonmfg` | `Sep 2026 Survey` | `complete/catalogue_period_mapped` | 2026-Q3 |
| `128` | `cn_industrial_profits` | `Jan-Aug 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `13` | `br_unemp` | `8/2026`; `Jun-Aug 2026`; `quarter ended Aug 2026`; `quarter ending Aug 2026`; `trimestre móvel Jun-Aug 2026` | `pending/period_is_rolling_window` | — |
| `14` | `br_unemp` | `quarter ending Aug 2026` | `pending/period_is_rolling_window` | — |
| `140` | `jp_boj_minutes` | `Jul 2026 meeting` | `complete/catalogue_period_mapped` | 2026-07 |
| `141` | `jp_boj_summary_opinions` | `Sep 2026 meeting` | `complete/catalogue_period_mapped` | 2026-09 |
| `145` | `jp_tankan` | `Q3 2026 (Sep survey)`; `Sep 2026 survey` | `complete/catalogue_period_mapped` | 2026-Q3 |
| `146` | `jp_tankan_nonmfg` | `Q3 2026 (Sep survey)`; `Sep 2026 survey` | `complete/catalogue_period_mapped` | 2026-Q3 |
| `16` | `ca_bond_auction_2y` | `bond maturing 2028-11-01` | `terminal/period_is_maturity_date` | — |
| `176` | `uk_gdp_qq_final` | `Apr-Jun 2026 (Q2 2026)` | `complete/catalogue_period_mapped` | 2026-Q2 |
| `177` | `uk_gdp_yy_final` | `Apr-Jun 2026 (Q2 2026)` | `complete/catalogue_period_mapped` | 2026-Q2 |
| `205` | `cn_industrial_profits` | `Jan-Aug 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `214` | `ca_gdp_mm_advance` | `Aug 2026 (advance)` | `complete/catalogue_period_mapped` | 2026-08 |
| `216` | `in_rbi` | `Oct 2026 MPC`; `Oct 2026 MPC Meeting`; `Oct 2026 MPC meeting`; `Oct 2026 MPC meeting (Oct 5-7)`; `October 5–7, 2026` | `complete/catalogue_period_mapped` | 2026-10 |
| `24` | `mx_banxico` | `September 24, 2026 decision` | `complete/catalogue_period_mapped` | 2026-09 |
| `240` | `us_fomc_minutes` | `Sep 15-16, 2026 FOMC meeting`; `Sep 15-16, 2026 meeting`; `Sep 2026 FOMC meeting`; `Sep 2026 meeting`; `September 15-16, 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `241` | `us_fomc_minutes` | `Sep 16-17 2026 meeting` | `complete/catalogue_period_mapped` | 2026-09 |
| `25` | `mx_core_cpi_biweekly` | `Primera quincena de Septiembre de 2026` | `complete/catalogue_period_mapped` | 2026-09-15 |
| `251` | `pl_nbp` | `6-7 October 2026`; `Oct 2026 MPC meeting`; `Oct 2026 meeting` | `complete/catalogue_period_mapped` | 2026-10 |
| `253` | `cn_exports` | `2026-09`; `Sep 2026`; `September`; `September 2026` | `complete/late_value_mapped_unverified` | 2026-09 |
| `255` | `cn_imports` | `2026-09`; `Sep 2026`; `September`; `September 2026` | `complete/late_value_mapped_unverified` | 2026-09 |
| `257` | `cn_trade` | `2026-09`; `Sep 2026`; `September`; `September 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `258` | `in_cpi_yy` | `2026-09`; `Sep 2026`; `September 2026`; `September, 2026` | `complete/late_value_mapped_unverified` | 2026-09 |
| `26` | `mx_cpi_biweekly` | `Primera quincena de Septiembre de 2026` | `complete/catalogue_period_mapped` | 2026-09-15 |
| `27` | `mx_cpi_biweekly_yy` | `Primera quincena de Septiembre de 2026` | `complete/catalogue_period_mapped` | 2026-09-15 |
| `292` | `mx_exports` | `Agosto`; `Agosto 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `296` | `mx_imports` | `Agosto`; `Agosto 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `298` | `mx_indprod_mm` | `Agosto`; `Agosto 2026`; `Agosto de 2026`; `agosto de 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `299` | `mx_international_reserves_weekly` | `semana al 9 de octubre de 2026`; `week ending 2026-10-09` | `complete/catalogue_period_mapped` | 2026-10-09 |
| `300` | `mx_monetary_base_weekly` | `al 9 de octubre de 2026`; `week ending 2026-10-09` | `complete/catalogue_period_mapped` | 2026-10-09 |
| `319` | `ch_ppi_domestic_mm` | `2026-09`; `September`; `September 2026` | `pending/overdue` | 2026-09 |
| `320` | `ch_ppi_domestic_yy` | `2026-09`; `September`; `September 2026` | `pending/overdue` | 2026-09 |
| `321` | `ch_ppi_import_mm` | `2026-09`; `Sep 2026`; `September`; `September 2026` | `complete/late_value_mapped_unverified` | 2026-09 |
| `322` | `ch_ppi_import_yy` | `2026-09`; `Sep 2026`; `September`; `September 2026` | `complete/late_value_mapped_unverified` | 2026-09 |
| `333` | `de_fx_reserves` | `Sept.` | `pending/period_year_missing` | — |
| `339` | `de_indprod_yy` | `Aug.` | `pending/period_year_missing` | — |
| `340` | `de_reserve_assets_gross` | `Sept.` | `pending/period_year_missing` | — |
| `348` | `ez_ecb_accounts` | `9-10 Sep 2026 meeting`; `9-10 September 2026`; `Meeting of 9-10 Sep 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `388` | `in_bank_rate` | `October 5–7, 2026` | `complete/catalogue_period_mapped` | 2026-10 |
| `390` | `in_fx_reserves` | `As on October 2, 2026`; `October 2, 2026`; `week ending 2026-10-02` | `complete/catalogue_period_mapped` | 2026-10-02 |
| `391` | `in_gdp_growth_projection` | `2026-27` | `pending/period_fiscal_year_ambiguous` | — |
| `393` | `in_msf_rate` | `October 5–7, 2026` | `complete/catalogue_period_mapped` | 2026-10 |
| `394` | `in_rbi_stance` | `October 5–7, 2026` | `complete/catalogue_period_mapped` | 2026-10 |
| `395` | `in_sdf_rate` | `October 5–7, 2026` | `complete/catalogue_period_mapped` | 2026-10 |
| `411` | `jp_jgb_5y_auction` | `Issue Number 188` | `terminal/period_is_document_identifier` | — |
| `45` | `us_durable` | `Aug 2026 (P)` | `complete/catalogue_period_mapped` | 2026-08 |
| `463` | `mx_mfg_employment_mm` | `Agosto`; `Agosto de 2026`; `agosto de 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `464` | `mx_mfg_real_remuneration_mm` | `Agosto`; `Agosto de 2026`; `agosto de 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `465` | `mx_private_consumption_mm` | `Septiembre`; `Septiembre de 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `466` | `mx_private_consumption_yy` | `Septiembre`; `Septiembre de 2026`; `septiembre de 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `467` | `mx_trade_revised` | `Agosto` | `pending/period_year_missing` | — |
| `468` | `us_bank_loans_leases` | `Week ending Sep 30` | `pending/period_year_missing` | — |
| `510` | `pl_nbp_minutes` | `9 September 2026`; `9 September 2026 meeting` | `complete/catalogue_period_mapped` | 2026-09 |
| `516` | `in_fx_reserves` | `As on October 9, 2026`; `Oct 09, 2026`; `October 9, 2026`; `week ending 2026-10-09` | `complete/catalogue_period_mapped` | 2026-10-09 |
| `518` | `jp_bank_lending_yy` | `Sept.`; `Sept. 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `519` | `jp_boj_opinion_survey_di` | `September 2026 Survey` | `complete/catalogue_period_mapped` | 2026-Q3 |
| `520` | `jp_cgpi_mm_prelim` | `Sept.`; `Sept. 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `521` | `jp_cgpi_yy_prelim` | `Sept.`; `Sept. 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `525` | `jp_tbill_3m_auction` | `Issue 1413` | `terminal/period_is_document_identifier` | — |
| `548` | `mx_emim_production_yy` | `Agosto`; `Agosto de 2026`; `agosto de 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `549` | `mx_mfg_employment_yy` | `Agosto`; `Agosto de 2026`; `agosto de 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `550` | `mx_mfg_real_remuneration_yy` | `Agosto`; `Agosto de 2026`; `agosto de 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `585` | `uk_gdp_3m_avg` | `3 months to Aug 2026`; `Jun-Aug 2026`; `three months to August 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `586` | `in_unemployment_rate_cws` | `2026-09`; `Sep 2026`; `September 2026`; `September, 2026` | `complete/late_value_mapped_unverified` | 2026-09 |
| `587` | `jp_boj_opinion_survey_outlook_di` | `September 2026 Survey` | `complete/catalogue_period_mapped` | 2026-Q3 |
| `609` | `jp_m2_yy_prelim` | `Sept.` | `pending/period_year_missing` | — |
| `610` | `jp_m3_yy_prelim` | `Sept.` | `pending/period_year_missing` | — |
| `617` | `br_ibcbr_yy` | `2026-08`; `August`; `August 2026` | `complete/late_value_mapped_unverified` | 2026-08 |
| `618` | `ca_boc_bos_indicator` | `Third Quarter of 2026`; `third quarter` | `complete/catalogue_period_mapped` | 2026-Q3 |
| `619` | `ca_boc_csce` | `Third Quarter of 2026`; `third quarter` | `complete/catalogue_period_mapped` | 2026-Q3 |
| `638` | `it_intl_reserves_fx_liquidity` | `Septermber 2026` | `pending/period_format_unrecognised` | — |
| `639` | `uk_construction_output_3m3m` | `three months to Aug 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `646` | `cn_housing_starts_ytd_yy` | `January to September 2026`; `January–September 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `649` | `cn_indprod_ytd_yy` | `January to September 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `650` | `cn_property_sales_ytd_yy` | `January to September 2026`; `January–September 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `653` | `cn_retail_ytd_yy` | `January to September 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `664` | `jp_tbill_1y_auction` | `Issue 1414` | `terminal/period_is_document_identifier` | — |
| `690` | `it_inflation_growth_expectations_survey` | `QIII 2026` | `complete/catalogue_period_mapped` | 2026-Q3 |
| `705` | `in_cfpi_yy` | `2026-09`; `Sep 2026`; `September, 2026` | `complete/late_value_mapped_unverified` | 2026-09 |
| `738` | `cn_tsf_cumulative_flow` | `First three quarters of 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `760` | `mx_capacity_utilization_mfg` | `Agosto` | `pending/period_year_missing` | — |
| `775` | `us_umich_prelim` | `Oct 2026 (prelim)` | `complete/catalogue_period_mapped` | 2026-10 |
| `784` | `pl_goods_exports_cumulative` | `January-August 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `785` | `pl_goods_imports_cumulative` | `January-August 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `786` | `pl_goods_trade_deficit_cumulative` | `January-August 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `823` | `br_ibcbr_mm` | `2026-08`; `August` | `complete/late_value_mapped_unverified` | 2026-08 |
| `834` | `us_claims` | `week ending October 10` | `pending/period_year_missing` | — |
| `836` | `us_continuing_claims` | `week ending October 3` | `pending/period_year_missing` | — |
| `884` | `uk_industrial_production_3m3m` | `3 months to Aug 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `889` | `uk_trade_balance_ex_precious_metals_3m` | `3 months to Aug 2026` | `complete/catalogue_period_mapped` | 2026-08 |
| `931` | `us_bank_loans_leases` | `Week ending Oct 07` | `pending/period_year_missing` | — |
| `sighting:1558` | `us_fomc_minutes` | `Sep 2026 meeting` | `complete/catalogue_period_mapped` | 2026-09 |
| `sighting:1776` | `us_fomc_minutes` | `FOMC meeting 15-16 Sep 2026` | `complete/catalogue_period_mapped` | 2026-09 |
| `sighting:1855` | `in_rbi` | `Oct 2026 MPC meeting` | `complete/catalogue_period_mapped` | 2026-10 |
| `sighting:2371` | `mx_indprod_sa_yy` | `Agosto` | `pending/period_year_missing` | — |
| `sighting:778` | `br_unemp` | `quarter ending Aug 2026` | `pending/period_is_rolling_window` | — |

## Still pending as period_format_unrecognised

Only item 638 belongs to the original 100. The other six became due after the inventory run and introduce formats outside the authorized inventory. They remain unchanged by format extensions; explicit new support would require a separate brief. Their raw text is retained rather than corrected or guessed.

| Item | Indicator suffix | Raw text |
| --- | --- | --- |
| `638` | `it_intl_reserves_fx_liquidity` | `Septermber 2026` |
| `668` | `ar_business_confidence_mfg` | `Expectativas octubre-diciembre de 2026` |
| `669` | `ar_business_confidence_retail` | `Expectativas octubre-diciembre de 2026` |
| `670` | `ar_monetary_base_weekly` | `15/10/2026` |
| `746` | `ar_bcra_balance_sheet` | `15/10/2026` |
| `874` | `pl_building_permits` | `01-09 2026` |
| `880` | `pl_housing_starts` | `01-09 2026` |

## Verification and limits

- Before/after CLI JSON, fixed cutoff, and the unchanged SQLite backup are retained locally under `.tmp/period-*` and `.tmp/macro_copy.sqlite` (ignored by git). A repeated final preview returned identical counts and preserved the copy's SHA-256: `5a3a3fed4cd95d7bd720ddc30fc19dd206b3ed217b6716e7a6c17d92bd549add`.
- Tests include real example literals for all 83 supplied shapes and cover every new shape family and reason, placeholder/missing exclusion, compatible and incompatible partial labels, wrong frequencies, invalid/contradictory ranges and annotations, cumulative/rolling vetoes, and read-overlay/audit/revert behavior.
- `ruff check .`: passed. `ruff format --check .`: 193 files would reformat; a separate extraction of the base revision confirms pre-existing formatting debt in 194 tracked Python files. This task does not reformat the repository. The changed parser and new tests are formatted.
- Full suite: `C:/ProgramData/spyder-6/python.exe -m pytest -q --basetemp=.tmp/pytest-periods` — **1,657 passed, one skipped**, two warnings, in 735.06 seconds. Final focused worklist run after the last guards and overlay/revert test: **174 passed** in 15.11 seconds. The pytest temp directory was removed after verification.
- Commit/push are recorded in the task response. No production rerun or contract closure is claimed.
