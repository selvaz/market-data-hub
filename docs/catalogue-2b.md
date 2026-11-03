# Catalogue 2b: publication cadence and energy gaps

H.10 bilateral DEX observations and Kim-Wright THREEFYTP10 are daily observations published weekly. Their catalogue `publication_cadence` is `W`, separate from native daily `frequency`; coverage uses publication cadence for its default freshness threshold, with the existing 10-calendar-day explicit limits retained. A coverage rebuild is required to update stored verdicts. Consumers should use publication cadence, not observation frequency, for freshness.

The NY Fed ACM 10-year daily term premium is `NYFED_ACM_TP10_DAILY` (percent, `ACMTP10` in the official [ACM Daily workbook](https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls)). It uses the existing ACM runner, vintage, and log conventions; the monthly chart series remains distinct. The workbook was reachable on 2026-11-03; its last observation was 2026-10-30. Model values may be revised.

## Inventory gap

No new FRED series were added. Live FRED series pages returned HTTP 404 on 2026-11-03 for `WCESTUS1` (commercial crude excluding SPR), `WGTSTUS1` (total gasoline), and `WDISTUS1` (distillate). These are EIA identifiers, not verified FRED identifiers; FRED title, frequency and last observation are therefore unavailable. Searches of FRED did not establish a current weekly natural-gas storage series either. EIA publishes these data directly, including [weekly natural gas storage](https://www.eia.gov/dnav/ng/ng_stor_wkly_s1_w.htm), but adding an EIA collector is outside this job. Do not treat these EIA codes as FRED codes.

## Futures curves gap

The existing Yahoo catalogue has continuous front contracts including `CL=F`, `BZ=F` and `NG=F`, but no dated-contract universe, second-month series, expiry/roll convention or curve collector. Yahoo lists [front contracts](https://finance.yahoo.com/markets/commodities/); a reproducible rolling second month and its history are not established in the hub. Thus the front price is available, but a futures curve and calendar spread remain a declared gap. Dated Yahoo contracts could be evaluated in a separate job with expiry and roll rules.
