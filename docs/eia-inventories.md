# EIA weekly inventories

The EIA API is free with a registered key, subject to the [API terms of service](https://www.eia.gov/opendata/register.php). Set `EIA_API_KEY` in the user's environment; never put it in a command line or log. The API requires it in the request URL. The collector uses a non-logging HTTP client and never emits URLs or transport exceptions.

The configured API v2 candidates are `WCESTUS1`, `WGTSTUS1`, and `WDISTUS1` in `petroleum/stoc/wstk`, and `NW2_EPG0_SWO_R48_BCF` in `natural-gas/stor/wkly`. **The lead must verify each ID, title, weekly frequency, unit and latest period live before deployment.** Petroleum values are in thousand barrels and gas values in Bcf. Weekly reference dates identify the week ending; petroleum is normally published Wednesday and gas Thursday, with holiday delays. The [EIA FAQ](https://www.eia.gov/opendata/faqs.php) says these releases reach the API within two hours.

Run `python run_eia_inventories.py --db <absolute-path>`; an initial run requests history from 1982, and later runs refresh 95 days for revisions. `--start` and `--end` override the window. Each successful series is stored in `macro_series` with hub ingestion-date vintages, not historical publication-time vintages. The runner uses the shared database lock and download log; a lock timeout exits 9, a collection failure exits 1.

`setup_eia_scheduler.ps1 -Database <absolute-path> -Python <python-path>` registers a **disabled** task at 14:00 local time daily; the machine must be set to Pacific time. Daily retries recover holiday-delayed releases and missed jobs; 14:00 PT is after normal releases and the API's two-hour processing window. Deploy and enable only after live verification. The script refuses to replace an existing task.
