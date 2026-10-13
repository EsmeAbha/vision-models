# Serve one Ollama model to the network, behind an API key.
#
#   .\serve_qwen_api.ps1 -ApiKey "sk-local-..."
#
# qwen_api.py refuses to start if it would listen beyond this machine without
# a key, so the key is required here rather than optional. What it exposes is
# one model and the OpenAI-compatible read routes only -- the native /api/*
# surface is not proxied, so nothing reachable through it can pull or delete
# a model.
#
# The firewall needs opening once, from an ADMIN PowerShell:
#   New-NetFirewallRule -DisplayName "qwen api" -Direction Inbound `
#     -Action Allow -Protocol TCP -LocalPort 11435 -Profile Private
#
# Keep it to Private. On a public or guest network, do not run this at all:
# it is plain HTTP, so the key crosses the wifi in clear.
param(
    [Parameter(Mandatory = $true)][string]$ApiKey,
    [string]$Model = "qwen3.8:27b",
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 11435
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "python not found at $py" }
if ($ApiKey.Length -lt 16) { throw "use a longer key than that" }

$env:QWEN_API_HOST = $BindHost
$env:QWEN_API_PORT = "$Port"
$env:QWEN_API_KEY = $ApiKey
$env:QWEN_API_MODEL = $Model

# Free the port if an older copy is still on it.
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

# Start-Process, not a background job: this has to outlive the shell that
# launched it, and a job dies with its session.
Start-Process -FilePath $py -ArgumentList "-u", "qwen_api.py" `
    -WorkingDirectory $root -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { break }
    Start-Sleep -Milliseconds 500
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notlike "127.*" -and
                      $_.IPAddress -notlike "169.254.*" -and
                      $_.InterfaceAlias -notlike "*WSL*" } |
       Select-Object -First 1).IPAddress

$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
$fw = Get-NetFirewallRule -DisplayName "qwen api" -ErrorAction SilentlyContinue

Write-Host ""
if ($listening) {
    Write-Host "  up    http://${ip}:${Port}/v1   model $Model" -ForegroundColor Green
} else {
    Write-Host "  DOWN  it did not start listening on ${Port}" -ForegroundColor Red
    Write-Host "        qwen_api.py refuses to bind wide with no key -- check -ApiKey" -ForegroundColor DarkGray
}
if (-not $fw) {
    Write-Host ""
    Write-Host "  The firewall is not open, so other machines cannot reach it yet." -ForegroundColor Yellow
    Write-Host "  From an ADMIN PowerShell, once:" -ForegroundColor Yellow
    Write-Host "    New-NetFirewallRule -DisplayName 'qwen api' -Direction Inbound ``" -ForegroundColor DarkGray
    Write-Host "      -Action Allow -Protocol TCP -LocalPort $Port -Profile Private" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "  $Model does not fit this card: ~12.6GB of 18.1GB loads into VRAM" -ForegroundColor DarkGray
Write-Host "  and the rest runs on CPU. It also cannot be resident at the same" -ForegroundColor DarkGray
Write-Host "  time as the OCR server, so expect contention with a page read." -ForegroundColor DarkGray
Write-Host ""
