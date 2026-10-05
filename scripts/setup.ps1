# QABuddy one-time setup (safe to re-run). Run from any folder:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$QdrantVersion = "v1.19.1"

function Require($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Host "Missing '$name'. $hint" -ForegroundColor Red
        exit 1
    }
}

function Get-SettingValue($key) {
    $line = Select-String -Path "$Root\config\settings.yaml" -Pattern "^\s*${key}:\s*`"?([^`"#]+)`"?" | Select-Object -First 1
    return $line.Matches[0].Groups[1].Value.Trim()
}

Require "uv" "Install it from https://docs.astral.sh/uv/ (winget install astral-sh.uv)."
Require "ollama" "Install it from https://ollama.com/download."
Require "npm" "Install Node.js 20+ from https://nodejs.org/."

Write-Host "1/4 Python dependencies (uv, Python 3.12)..." -ForegroundColor Cyan
Push-Location "$Root\backend"
uv sync
Pop-Location

Write-Host "2/4 Qdrant $QdrantVersion..." -ForegroundColor Cyan
$QdrantDir = "$Root\tools\qdrant"
if (-not (Test-Path "$QdrantDir\qdrant.exe")) {
    New-Item -ItemType Directory -Force $QdrantDir | Out-Null
    $zip = "$QdrantDir\qdrant.zip"
    Invoke-WebRequest "https://github.com/qdrant/qdrant/releases/download/$QdrantVersion/qdrant-x86_64-pc-windows-msvc.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath $QdrantDir -Force
    Remove-Item $zip
} else {
    Write-Host "   already installed"
}

Write-Host "3/4 Ollama models..." -ForegroundColor Cyan
ollama pull (Get-SettingValue "embed_model")
ollama pull (Get-SettingValue "llm_model")

Write-Host "4/4 Web UI..." -ForegroundColor Cyan
Push-Location "$Root\frontend"
if (Test-Path "package-lock.json") { npm ci --no-audit --no-fund } else { npm install --no-audit --no-fund }
npm run build
Pop-Location

Write-Host "`nSetup complete. Next:" -ForegroundColor Green
Write-Host "  powershell -File scripts\start.ps1          # start Qdrant + the web app"
Write-Host "  cd backend; uv run qabuddy ingest           # index data\ (first run is slow on CPU)"
