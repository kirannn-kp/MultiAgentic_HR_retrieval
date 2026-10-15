<#
 One-time setup. In a terminal opened in this folder:   .\setup.ps1
 Creates a private Python environment, installs what is needed and runs the guided Azure setup.
 (-InstallOnly installs the components without connecting to Azure.)
#>
param(
  [switch]$InstallOnly,
  [switch]$Yes,
  [switch]$UpdateExisting,
  [string]$KnowledgeBase,
  [string]$KBConnection
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Fail($msg) { Write-Host "`n$msg`n" -ForegroundColor Red; exit 1 }

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Fail "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), open a NEW terminal and run this again." }
$ver = [version](python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))")
if ($ver -lt [version]"3.10") { Fail "Python $ver found, but 3.10 or newer is required." }

if (-not (Test-Path ".venv\Scripts\python.exe")) { Write-Host "Creating Python environment ..."; python -m venv .venv }
$dependencyHash = ((Get-FileHash requirements.txt).Hash + (Get-FileHash pyproject.toml).Hash)
$installedHash = if (Test-Path ".venv\.installed") { ([string](Get-Content ".venv\.installed" -Raw)).Trim() } else { "" }
if ($installedHash -ne $dependencyHash) {
  Write-Host "Installing components (1-3 minutes the first time) ..."
  .\.venv\Scripts\python -m pip install --quiet --upgrade pip
  if ($LASTEXITCODE -ne 0) { Fail "Could not update pip. No installation marker was written." }
  .\.venv\Scripts\python -m pip install --quiet -r requirements.txt
  if ($LASTEXITCODE -ne 0) { Fail "Could not install the required components. Please retry setup." }
  .\.venv\Scripts\python -m pip install --quiet --no-deps -e .
  if ($LASTEXITCODE -ne 0) { Fail "Could not install the local application. Please retry setup." }
  Set-Content -Path ".venv\.installed" -Value $dependencyHash
}
if ($InstallOnly) { exit 0 }

if (-not (Get-Command az -ErrorAction SilentlyContinue)) { Fail "The Azure CLI is required. Install it from https://aka.ms/installazurecliwindows , open a NEW terminal, run 'az login', then run this again." }
$setupArgs = @()
if ($Yes) { $setupArgs += "--yes" }
if ($UpdateExisting) { $setupArgs += "--update-existing" }
if ($KnowledgeBase) { $setupArgs += @("--knowledge-base", $KnowledgeBase) }
if ($KBConnection) { $setupArgs += @("--kb-connection", $KBConnection) }
.\.venv\Scripts\python scripts\setup.py @setupArgs
exit $LASTEXITCODE
