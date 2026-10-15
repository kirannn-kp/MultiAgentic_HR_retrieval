<#
 Starts the demo and opens http://127.0.0.1:8000 . Stop with Ctrl+C.
 Works before the Azure setup too (offline simulation).
#>
param([switch]$RegionalDemo)
Set-Location $PSScriptRoot
.\setup.ps1 -InstallOnly
if ($LASTEXITCODE -ne 0) { exit 1 }
if ($RegionalDemo) {
  $env:REGIONAL_RETRIEVAL_ENABLED = "true"
  $env:MOCK_MODE = "true"
  $env:MOCK_LATENCY_SEC = "0"
  $env:ORCHESTRATOR_MODE = "local"
  $env:ORCHESTRATOR_HOST = "127.0.0.1"
  $env:ORCHESTRATOR_PORT = "8001"
  $env:ORCHESTRATOR_URL = "http://127.0.0.1:8001"
  $env:MB_CHAT_HOST = "127.0.0.1"
  $env:MB_CHAT_PORT = "8000"
  $env:MB_CHAT_URL = "http://127.0.0.1:8000"
  Write-Host "Regional offline demo: Germany and European Union. No Azure calls or website retrieval."
}
Start-Process "http://127.0.0.1:8000"
.\.venv\Scripts\python -m mbpoc.run_all
