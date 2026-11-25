[CmdletBinding()]
param([string]$Assets)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (!$Assets) {
    $Assets = Join-Path $root 'var'
    if (!(Test-Path -LiteralPath (Join-Path $Assets 'voice/runtime/python.exe'))) {
        $Assets = Join-Path (Split-Path -Parent $root) 'kindroid/var'
    }
}
$Assets = (Resolve-Path -LiteralPath $Assets).Path
$python = Join-Path $Assets 'voice/runtime/python.exe'
if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Restore the Windows voice bundle or supply its directory with -Assets.'
}
$bootstrap = "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module('voice_harness', run_name='__main__')"
& $python -c $bootstrap (Join-Path $root 'src') --root $root --assets $Assets
if ($LASTEXITCODE) { throw 'Voice Harness did not start or close normally.' }
