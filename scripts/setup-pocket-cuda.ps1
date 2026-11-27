[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'var/pocket-cuda/v4.0.1'
$python = Join-Path $runtime 'Scripts/python.exe'
if (!(Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Install uv to set up the isolated Pocket TTS CUDA runtime.' }
if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
    uv venv --python 3.13 $runtime
    if ($LASTEXITCODE) { throw 'Could not create the Pocket TTS CUDA environment.' }
}
$installed = & $python -I -c "from importlib.metadata import distributions; d={p.metadata['Name'].lower():p.version for p in distributions()}; print(d.get('torch'),d.get('pocket-tts'))"
if ($LASTEXITCODE -ne 0 -or $installed -ne '2.8.0+cu126 4.0.1') {
    $env:UV_HTTP_TIMEOUT = '120'
    $torch = 'torch @ https://download.pytorch.org/whl/cu126/torch-2.8.0%2Bcu126-cp313-cp313-win_amd64.whl'
    uv pip install --python $python $torch 'pocket-tts==4.0.1'
    if ($LASTEXITCODE) { throw 'Could not install the Pocket TTS CUDA runtime.' }
}
& $python -I -c "import torch; assert torch.cuda.is_available(); assert torch.ones(1, device='cuda').item() == 1; print(torch.cuda.get_device_name(0))"
if ($LASTEXITCODE) { throw 'The installed PyTorch build could not run on the NVIDIA GPU.' }
Write-Host "Pocket TTS CUDA is installed in $runtime"
