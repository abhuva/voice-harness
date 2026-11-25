[CmdletBinding()]
param(
    [string]$Assets,
    [string]$Pattern = 'test_*.py',
    [switch]$LocalModels,
    [ValidateRange(1,60)][int]$TimeoutSeconds = 60
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
if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Windows speech interpreter is missing.' }
$logs = Join-Path ([IO.Path]::GetTempPath()) ('voice-harness-tests-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $logs | Out-Null
$stdout = Join-Path $logs 'stdout.txt'
$stderr = Join-Path $logs 'stderr.txt'
$bootstrap = "import os, sys, unittest; sys.path[:0] = ['src', 'var/webrtc-audio-0.2.0']; os.environ['QT_QPA_PLATFORM'] = 'offscreen'; os.environ['VOICE_HARNESS_MODEL_FIXTURE'] = sys.argv[2] if sys.argv[3] == 'True' else ''; suite = unittest.defaultTestLoader.discover('tests', pattern=sys.argv[1]); sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())"
$arguments = @('-c', ('"{0}"' -f $bootstrap), ('"{0}"' -f $Pattern), ('"{0}"' -f $Assets), [string][bool]$LocalModels)
$p = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -PassThru `
    -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$handle = $p.Handle
$completed = $p.WaitForExit(1000 * $TimeoutSeconds)
if (!$completed) {
    Stop-Process -Id $p.Id -Force
    $null = $p.WaitForExit(5000)
}
Get-Content -LiteralPath $stdout
Get-Content -LiteralPath $stderr
Write-Host "Test logs in $logs"
if (!$completed) { throw "Test process exceeded $TimeoutSeconds seconds." }
if ($p.ExitCode -ne 0) { throw "Test process failed with exit code $($p.ExitCode)." }
