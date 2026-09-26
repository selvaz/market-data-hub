# Listing identity maintenance

## Why BCI has two active listings

`ensure_listing()` and the config-universe registration path in
`services.prices._register_listing()` create the deterministic base listing
for `(symbol, provider)`. `services.prices.register_listing()` also supports
real venue-specific listings: when the base ID already exists, it creates a
venue-qualified ID using `(symbol, provider, exchange)`. Both paths register
the ticker alias.

The live BCI rows were inspected with DuckDB opened using `read_only=True` on
2026-09-26. They share instrument `ins_814b29dd89f3`, provider `yahoo`, and
provider symbol `BCI`:

| Listing ID | Exchange | Active | Price rows | Date range |
| --- | --- | --- | ---: | --- |
| `lst_03730b3e2ad5` | NYSE Arca | yes | 2,333 | 2017-03-31 to 2026-07-14 |
| `lst_74f2e91771c7` | NYSE | yes | 0 | — |

The first ID is `stable_id("lst", "BCI", "yahoo")`. The second is exactly
`stable_id("lst", "BCI", "yahoo", "NYSE")`, which identifies the
venue-qualified path in `register_listing()`. The duplicate's alias points to
that second listing as well.

`resolve_empty_duplicate_listing()` is the hub's explicit listing-management
API. It requires the keeper and retiree IDs. It refuses to act unless both
rows share the symbol, instrument, provider, and provider symbol; the IDs are
the only active listings for that symbol; and the row being retired has no
price bars. It moves active aliases to the keeper, coalesces duplicate alias
rows, and sets `active_to` on the retired row. It leaves price history and the
retired row in place. Repeating an applied resolution is safe.

## Production procedure for BCI

Run this from the repository checkout while the scheduled daily and live
writers are stopped. Wait for any in-flight writer to finish before copying
the database. The command defaults to a read-only dry run. Review its IDs,
exchange names, and price counts before applying.

```powershell
$db = 'C:\ProgramData\InvestmentCommittee\live\db\market_data.duckdb'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "$db.pre-bci-listing-$stamp.bak"
Copy-Item -LiteralPath $db -Destination $backup
Get-FileHash -LiteralPath $backup -Algorithm SHA256

& 'C:\ProgramData\spyder-6\python' scripts/resolve_duplicate_listings.py `
  --db $db --symbol BCI

& 'C:\ProgramData\spyder-6\python' scripts/resolve_duplicate_listings.py `
  --db $db --symbol BCI `
  --keep-listing-id lst_03730b3e2ad5 `
  --deactivate-listing-id lst_74f2e91771c7 --apply

# Read-only verification: should report no active duplicate listings.
& 'C:\ProgramData\spyder-6\python' scripts/resolve_duplicate_listings.py `
  --db $db --symbol BCI
```

The backup must be retained until the next daily run has completed and been
reviewed. The apply command is expected to keep the NYSE Arca row active,
deactivate the empty NYSE row, point the active BCI ticker alias at the Arca
row, and leave all 2,333 price bars unchanged. Do not use hand-written SQL for
this maintenance operation.

## Yahoo behavior when another ambiguity is found

Yahoo results are upserted one symbol at a time after each group fetch. An
`AmbiguousSymbolError` now writes a `download_log` row with status `error`,
logs that symbol as skipped, and continues the group. The other symbols still
write normally. This follows the runner's existing per-symbol failure
convention: isolated symbol errors are reported in the run log and do not
change the process exit code; an uncaught run-level failure still makes
`run_daily.py` exit with code 1.

BCI is back in `config/tickers.yaml`; the daily Yahoo universe count is 149.
`tests/test_config.py` asserts both the count and that BCI appears exactly
once, and `tests/test_bci_listing_resolution.py` covers the explicit merge,
read-only dry run, non-empty refusal, and Yahoo continuation behavior.
