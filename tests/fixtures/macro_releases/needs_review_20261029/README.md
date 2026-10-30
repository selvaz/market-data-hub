# Saved evidence for the 29 October 2026 review failures

Extracted offline from a read-only copy of the production macro release database.
Review run: `0016034008d3453caabbfb55c94f0976`. No network fetches were needed.
Each `.meta.json` records the saved URL, retrieval time, original document/raw
hashes, trimmed fixture SHA-256 and retained PDF page numbers. PDF pages keep
their issuer content streams; HTML fixtures retain only relevant rows/links.

| Fixture | Evidence |
| --- | --- |
| `fhfa-listing.html` | Today's saved listing links 27 October to the October report. |
| `fhfa-october.pdf` | Original pages 1, 3 and 5: cover, press release and monthly table. The press date extracts as `October 27, 202 6`; the cover prints `Released on October 27, 2026`, data through August. Purchase-only, seasonally adjusted U.S. changes are 0.3% m/m and 2.4% y/y. |
| `ec-latest.html` | Today's saved listing links the October final release and its statistical annex (and the separate flash release). |
| `ec-october.pdf` | Original page 1, dated 29 October: ESI falls to 96.9 explicitly in both the EU and euro area. EEI is a separate measure. |
| `ec-october-annex.pdf` | Original pages 1–2: independently dated cover and Table 1, with EA consumer confidence of -17.9 in October and revised -16.8 in September. |
| `jolts-request-failed.json` | Exact saved BLS `REQUEST_FAILED` response, `Results: null`; no issuer values. |

FHFA's review queue references the listing hash
`aeee51a2dd71ded9301ae7b3a9d9b222d4b22a4cdef08beb5b20308376424c55`.
The linked October PDF was already saved on 27 October under
`25588e480ddc194f07fa4b5fce82b585219ccde11c096a31629f4ef79a883c6f`.
Its complete saved body was reparsed alongside the listing; the trimmed fixture
exercises the same failing date extraction and printed table.

Reparsing the complete saved EC release
`bbfbce7df1e41438d209a0b82c2bcb43e44546f4c6203eec9b90e82a64ca3e9f`
and annex
`b4225536debc3ba3fcfd0678ec9d7037048a644ddd2829371c84f8c328535140`
produces ESI 96.9, October CCI -17.9 and revised September CCI -16.8,
all as the 29 October final edition.

Reparsing the complete saved JOLTS rejection
`7375ccc57b776ffb5062da8f76d3bb4e174c723775a6c826c19f613cf401c7b5`
raises a typed availability failure and enables the existing ALFRED path.
The fallback responses in the regression tests are **synthetic**, so this does
not certify any live ALFRED value or repair a production row. Tests verify
redistributor/BLS provenance, the 10:00 New York release instant and rejection
of a mismatched first vintage. No value is inferred from the BLS failure.

The older FHFA (`m9f/fhfa-aug.pdf`, `m9f/fhfa-sept.pdf`) and EC
(`m9b/ec-final.pdf`, `m9b/ec-annex.pdf`, `m9b/ec-flash.pdf`) fixtures remain
unchanged and are covered by the M9 regression suites.
