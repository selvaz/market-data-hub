# ============================================================================
# reinstall_yfinance.ps1 — uninstalls, cleans the cache and reinstalls yfinance
#
# WHY: the installed version (1.2.1) is not an official yfinance release
# (the real latest is 0.2.x) and the SQLite cookie/tz cache stays locked by the
# Spyder kernel -> deterministic error "unexpected character: line 1 column 1".
#
# BEFORE RUNNING: close Spyder / any open Python kernel, otherwise the
# cookies.db / tkr-tz.db files stay locked and cannot be deleted.
#
# Usage (from PowerShell):
#     powershell -ExecutionPolicy Bypass -File D:\market_data\reinstall_yfinance.ps1
# ============================================================================
$ErrorActionPreference = "Continue"

$py = "C:\Users\Marco\AppData\Local\Programs\Python\Python311\python.exe"
$cacheDir = "$env:LOCALAPPDATA\py-yfinance"

Write-Host "=== 1) Current version ===" -ForegroundColor Cyan
& $py -m pip show yfinance 2>$null | Select-String "Version"

Write-Host "`n=== 2) Closing Python processes that lock the cache ===" -ForegroundColor Cyan
# Show any running python (Spyder kernel, etc.)
$procs = Get-Process python, pythonw -ErrorAction SilentlyContinue
if ($procs) {
    Write-Host "WARNING: active Python processes found (they may lock the cache):"
    $procs | Select-Object Id, ProcessName, StartTime | Format-Table -AutoSize
    Write-Host "If the cache cleanup fails, close them and re-run." -ForegroundColor Yellow
} else {
    Write-Host "No active Python process - OK."
}

Write-Host "`n=== 3) Uninstalling yfinance (bogus 1.2.1) ===" -ForegroundColor Cyan
& $py -m pip uninstall -y yfinance

Write-Host "`n=== 4) Cleaning the cookie/timezone cache ===" -ForegroundColor Cyan
if (Test-Path $cacheDir) {
    try {
        Remove-Item -Path $cacheDir -Recurse -Force -ErrorAction Stop
        Write-Host "Cache removed: $cacheDir" -ForegroundColor Green
    } catch {
        Write-Host "UNABLE to remove the cache (file locked): $_" -ForegroundColor Red
        Write-Host "Close Spyder/Python and re-run the script." -ForegroundColor Yellow
    }
} else {
    Write-Host "No cache to remove."
}

Write-Host "`n=== 5) Reinstalling OFFICIAL yfinance (0.2.x) + dependencies ===" -ForegroundColor Cyan
# --no-cache-dir to avoid pulling 1.2.1 back from the pip cache
& $py -m pip install --no-cache-dir --upgrade "yfinance>=0.2.50,<0.3" "curl_cffi>=0.7" "pandas" "numpy"

Write-Host "`n=== 6) Verification ===" -ForegroundColor Cyan
& $py -m pip show yfinance 2>$null | Select-String "Version"
& $py -c "import yfinance as yf; print('import OK, runtime version:', yf.__version__)"

Write-Host "`n=== 7) Quick download test ===" -ForegroundColor Cyan
& $py -c "import yfinance as yf; from curl_cffi import requests as r; s=r.Session(impersonate='chrome'); df=yf.download('SPY QQQ', period='5d', progress=False, session=s); print('Rows:', len(df), '| Columns:', list(df.columns)[:4])"

Write-Host "`nDone. RESTART the Spyder kernel before re-running run_daily.py." -ForegroundColor Green
