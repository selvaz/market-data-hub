# M9i: Latin America and policy-rate coverage

This branch stacks on `feat/m9d1-japan-china` / PR #139. BoE and PBOC are
already implemented by M9c; M9i reuses those collectors and adds no duplicate ids.

| Family | Issuer / source | Source class | Reviewed publication editions | Saved golden cases |
| --- | --- | --- | --- | --- |
| `bcb_policy` | [BCB public Copom statement API](https://www.bcb.gov.br/conteudo/dadosabertos/BCBDeinf/elements_copom.html) | official | Aug 5, Sep 16; scheduled Nov 4, Dec 9 | 2 decisions, with Jun 17 preceding statement |
| `banxico_policy` | [Banxico statement PDFs](https://www.banxico.org.mx/publicaciones-y-prensa/anuncios-de-las-decisiones-de-politica-monetaria/anuncios-politica-monetaria-t.html) | official | Aug 6, Sep 24; scheduled Nov 5, Dec 17 | 2 decisions |
| `mx_cpi` | [INEGI monthly INPC bulletins](https://www.inegi.org.mx/app/saladeprensa/noticia/11265) | official | Sep 9 (Aug), Oct 8 (Sep); scheduled Nov 9, Dec 9 | 4 readings in 2 real bulletins |
| `boe_policy` | BoE Monetary Policy Summary and Minutes, inherited from M9c | official | 2026 calendar through Dec 17 | Existing M9c fixtures |
| `pboc_lpr` | PBOC announcement of authorized NIFC 1Y and over-5Y LPR, inherited from M9c | official | 2026 calendar through Dec 21 | Existing M9c fixtures |
| IPCA / IPCA-15 | IBGE SIDRA responds; required publication-calendar pages deny access | blocked, excluded from certified families | Needs review; no publication certified | None; stopped under the brief's access rule |

BCB exposes decision dates without actual publication clocks. Cards retain
date-only precision. The calendar's 18:30 America/Sao_Paulo lower bound gates
collection and starts the six-hour publication-delay grace; it is not an
asserted observation of the publication clock. The [BCB permanent release rule](https://www.bcb.gov.br/detalhenoticia/17291/nota)
confirms that statements are published from 18:30 after the meeting.
The Selic rate is the target stated by Copom, not the daily
realized SGS rate. Changes compare consecutive issuer statements. Banxico
uses its printed target, change and unanimous vote. Its SIE API needs a token;
the public statement PDFs need none. INEGI's indicator API also needs a token;
the public bulletins need none. No credentials or new dependencies are used.

The INEGI parser checks the publication date, monthly reference period, full
Cuadro 1 headings and 11 numeric cells per row. It selects the current annual
rate, compares the headline with the narrative and never calculates rates
from index levels. IPCA-15 is a separate concept from IPCA, not a release stage.
Unknown dates, source URLs, schemas or formats fail closed with `needs review`.

The supplied tier-1 catalogue contains no Latin American measures and no
first-half INPC row. The conditional first-half edition is therefore excluded.
The eight additions are preserved in [macro-catalogue-m9i.toml](macro-catalogue-m9i.toml)
and appended to the supplied external catalogue. The four IBGE rows are
definitions only, explicitly unverified, pending accessible publication evidence.

Approved context aliases map Selic, Banxico and headline INPC to `br_selic`,
`mx_banxico` and `mx_cpi`. There is no approved Mexican core INPC concept card:
`mx_core_cpi_yy` remains an explicit mapping gap. Coverage is 96 mapped of 97
macro indicators; the underlying core INPC release cards still resolve official.

The server returned HTTP 403 for both IBGE calendar pages:

- `https://www.ibge.gov.br/estatisticas/economicas/precos-e-custos/9256-indice-nacional-de-precos-ao-consumidor-amplo.html`
- `https://www.ibge.gov.br/estatisticas/economicas/precos-e-custos/9260-indice-nacional-de-precos-ao-consumidor-amplo-15.html`

No alternate route around those denials was attempted. The SNIEG calendar
subdomain failed TLS verification; INEGI's accessible public release page
provides previous and next monthly release dates through December instead.

Run `scripts/verify_macro_m9i.py --source <existing SQLite> --destination-root D:\Temp\<new directory>`.
It checks the three new and two inherited families by default; `--families`
can select a subset. Production is read only as stable main/WAL bytes;
SQLite opens a private snapshot and backs it up under `D:\Temp`. All migrations,
collection and repeat-run checks affect the backup. Reports check source
fingerprints, existing-vintage fingerprints, integrity, official latest cards
and the October 1–27 window. The report explicitly lists blocked IBGE families
and the approved-card gap, so `ok` does not certify excluded families.

On resume, the committed files passed the focused read-API regression file
(65 tests) and indicator-card regression file (414 tests, including generated
JSON freshness and provenance). CI then located the original regression in
`test_macro_m9d1.py`: the M9i registration edit had omitted the Asia entries
from `DISCOVERY_ADAPTERS`, causing all nine Asia daily collectors to fail with
a missing registry key. Restoring those entries preserves the inherited
Japan/China collection path and removes duplicate registration statements.
The previously failing file passed all 53 tests after this fix, before the
final permitted selection. Each run uses a fresh `D:\Temp` basetemp.

## Review fixes

Banxico discovers missing archive URLs from its own dated statement listing.
The listing date, issuer host/path, unique PDF link and PDF's printed date must
agree. A missing link retains earlier cards and records `not_yet_published`
during the six-hour grace, then `scheduled_unreviewed`. No guessed document
identifiers or alternate hosts are accepted. The saved real listing has its
issuer URL, retrieval time and SHA-256 alongside the original bytes.

All three LatAm collectors isolate edition-specific schema/parse failures and
HTTP 404s. A 404 inside grace is pending; outside grace it records `needs_review`
for that edition. Missing Copom preceding statements follow the same policy.
Validated editions and other families still persist, and `scheduled_editions`
in the daily run report exposes each pending/review reason. Transport and
authentication failures retain Asia's issuer-family stopping behavior.

Regression coverage includes the real Banxico boundary (18:59:59Z versus
19:00:00Z), missing and discovered links, a discovered PDF with a different
edition, listing/statement 404s at both grace boundaries, malformed editions
in each family, missing BCB preceding statements, and Copom before 18:30 local.
Daily tests assert the resulting statuses and the surviving official read cards.

The complete offline M9i file passed all 46 tests at `61e022b` (seed 20261028,
fresh `D:\Temp\mdh-m9i-review-f3-20261028-01`). Both broader local selection
commands were rejected before pytest started; neither was executed.
The [fresh private-backup live check](macro-m9i-review-verification.json)
returned `ok` for all three LatAm and both reused policy families: eight new
vintages, zero on repetition, integrity `ok`, original source files and existing
vintages unchanged. Its full report remains at
`D:\Temp\mdh-m9i-review-live-20261028-01\mdh-m9i-mt6x8rcm\verification.json`.
