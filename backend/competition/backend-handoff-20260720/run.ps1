[CmdletBinding()]
param([string]$Cmd = "start")
& (Join-Path $PSScriptRoot "../../platform_backend/run.ps1") $Cmd
exit $LASTEXITCODE
