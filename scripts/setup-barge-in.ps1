# Installs the pinned Windows x64 wheel for the checkout's portable CPython 3.13.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = New-Item -ItemType Directory -Force -Path (Join-Path $root 'var')
$destination = Join-Path $runtime.FullName 'webrtc-audio-0.2.0'
if (Test-Path -LiteralPath $destination) {
    Write-Host 'The local WebRTC audio package is already installed.'
    return
}
$staging = New-Item -ItemType Directory -Path (Join-Path $runtime.FullName ('.webrtc-' + [guid]::NewGuid()))
try {
    $archive = Join-Path $staging.FullName 'webrtc.zip'
    $uri = 'https://files.pythonhosted.org/packages/89/1e/ff2d90d0450bb2c117f314c946b4a780ff80e65d98d7e760bb4a8d627b22/pywebrtc_audio-0.2.0-cp313-cp313-win_amd64.whl'
    Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $archive -TimeoutSec 30
    $sha256 = '346F57A3E06E164FA9E8298C7ECB91D19333618E29A0FF6D972AC4C46A970A9D'
    if ((Get-FileHash -LiteralPath $archive).Hash -ne $sha256) { throw 'WebRTC wheel checksum mismatch.' }
    $package = Join-Path $staging.FullName 'package'
    Expand-Archive -LiteralPath $archive -DestinationPath $package
    [IO.Directory]::Move($package, $destination)
    Write-Host 'Installed the local WebRTC audio package.'
} finally {
    $resolved = (Resolve-Path -LiteralPath $staging.FullName).Path
    $prefix = $runtime.FullName + [IO.Path]::DirectorySeparatorChar
    if ($resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
