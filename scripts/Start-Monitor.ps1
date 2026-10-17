$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts\Setup-Monitor.ps1 first.' }
Set-Location -LiteralPath $projectRoot
$server = Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $projectRoot -PassThru
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if ($server.HasExited) { throw 'The server exited. Check its console; port 8000 might already be in use.' }
    try {
        $null = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 1
        Start-Process 'http://127.0.0.1:8000'
        exit 0
    } catch { Start-Sleep -Milliseconds 500 }
}
Write-Warning 'The API has not responded yet. Check the server console.'
