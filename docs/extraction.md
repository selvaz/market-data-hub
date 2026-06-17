# Extraction & Discovery API

For programmatic and LLM consumption the hub exposes three layers on top of the
read-only DuckDB. The reader returns raw stored shapes; the **catalog** answers
*what* is available; **extract** returns analysis-ready `(DataFrame, meta)`;
**agent_tools** wraps both as JSON tools for LLMs.

```
reader.py        raw read API (read_prices, read_macro, ...)
catalog.py       discovery / "map" of the DB (by asset class, area, sector, pillar, country)
extract.py       analysis-ready time series (log-returns, resampling, NaN handling)
agent_tools.py   JSON tool functions + optional LazyBridge ToolProvider (DataHubTools)
```

> A full how-to for agents lives in `skills/query-market-data-hub/SKILL.md`.

## Discovery (`market_data_hub.catalog`)

```python
from market_data_hub import catalog

catalog.list_datasets()                                  # the 5 domains
catalog.list_symbols(asset_class="EQUITY", area="Emerging Markets")
catalog.list_symbols(asset_class="EQUITY", area="USA", sector="Energy")   # -> XLE
catalog.list_symbols(asset_class="EQUITY", area="USA", sector="*")        # all US sector ETFs
catalog.list_sectors(area="USA")                         # sector -> symbols
catalog.list_macro_series(category="RATES")
catalog.list_macro_indicators(pillar="growth")
catalog.list_countries(region="G7")
catalog.describe_series("SPY")
catalog.search("emerging markets bonds")                 # free-text, all domains
```

`area` is normalized (`US` == `USA`). Sector ETFs (US SPDR + STOXX Europe 600
sleeves) get a derived GICS `sector`. Every `list_*` enriches rows with coverage
(`first_date`, `last_date`, `obs_count`, `freq_detected`, `lag_days`, `stalled`,
`coverage_score`) when the DB is populated.

## Extraction (`market_data_hub.extract`)

```python
from market_data_hub.extract import extract_series, extract_returns, extract_macro

df, meta = extract_returns(["SPY", "TLT", "^VIX"], start="2010-01-01", frequency="W")
px, _   = extract_series(["XLE", "XLF"], domain="prices", field="adj_close")
m, _    = extract_macro(["DGS10", "T10Y2Y"], transform="diff")
```

`extract_series(symbols, *, domain, field, transform, frequency, fillna, align)`:
- `domain`: `prices` | `macro` | `crypto` | `factors`
- `transform`: `level` | `log_return` | `pct_change` | `diff`
- `frequency`: `None` (native) | `D` | `W` (Friday) | `M` | `Q`
- daily `adj_close` log-returns are served from the `v_returns` view.

Returns `(df, meta)` — `df` is `DatetimeIndex × symbols`, ready for
`MSRegimeEngine.fit(df, model="panel")` in LazyHMM.

## LLM tools (`market_data_hub.agent_tools`)

Plain JSON functions (`tool_list_symbols`, `tool_search`, `tool_get_returns`, …)
work anywhere. With the `agent` extra installed:

```python
from lazybridge import Agent
from market_data_hub.agent_tools import DataHubTools
agent = Agent("claude-opus-4-8", tools=[DataHubTools()])
```

Install: `pip install -e ".[agent]"`.
