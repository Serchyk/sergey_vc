# Локальный запуск
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) { python -m venv .venv }
& "$root\.venv\Scripts\python.exe" -m pip install -q -r requirements.txt

$server = Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru
Write-Host "http://127.0.0.1:8000/result" -ForegroundColor Green
Write-Host "PID: $($server.Id)"