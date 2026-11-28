[CmdletBinding()]
param([string]$Assets, [switch]$SkipServer)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (!$Assets) {
    $Assets = Join-Path $root 'var'
    if (!(Test-Path -LiteralPath (Join-Path $Assets 'voice/runtime/python.exe'))) {
        $Assets = Join-Path (Split-Path -Parent $root) 'kindroid/var'
    }
}
$python = Join-Path $Assets 'voice/runtime/python.exe'
if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Restore the Windows voice bundle or supply its directory with -Assets.'
}
$modelIds = @()
if (!$SkipServer) { $modelIds = @(& (Join-Path $PSScriptRoot 'start-lfm3-server.ps1')) }
$bootstrap = "import sys; sys.path.insert(0, sys.argv.pop(1)); from voice_harness.lfm3_playground import main; sys.exit(main(sys.argv[1:]))"
& $python -c $bootstrap (Join-Path $root 'src') @modelIds
if ($LASTEXITCODE) { throw 'LFM3 Playground did not start or close normally.' }
