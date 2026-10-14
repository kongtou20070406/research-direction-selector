param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Suite,
    [Parameter(Mandatory=$true)][string]$Manifest,
    [Parameter(Mandatory=$true)][string]$Log
)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$suiteFile = (Resolve-Path -LiteralPath $Suite).Path
$manifestFile = (Resolve-Path -LiteralPath $Manifest).Path
Set-Location -LiteralPath (Split-Path -Parent $manifestFile)
try {
    & $Python -B $suiteFile --manifest $manifestFile *> $Log
    if ($LASTEXITCODE -ne 0) { throw "Frozen experiment process exited $LASTEXITCODE" }
} catch {
    $_ | Out-String | Add-Content -LiteralPath $Log -Encoding utf8
    exit 1
}
