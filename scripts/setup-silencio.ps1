$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'var/pocket-cuda/v4.0.1/Scripts/python.exe'
$destination = Join-Path $root 'var/silencio-2'
$repo = 'BlueRoseLabs/Silencio-2'
$revision = '20930f5f6a6bb564f2a8207152fd5c5d8eecff03'
if (!(Test-Path -LiteralPath $runtime -PathType Leaf) -or !(Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Silencio-2 uses the local Pocket TTS Python runtime. Install it first with scripts/setup-pocket-cuda.ps1.'
}
uv run --no-project --python 3.13 --with huggingface-hub --with hf-xet hf download $repo --revision $revision --local-dir $destination --exclude 'samples/*'
if ($LASTEXITCODE) { throw 'Could not download Silencio-2.' }
$entry = (Invoke-RestMethod -TimeoutSec 30 "https://huggingface.co/api/models/$repo/tree/$revision") |
    Where-Object path -eq 'model.safetensors'
$weights = Join-Path $destination 'model.safetensors'
if (!$entry.lfs.oid -or (Get-FileHash -LiteralPath $weights).Hash -ne $entry.lfs.oid) {
    throw 'Silencio-2 model checksum mismatch.'
}
uv pip install --python $runtime --no-upgrade -r (Join-Path $destination 'requirements.txt')
if ($LASTEXITCODE) { throw 'Silencio-2 dependencies could not be installed.' }
Write-Host "Silencio-2 is installed in $destination."
