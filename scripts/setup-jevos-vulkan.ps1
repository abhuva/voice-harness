[CmdletBinding()]
param([string]$Downloads)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$parent = Join-Path $root 'var/jevos-vulkan'
$target = Join-Path $parent 'b12777'
$modelName = 'jevos-v5-q8_0.gguf'
$modelHash = 'F20F77D2D49C8863FE23A04C1BD237139182BDC97BD2DFC2275F48AD5E4091FA'
$serverArchiveHash = '0C9CDBB1304C413CC1B28103B62DE21CFB9291CDAE60BC1A2A859E806AE6FAC1'

function Assert-Hash($File, $Expected) {
    if ((Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash -ne $Expected) {
        throw "SHA256 mismatch for $(Split-Path -Leaf $File)."
    }
}

if (Test-Path -LiteralPath $target) {
    Assert-Hash (Join-Path $target $modelName) $modelHash
    if (!(Test-Path -LiteralPath (Join-Path $target 'llama-server.exe') -PathType Leaf)) {
        throw 'The Jevos Vulkan installation is missing llama-server.exe.'
    }
    Write-Host "Jevos Vulkan is installed in $target"
    return
}

$stage = Join-Path $parent ('.setup-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $stage -Force
$zip = Join-Path $stage 'llama.zip'
$model = Join-Path $stage $modelName
try {
    if ($Downloads) {
        Copy-Item -LiteralPath (Join-Path $Downloads 'llama.zip') -Destination $zip
        Copy-Item -LiteralPath (Join-Path $Downloads 'jevos.gguf') -Destination $model
    } else {
        curl.exe -fL --retry 2 --connect-timeout 15 --max-time 600 -o $zip 'https://github.com/ggml-org/llama.cpp/releases/download/b12777/llama-b12777-bin-win-vulkan-x64.zip'
        if ($LASTEXITCODE) { throw 'Could not download the Jevos Vulkan runner.' }
        curl.exe -fL --retry 2 --connect-timeout 15 --max-time 600 -o $model 'https://github.com/feder-cr/jev/releases/download/jevos-v5/jevos-v5-q8_0.gguf'
        if ($LASTEXITCODE) { throw 'Could not download the Jevos v5 Q8 model.' }
    }
    Assert-Hash $zip $serverArchiveHash
    Assert-Hash $model $modelHash
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zip)
    try {
        foreach ($entry in $archive.Entries) {
            if ($entry.Name -match '^(ggml.*\.dll|llama(-common|-server-impl)?\.dll|mtmd\.dll|libomp\.dll|llama-server\.exe|LICENSE[^\\/]*)$') {
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $stage $entry.Name))
            }
        }
    } finally {
        $archive.Dispose()
    }
    Remove-Item -LiteralPath $zip
    $resolvedParent = (Resolve-Path -LiteralPath $parent).Path
    $resolvedStage = (Resolve-Path -LiteralPath $stage).Path
    if ((Split-Path -Parent $resolvedStage) -ne $resolvedParent) { throw 'Invalid Vulkan setup directory.' }
    [IO.Directory]::Move($resolvedStage, (Join-Path $resolvedParent 'b12777'))
    Write-Host "Installed Jevos Vulkan in $target"
} finally {
    if (Test-Path -LiteralPath $stage) {
        Get-ChildItem -LiteralPath $stage -File | ForEach-Object { Remove-Item -LiteralPath $_.FullName }
        [IO.Directory]::Delete($stage)
    }
}
