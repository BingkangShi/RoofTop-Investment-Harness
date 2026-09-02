param(
    [int]$Port = 8765
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
Write-Host "Starting RoofTop Investment Harness (天台智投)..." -ForegroundColor Cyan
Write-Host "Open http://127.0.0.1:$Port in your browser." -ForegroundColor Green
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { $Python = "python" }
& $Python -c "from app.server import run; run(port=$Port)"
