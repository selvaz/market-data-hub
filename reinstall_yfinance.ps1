# ============================================================================
# reinstall_yfinance.ps1 — disinstalla, pulisce la cache e reinstalla yfinance
#
# PERCHE': la versione installata (1.2.1) non e' una release ufficiale yfinance
# (l'ultima vera e' 0.2.x) e la cache cookie/tz SQLite resta bloccata dal kernel
# Spyder -> errore deterministico "unexpected character: line 1 column 1".
#
# PRIMA DI LANCIARE: chiudi Spyder / qualunque kernel Python aperto, altrimenti
# i file cookies.db / tkr-tz.db restano lock-ati e non si cancellano.
#
# Uso (da PowerShell):
#     powershell -ExecutionPolicy Bypass -File D:\market_data\reinstall_yfinance.ps1
# ============================================================================
$ErrorActionPreference = "Continue"

$py = "C:\Users\Marco\AppData\Local\Programs\Python\Python311\python.exe"
$cacheDir = "$env:LOCALAPPDATA\py-yfinance"

Write-Host "=== 1) Versione attuale ===" -ForegroundColor Cyan
& $py -m pip show yfinance 2>$null | Select-String "Version"

Write-Host "`n=== 2) Chiusura processi Python che bloccano la cache ===" -ForegroundColor Cyan
# Mostra eventuali python in esecuzione (Spyder kernel, ecc.)
$procs = Get-Process python, pythonw -ErrorAction SilentlyContinue
if ($procs) {
    Write-Host "ATTENZIONE: trovati processi Python attivi (potrebbero bloccare la cache):"
    $procs | Select-Object Id, ProcessName, StartTime | Format-Table -AutoSize
    Write-Host "Se la pulizia cache fallisce, chiudili e rilancia." -ForegroundColor Yellow
} else {
    Write-Host "Nessun processo Python attivo - OK."
}

Write-Host "`n=== 3) Disinstallazione yfinance (bogus 1.2.1) ===" -ForegroundColor Cyan
& $py -m pip uninstall -y yfinance

Write-Host "`n=== 4) Pulizia cache cookie/timezone ===" -ForegroundColor Cyan
if (Test-Path $cacheDir) {
    try {
        Remove-Item -Path $cacheDir -Recurse -Force -ErrorAction Stop
        Write-Host "Cache rimossa: $cacheDir" -ForegroundColor Green
    } catch {
        Write-Host "IMPOSSIBILE rimuovere la cache (file bloccato): $_" -ForegroundColor Red
        Write-Host "Chiudi Spyder/Python e rilancia lo script." -ForegroundColor Yellow
    }
} else {
    Write-Host "Nessuna cache da rimuovere."
}

Write-Host "`n=== 5) Reinstallazione yfinance UFFICIALE (0.2.x) + dipendenze ===" -ForegroundColor Cyan
# --no-cache-dir per non ripescare la 1.2.1 dalla cache pip
& $py -m pip install --no-cache-dir --upgrade "yfinance>=0.2.50,<0.3" "curl_cffi>=0.7" "pandas" "numpy"

Write-Host "`n=== 6) Verifica ===" -ForegroundColor Cyan
& $py -m pip show yfinance 2>$null | Select-String "Version"
& $py -c "import yfinance as yf; print('import OK, versione runtime:', yf.__version__)"

Write-Host "`n=== 7) Test download rapido ===" -ForegroundColor Cyan
& $py -c "import yfinance as yf; from curl_cffi import requests as r; s=r.Session(impersonate='chrome'); df=yf.download('SPY QQQ', period='5d', progress=False, session=s); print('Righe:', len(df), '| Colonne:', list(df.columns)[:4])"

Write-Host "`nFatto. RIAVVIA il kernel Spyder prima di rilanciare run_daily.py." -ForegroundColor Green
