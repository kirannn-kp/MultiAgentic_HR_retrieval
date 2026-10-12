<#
 Creates dist\mb-consultation-assistant.zip for handing the project to someone else.
 Excludes your settings (.env), Python environments, Azure deployment state and generated files.
#>
Set-Location $PSScriptRoot
$stage = Join-Path $env:TEMP "mb-consultation-assistant"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
robocopy . $stage /E /NFL /NDL /NJH /NJS /XD .venv .venv-hosted .azure __pycache__ .pytest_cache dist "*.egg-info" mbpoc /XF .env azure.yaml *.pyc `
  | Out-Null
# robocopy excluded every folder named 'mbpoc' (also src\mbpoc); add the real one back, but not the generated copy inside foundry-hosted
robocopy src\mbpoc (Join-Path $stage "src\mbpoc") /E /NFL /NDL /NJH /NJS /XD __pycache__ /XF *.pyc | Out-Null
New-Item -ItemType Directory -Force dist | Out-Null
$zip = "dist\mb-consultation-assistant.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$stage\*" -DestinationPath $zip
Remove-Item $stage -Recurse -Force
Write-Host "Created $zip"
