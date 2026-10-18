# M6 shadow parity: 10–17 October 2026

The offline test `test_shadow_legacy_week_parity_report` builds a temporary
calendar from `tests/fixtures/legacy_calendar_v4.sql`, reuses the importer test's
HICP evidence, and adds a synthetic CPI observation. It imports that copy into a
temporary macro store. No live calendar or production database is read.

| Release | Card actual | Legacy forecast evidence | Stage | Authority | Surprise |
| --- | ---: | ---: | --- | --- | --- |
| CPI US y/y, September | 3.7% | 3.6% | unknown (legacy has no certified stage) | provisional | null |
| EA HICP y/y final, September | 3.8% | 3.6% (synthetic importer fixture) | final | provisional | null |

The source fixture places both releases on 17 October. These are preserved
legacy calendar dates, not certified issuer publication times. The separate
issuer-adapter test places HICP final on **16 October**, based on the saved
issuer publication; the original 32-case golden file remains unchanged.

The accepted owner decision retains the CPI forecast in `legacy_consensus_evidence`, including its
original merged row. It cannot enter provider-specific `consensus` or generate
surprise: the legacy fixture has neither a certified provider nor a pre-release
capture. A legacy observation captured after the release is never pre-release
consensus, and its surprise stays null. This behavior is decided, not an open
deviation requiring further approval. Both cards retain a null issuer `published_at`. The test checks that
the source file is byte-for-byte unchanged and that the imported evidence is
invisible before our import knowledge time. It writes a detailed JSON report
inside its repository-local pytest temporary directory.

This is a fixture parity report. It does not certify the contents or completeness
of the live legacy calendar and does not migrate the ECO recap.
