$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonBin = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
& $pythonBin scripts/reproduce.py @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
