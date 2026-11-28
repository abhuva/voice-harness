[CmdletBinding()]
param([ValidateRange(1024, 65535)][int]$Port = 18770)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$modelDir = Join-Path $root 'var/lfm3'
$serverPath = Join-Path $root 'var/jevos-vulkan/b12777/llama-server.exe'
$catalog = [ordered]@{
    'lfm3-local' = 'LFM3-1.7B-Q4_K_M.gguf'
    'lfm3-abliterated' = 'LFM3-1.7B-abliterated-Q4_K_M.gguf'
}
$available = @($catalog.Keys | Where-Object {
    Test-Path -LiteralPath (Join-Path $modelDir $catalog[$_]) -PathType Leaf
})
if (!$available.Count -or !(Test-Path -LiteralPath $serverPath -PathType Leaf)) {
    throw 'The local LFM3 model or its Vulkan runner is missing. Use -SkipServer for your own endpoint.'
}

$origin = "http://127.0.0.1:$Port"
function Test-Catalog {
    try {
        $models = Invoke-RestMethod -UseBasicParsing -TimeoutSec 2 -Uri "$origin/v1/models"
        foreach ($id in $available) {
            if ($id -notin @($models.data.id)) { return $false }
        }
        return $true
    } catch { return $false }
}

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    if (!(Test-Catalog)) { throw "Port $Port is already in use by a server without the installed LFM3 models." }
    return $available
}

$presets = Join-Path $modelDir "models-$Port.ini"
$lines = @('version = 1')
foreach ($id in $available) {
    $lines += @('', "[$id]", ('model = ./' + $catalog[$id]))
}
$lines | Set-Content -LiteralPath $presets -Encoding Ascii

$llamaArgs = @(
    '--models-preset', ('"{0}"' -f $presets), '--models-max', '1', '--host', '127.0.0.1', '--port', $Port,
    '--device', 'Vulkan0', '--n-gpu-layers', 'all', '--fit', 'off',
    '--ctx-size', '4096', '--parallel', '1', '--threads', '2', '--threads-batch', '2',
    '--batch-size', '128', '--ubatch-size', '64', '--no-warmup',
    '--reasoning', 'off', '--temp', '0.1', '--top-k', '50', '--repeat-penalty', '1.1',
    '--no-webui', '--no-slots', '--no-cache-prompt', '--cache-ram', '0',
    '--sleep-idle-seconds', '45', '--log-disable'
)
$previousLayers = $env:VK_LOADER_LAYERS_DISABLE
$previousCache = $env:LLAMA_CACHE
$process = $null
$ready = $false
try {
    $env:VK_LOADER_LAYERS_DISABLE = 'VK_LAYER_AMD_switchable_graphics,VK_LAYER_OW_*'
    $env:LLAMA_CACHE = Join-Path $modelDir 'cache'
    $stdout = Join-Path $modelDir "router-$Port-out.txt"
    $stderr = Join-Path $modelDir "router-$Port-err.txt"
    $process = Start-Process -FilePath $serverPath -ArgumentList $llamaArgs -WorkingDirectory $modelDir `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $handle = $process.Handle
    $deadline = (Get-Date).AddSeconds(20)
    do {
        if ($process.HasExited) { throw "The LFM3 server exited with code $($process.ExitCode)." }
        if (Test-Catalog) {
            $ready = $true
            return $available
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw 'The LFM3 server did not list the installed models in time.'
} finally {
    $env:VK_LOADER_LAYERS_DISABLE = $previousLayers
    $env:LLAMA_CACHE = $previousCache
    if ($process -and !$ready -and !$process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $null = $process.WaitForExit(2000)
    }
}
