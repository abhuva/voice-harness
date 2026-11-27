[CmdletBinding()]
param([string]$Downloads)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$parent = Join-Path $root 'var/fluid-vulkan'
$target = Join-Path $parent 'v0.6.1'
$serverHash = '0C834C36DFC2CEAB0D25ADE2633B7605B314EF0869DEE45AA9716C7919E7B715'
$modelHash = '1B9C613FE3B058895885105E696C203A1A0B47C37EACE80D18B51E20DCDC5FFD'
$modelName = 'fluid-speech-pro-q8_0.gguf'

function Assert-Hash($File, $Expected) {
    if ((Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash -ne $Expected) {
        throw "SHA256 mismatch for $(Split-Path -Leaf $File)."
    }
}

if (Test-Path -LiteralPath $target) {
    Assert-Hash (Join-Path $target 'parakeet-server.exe') $serverHash
    Assert-Hash (Join-Path $target $modelName) $modelHash
    Write-Host "Fluid Speech Pro Vulkan is installed in $target"
    return
}

$stage = Join-Path $parent ('.setup-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $stage -Force
$zip = Join-Path $stage 'parakeet.zip'
$model = Join-Path $stage $modelName
$server = Join-Path $stage 'parakeet-server.exe'
$license = Join-Path $stage 'parakeet-LICENSE'
try {
    if ($Downloads) {
        Copy-Item -LiteralPath (Join-Path $Downloads 'parakeet.zip') -Destination $zip
        Copy-Item -LiteralPath (Join-Path $Downloads 'fluid.gguf') -Destination $model
    } else {
        Write-Host 'Downloading the Windows Vulkan worker and Fluid Speech Pro model'
        curl.exe -fL --retry 2 --connect-timeout 15 --max-time 600 -o $zip 'https://github.com/mudler/parakeet.cpp/releases/download/v0.6.1/parakeet-v0.6.1-bin-win-vulkan-x64.zip'
        if ($LASTEXITCODE) { throw 'Could not download the Vulkan worker.' }
        curl.exe -fL --retry 2 --connect-timeout 15 --max-time 600 -o $model 'https://huggingface.co/FluidVoice-Labs/fluid-speech-pro-gguf/resolve/fee970d9ac83f6d236632d39b67cbec12f5a9020/fluid-speech-pro-q8_0.gguf'
        if ($LASTEXITCODE) { throw 'Could not download Fluid Speech Pro.' }
    }
    Assert-Hash $zip '1B9ADFB041394EBD016865A15699614A3C095876E07E7C3D67669D5A00CDDA86'
    Assert-Hash $model $modelHash
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zip)
    try {
        $prefix = 'parakeet-v0.6.1-bin-win-vulkan-x64/'
        [IO.Compression.ZipFileExtensions]::ExtractToFile($archive.GetEntry($prefix + 'parakeet-server.exe'), $server)
        [IO.Compression.ZipFileExtensions]::ExtractToFile($archive.GetEntry($prefix + 'LICENSE'), $license)
    } finally {
        $archive.Dispose()
    }
    Assert-Hash $server $serverHash
    Remove-Item -LiteralPath $zip
    $resolvedParent = (Resolve-Path -LiteralPath $parent).Path
    $resolvedStage = (Resolve-Path -LiteralPath $stage).Path
    if ((Split-Path -Parent $resolvedStage) -ne $resolvedParent) { throw 'Invalid Vulkan setup directory.' }
    [IO.Directory]::Move($resolvedStage, (Join-Path $resolvedParent 'v0.6.1'))
    Write-Host "Installed Fluid Speech Pro Vulkan in $target"
} finally {
    foreach ($file in $zip, $model, $server, $license) {
        if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file }
    }
    if (Test-Path -LiteralPath $stage) { [IO.Directory]::Delete($stage) }
}
