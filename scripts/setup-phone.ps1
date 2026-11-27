[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$local = Join-Path $root 'var'
$runtime = Join-Path $local 'phone-runtime'
if (!(Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Install uv and CPython 3.13 to set up the optional phone runtime.'
}
$python = & uv python find 3.13
if ($LASTEXITCODE) { throw 'Install CPython 3.13 to match the Windows voice bundle.' }
& uv pip install --target $runtime --python $python --only-binary :all: --upgrade -r (Join-Path $root 'requirements-phone.txt')
if ($LASTEXITCODE) { throw 'Phone runtime installation failed.' }
Write-Host "Phone runtime installed in $runtime"
