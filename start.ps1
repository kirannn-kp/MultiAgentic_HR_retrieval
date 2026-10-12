<#
 Starts the demo and opens http://127.0.0.1:8000 . Stop with Ctrl+C.
 Works before the Azure setup too (offline simulation).
#>
Set-Location $PSScriptRoot
.\setup.ps1 -InstallOnly
if ($LASTEXITCODE -ne 0) { exit 1 }
Start-Process "http://127.0.0.1:8000"
.\.venv\Scripts\python -m mbpoc.run_all
