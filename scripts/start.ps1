# Start QABuddy: Qdrant (background), Ollama check, then the web app on http://localhost:8000
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Storage = "$Root\storage"
New-Item -ItemType Directory -Force "$Storage\qdrant" | Out-Null

function Test-Url($url) {
    try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}

function Wait-Url($url, $seconds, $name) {
    for ($i = 0; $i -lt $seconds; $i++) {
        if (Test-Url $url) { return }
        Start-Sleep -Seconds 1
    }
    Write-Host "$name did not start within $seconds s." -ForegroundColor Red
    exit 1
}

# Qdrant
if (Test-Url "http://127.0.0.1:6333/") {
    Write-Host "Qdrant already running on :6333"
} else {
    if (-not (Test-Path "$Root\tools\qdrant\qdrant.exe")) {
        Write-Host "Qdrant is not installed. Run scripts\setup.ps1 first." -ForegroundColor Red
        exit 1
    }
    $env:QDRANT__STORAGE__STORAGE_PATH = "$Storage\qdrant"
    $env:QDRANT__STORAGE__SNAPSHOTS_PATH = "$Storage\qdrant-snapshots"
    $env:QDRANT__SERVICE__HOST = "127.0.0.1"
    $env:QDRANT__TELEMETRY_DISABLED = "true"
    Start-Process -FilePath "$Root\tools\qdrant\qdrant.exe" -WorkingDirectory $Storage -WindowStyle Hidden `
        -RedirectStandardOutput "$Storage\qdrant.log" -RedirectStandardError "$Storage\qdrant.err.log"
    Wait-Url "http://127.0.0.1:6333/" 30 "Qdrant"
    Write-Host "Qdrant started (logs: storage\qdrant.log)"
}

# Ollama
if (-not (Test-Url "http://127.0.0.1:11434/api/tags")) {
    Write-Host "Starting Ollama..."
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Wait-Url "http://127.0.0.1:11434/api/tags" 30 "Ollama"
}
Write-Host "Ollama is running on :11434"

# Web UI build
if (-not (Test-Path "$Root\frontend\dist\index.html")) {
    Write-Host "Building the web UI..."
    Push-Location "$Root\frontend"; npm run build; Pop-Location
}

# Open the browser once the API answers
Start-Job -ScriptBlock {
    for ($i = 0; $i -lt 120; $i++) {
        try { Invoke-WebRequest "http://127.0.0.1:8000/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process "http://localhost:8000"; break } catch { Start-Sleep 1 }
    }
} | Out-Null

Write-Host "Starting QABuddy on http://localhost:8000 (Ctrl+C to stop)" -ForegroundColor Green
Write-Host "If the index is empty, run in another terminal:  cd backend; uv run qabuddy ingest"
Push-Location "$Root\backend"
try { uv run qabuddy serve } finally { Pop-Location }
