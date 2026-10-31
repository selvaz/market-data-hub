# 30 October collection evidence

`boj-listing.html`, `boj-october.pdf`, `boj-rss.xml`, `ecb-calendar.html`,
`ec-latest.html` and `fhfa-listing.html` are unchanged response bodies extracted
read-only from the production `documents` / `review_queue` tables for run
`abe2c2b86fe1407e855cb297290bf20d`. Their metadata retains the original retrieval
time and SHA-256. No production records were updated.

The remaining responses were downloaded directly from the official issuers
during this investigation. Their metadata records their actual later capture
times, URLs, parameters and SHA-256; they are corroborating evidence, not
reconstructions of the failed run's responses. The Eurostat `-ap` request's
recorded HTTP 302 was reproduced as a redirect to `/eurostat/c/portal/login`;
the reviewed `-bp` publication returned HTTP 200.

The replay also uses the existing September BoJ and ECB fixtures, earlier
Eurostat releases, and the October EC/FHFA publications under
`needs_review_20261029`. Metadata there records the original production hashes
and retained PDF pages. Those exact originals were reused across failed runs;
review-queue deduplication associates them with their first failed run rather
than creating another queue row on 30 October.

The BoJ statement's rate is 1.25%, unchanged from September, with an 8-1 rate
vote. The separate ETF vote is unanimous and the defeated alternative rate is
1.5%; neither describes the adopted rate decision. Existing timing policy uses
the matching statement RSS item (13:00 JST), while the PDF separately lists
12:02 as the statement release time. This change retains that RSS policy and
does not infer an exact clock from meeting hours or retrieval time.
