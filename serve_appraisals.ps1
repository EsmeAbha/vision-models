# Serve the rating-guide page to the rest of this network.
#
#   .\serve_appraisals.ps1 -Password "something-long"
#
# The page requires the password as soon as it listens anywhere but loopback;
# appraisal_server.py refuses to start otherwise. It accepts uploads and
# unpacks them, so an open one is not something to leave on a network.
#
# Needs the firewall opened once, from an ADMIN PowerShell:
#   New-NetFirewallRule -DisplayName "appraisal rating guide" -Direction Inbound `
#     -Action Allow -Protocol TCP -LocalPort 7885 -Profile Private
#
# Profile Private matters: this machine's Ethernet is on a private profile, and
# the rule stays off if it is ever joined to a public network.
param(
    [Parameter(Mandatory = $true)][string]$Password,
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 7885
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "python not found at $py" }
if ($Password.Length -lt 8) { throw "use a longer password than that" }

$env:APPRAISAL_HOST = $BindHost
$env:APPRAISAL_PORT = "$Port"
$env:APPRAISAL_PASSWORD = $Password

Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

Start-Process -FilePath $py -ArgumentList "-u", "appraisal_server.py" `
    -WorkingDirectory $root -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds(45)
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
$fw = Get-NetFirewallRule -DisplayName "appraisal rating guide" -ErrorAction SilentlyContinue

Write-Host ""
if ($listening) {
    Write-Host "  up    http://${ip}:${Port}   (and http://127.0.0.1:${Port} here)" -ForegroundColor Green
} else {
    Write-Host "  DOWN  it did not start listening on ${Port}" -ForegroundColor Red
}
if (-not $fw) {
    Write-Host ""
    Write-Host "  The firewall is not open, so other machines cannot reach it yet." -ForegroundColor Yellow
    Write-Host "  From an ADMIN PowerShell, once:" -ForegroundColor Yellow
    Write-Host "    New-NetFirewallRule -DisplayName 'appraisal rating guide' -Direction Inbound ``" -ForegroundColor DarkGray
    Write-Host "      -Action Allow -Protocol TCP -LocalPort $Port -Profile Private" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "  This is plain HTTP on your own network: the password is sent in" -ForegroundColor DarkGray
Write-Host "  clear to anyone who can watch the wifi. Do not reuse a password" -ForegroundColor DarkGray
Write-Host "  that opens anything else." -ForegroundColor DarkGray
Write-Host ""
