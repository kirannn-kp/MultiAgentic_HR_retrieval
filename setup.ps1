<#
 One-time setup. In a terminal opened in this folder:   .\setup.ps1
 Creates a private Python environment, installs what is needed and runs the guided Azure setup.
 (-InstallOnly installs the components without connecting to Azure.)
#>
param([switch]$InstallOnly, [switch]$Yes)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Fail($msg) { Write-Host "`n$msg`n" -ForegroundColor Red; exit 1 }

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Fail "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), open a NEW terminal and run this again." }
$ver = [version](python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))")
if ($ver -lt [version]"3.10") { Fail "Python $ver found, but 3.10 or newer is required." }

if (-not (Test-Path ".venv\Scripts\python.exe")) { Write-Host "Creating Python environment ..."; python -m venv .venv }
if (-not (Test-Path ".venv\.installed")) {
  Write-Host "Installing components (1-3 minutes the first time) ..."
  .\.venv\Scripts\python -m pip install --quiet --upgrade pip
  .\.venv\Scripts\python -m pip install --quiet -r requirements.txt
  .\.venv\Scripts\python -m pip install --quiet --no-deps -e .
  New-Item -ItemType File -Path ".venv\.installed" -Force | Out-Null
}
if ($InstallOnly) { exit 0 }

if (-not (Get-Command az -ErrorAction SilentlyContinue)) { Fail "The Azure CLI is required. Install it from https://aka.ms/installazurecliwindows , open a NEW terminal, run 'az login', then run this again." }
if ($Yes) { .\.venv\Scripts\python scripts\setup.py --yes } else { .\.venv\Scripts\python scripts\setup.py }
