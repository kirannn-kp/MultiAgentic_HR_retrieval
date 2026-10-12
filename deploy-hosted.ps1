<#
 Optional: runs the orchestrator as a Microsoft Foundry hosted agent (Microsoft Agent Framework).
 Prerequisite: .\setup.ps1 finished, and you signed in to azd once:  azd auth login --use-device-code
#>
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) { Write-Host "Run .\setup.ps1 first." -ForegroundColor Red; exit 1 }
if (-not (Get-Command azd -ErrorAction SilentlyContinue)) {
  Write-Host "Installing the Azure Developer CLI (azd) ..."
  winget install Microsoft.Azd --silent --accept-package-agreements --accept-source-agreements
  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
}
.\.venv\Scripts\python scripts\deploy_hosted.py
