# Operations catalog integration

The scheduled market-data tasks keep using `market_data.duckdb`. They also
publish a central run record and report artifacts through `lazytoolkit`:

* `run_daily.py` registers download runs and HTML/Markdown/dashboard reports.
* `run_regime_daily.py` registers the regime report, summary results and a
  portable snapshot of the fitted HMM parameters.
* `send_telegram_run_report.py` registers the generated Telegram report.

Install and initialize the shared catalog once from the LazyTools checkout:

```powershell
powershell -ExecutionPolicy Bypass -File ..\LazyTools\setup_operations.ps1
```

The Task Scheduler wrappers import `LAZYTOOLS_OPERATIONS_DB` and
`LAZYTOOLS_ARTIFACTS_DIR` from the persisted user environment. If LazyTools is
not installed, the data task continues normally and logs that central
registration was skipped.
