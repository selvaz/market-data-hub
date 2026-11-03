# Bond ETF carry source mapping (research in progress)

This is a working mapping, not yet an approved collector configuration. The
portfolio includes more bond funds than the initial list: the saved ACWI/AGG
trees include IBCL.DE and DBXP.DE; the global multi-asset tree also includes
IEI, MUB, BWX, IHY, CEMB and BNDX. Symbols refer to the US listing unless a
suffix is shown. BlackRock and iShares sites are unreachable from the server
(HTTP 403); no attempt is made to bypass this restriction. For AGG, State
Street SPAB tracks the same Bloomberg U.S. Aggregate Bond Index, as verified
against [AGG's regulatory disclosure](https://www.sec.gov/Archives/edgar/data/70858/000191870425013562/form424b2.htm)
and [SPAB's issuer page](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-portfolio-aggregate-bond-index-etf-spab).
No other cross-fund proxies have been established.

For TLT, LQD, TIP, EUHY, IEAC.AS, IEF, SHY, EMB, HYG, IEI, MUB, CEMB and
IBCL.DE, the carry gap is **issuer unreachable, no same-index proxy**. AGG is
the only BlackRock bond fund in this mapping with a verified reachable proxy.

State Street BIL, BWX and SPAB are collected from the issuer's daily
`spdr-product-data-us-en.xlsx` workbook. Its Option Adjusted Duration is
described by State Street as effective duration. Workbook percentage values
are converted to decimal fractions; source dates come from its `As of**` field.
BWX holds bonds in multiple currencies, so its single `index_currency` is null.
AGG is collected as `proxy_for=AGG` from SPAB, with `proxy_symbol=SPAB` and
the shared Bloomberg U.S. Aggregate Bond Index. Fund characteristics, SEC yield,
coupon and holdings count describe SPAB rather than AGG itself.

VanEck's daily fund-page JSON blocks provide 30-day SEC yield, yield to
worst, effective duration and total holdings for EMLC, HYEM and IHY. Each
block supplies its own as-of date. Yield to worst remains distinct from yield
to maturity. The monthly fact sheets no longer write these daily fields.

Vanguard BNDX's quarterly fact sheet publishes average duration and number of
bonds. Those are kept under their published meanings; the factsheet does not
provide YTM, SEC/distribution yield or average coupon. BNDX hedges multiple
underlying currencies into USD, so its index currency remains null.

DWS DBXP.DE: the downloaded September 2026 issuer factsheet does not contain
the requested carry fields. The product page renders portfolio characteristics
through a client-side application; a stable machine-readable data endpoint has
not yet been established. All DBXP.DE carry fields remain gaps pending that
endpoint. Yield to worst is not treated as yield to maturity.

| ETF | Issuer | Underlying index | Index currency | Share class currency | Hedged | Candidate source / gap |
|---|---|---|---|---|---|---|
| TLT | BlackRock | ICE U.S. Treasury 20+ Year Bond Index | USD | USD | No | iShares product page |
| LQD | BlackRock | iBoxx USD Liquid Investment Grade Index | USD | USD | No | iShares product page |
| TIP | BlackRock | Verification pending | USD | USD | No | iShares product page; exact index pending |
| EMLC | VanEck | J.P. Morgan GBI-EM Global Core Index | Multiple local currencies | USD | No | VanEck daily fund-page blocks |
| HYEM | VanEck | ICE BofA Diversified High Yield US Emerging Markets Corporate Plus Index | USD | USD | No | VanEck daily fund-page blocks |
| EUHY | BlackRock | Bloomberg Pan-European High Yield (Euro) 100% Hedged to USD Index | EUR underlying; USD hedged index | USD | Yes, USD | iShares product page; identify whether YTM is hedge-adjusted |
| IEAC.AS | BlackRock | BBG Euro Corporate Index (EUR) | EUR | EUR | No | iShares product page |
| BIL | State Street | Bloomberg 1-3 Month U.S. Treasury Bill Index | USD | USD | No | State Street product page; machine-readable endpoint pending |
| AGG | BlackRock | Bloomberg U.S. Aggregate Bond Index | USD | USD | No | iShares product page |
| IEF | BlackRock | ICE U.S. Treasury 7-10 Year Bond Index | USD | USD | No | iShares product page |
| SHY | BlackRock | ICE U.S. Treasury 1-3 Year Bond Index | USD | USD | No | iShares product page |
| EMB | BlackRock | Verification pending | USD | USD | No | iShares product page; exact index pending |
| HYG | BlackRock | iBoxx USD Liquid High Yield Index | USD | USD | No | iShares product page |
| IEI | BlackRock | ICE U.S. Treasury 3-7 Year Bond Index | USD | USD | No | iShares product page |
| MUB | BlackRock | Verification pending | USD | USD | No | iShares product page; exact index pending |
| BWX | State Street | Bloomberg Global Treasury ex-US Capped Index | Multiple local currencies | USD | No | State Street product page; endpoint pending |
| IHY | VanEck | ICE BofA Global ex-US Issuers High Yield Constrained Index | Multiple local currencies | USD | No | VanEck daily fund-page blocks |
| CEMB | BlackRock | J.P. Morgan CEMBI Broad Diversified Core Index | USD | USD | No | iShares product page |
| BNDX | Vanguard | Bloomberg Global Aggregate ex-USD Float Adjusted RIC Capped Index (Hedged) | Multiple local currencies | USD | Yes, USD | Vanguard fact sheet; machine-readable endpoint pending |
| IBCL.DE | BlackRock | Verification pending | EUR | EUR | No | iShares product page; exact index pending |
| DBXP.DE | DWS | Markit iBoxx EUR Eurozone (DE ES FR IT NL) 1-3 Index | EUR | EUR | No | DWS fact sheet; machine-readable endpoint pending |

An exact name **and version** match is required before using a proxy. A similar
index is not sufficient. Fund-level yields, coupons and holdings counts from a
proxy describe that fund, not the target fund. The source as-of date must be
attached to each field, since a product page can publish different dates.

For a single-currency index hedged into currency H, the planned approximation is
`hedged carry = local-currency YTM + short rate(H) - short rate(index currency)`.
This is a later calculation, not a stored issuer value. Multi-currency indexes
need currency weights and currency-specific short rates; a single index currency
and a single rate differential cannot faithfully represent them.

Sources checked: [TLT](https://www.ishares.com/uk/professionals/en/products/239454/),
[LQD](https://www.ishares.com/uk/professionals/en/products/239566/lqd),
[EMLC](https://www.vaneck.com/us/en/investments/jp-morgan-em-local-currency-bond-etf-emlc/),
[HYEM](https://www.vaneck.com/us/en/investments/emerging-markets-high-yield-bond-etf-hyem/),
[IHY](https://www.vaneck.com/us/en/investments/international-high-yield-bond-etf-ihy/overview/),
[CEMB](https://www.ishares.com/us/products/239525/CEMB),
[EUHY](https://www.ishares.com/ch/professionals/en/products/239550/ishares-euro-high-yield-corporate-bond-usd-hedged-etf),
[IEAC](https://www.blackrock.com/uk/individual/products/251726/ishares-euro-corporate-bond-ucits-etf),
[BIL](https://www.ssga.com/us/en/individual/etfs/state-street-spdr-bloomberg-1-3-month-t-bill-etf-bil),
[BWX](https://www.ssga.com/us/en/individual/etfs/state-street-spdr-bloomberg-international-treasury-bond-etf-bwx),
[BNDX](https://workplace.vanguard.com/assets/corp/fund_communications/pdf_publish/us-products/fact-sheet/F3711.pdf),
and [DBXP](https://etf.dws.com/download/asset/a3c073ff-e495-4163-b745-7eeb24badf62).
