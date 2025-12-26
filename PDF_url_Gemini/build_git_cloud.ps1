$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

pyinstaller `
  --clean `
  --noconfirm `
  --distpath "$ProjectRoot\\dist" `
  --workpath "$ProjectRoot\\build" `
  "$ProjectRoot\\Git Cloud.spec"

Write-Host "Built: $ProjectRoot\\dist\\Git Cloud.exe"
