<#
 Optional: publishes the A2A gateway on Azure Container Apps so your MB Enterprise Chat (for example on AWS) can call the orchestrator.
 Prerequisites: .\setup.ps1 and .\deploy-hosted.ps1 finished; you are signed in with: az login
#>
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) { Write-Host "Run .\setup.ps1 first." -ForegroundColor Red; exit 1 }
.\.venv\Scripts\python scripts\deploy_gateway.py
