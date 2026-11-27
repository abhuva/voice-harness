[CmdletBinding()]
param(
    [string]$Assets,
    [switch]$Phone,
    [string]$PhoneOrigin,
    [int]$PhonePort = 8768,
    [string]$PhoneIceAddress
)

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
$bootstrap = "import runpy, sys; sys.path[:0] = [sys.argv.pop(1), sys.argv.pop(1)]; phone_path = sys.argv.pop(1); sys.path[:0] = [phone_path] if '--phone' in sys.argv else []; runpy.run_module('voice_harness', run_name='__main__')"
$appArgs = @('--root', $root, '--assets', $Assets)
if ($Phone) {
    $appArgs += @('--phone', '--phone-port', $PhonePort)
    if ($PhoneOrigin) {
        $appArgs += @('--phone-origin', $PhoneOrigin)
        if (!$PhoneIceAddress -and (Get-Command tailscale -ErrorAction SilentlyContinue)) {
            $detected = & tailscale ip -4 2>$null
            if (!$LASTEXITCODE -and $detected) { $PhoneIceAddress = @($detected)[0] }
        }
    }
    if ($PhoneIceAddress) { $appArgs += @('--phone-ice-address', $PhoneIceAddress) }
}
$phonePath = Join-Path $root 'var/phone-runtime'
& $python -c $bootstrap (Join-Path $root 'src') (Join-Path $root 'var/webrtc-audio-0.2.0') $phonePath @appArgs
if ($LASTEXITCODE) { throw 'Voice Harness did not start or close normally.' }
