# Serve the FinAI chat page to other machines on the network.
#
#   .\serve_finai.ps1 -Password "something-long"
#
# A password is REQUIRED, not optional, and the page refuses to start without
# one when it is not bound to localhost. The other pages accept an upload and
# hand back a result; this one gives a chat model tools that read any path on
# this machine, so an open port is a way for anyone who can reach it to read
# the disk by asking.
#
# The firewall needs opening once, from an ADMIN PowerShell:
#   New-NetFirewallRule -DisplayName "FinAI" -Direction Inbound `
#     -Action Allow -Protocol TCP -LocalPort 7870 -Profile Private
#
# Keep it to Private. On a public or guest network, do not run this at all.
param(
    [Parameter(Mandatory = $true)][string]$Password,
    [string]$User = "esme",
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 7870
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "python not found at $py" }

if ($Password.Length -lt 8) {
    throw "Use a longer password. This port reaches a tool that can read files."
}

$env:VM_HOST = $BindHost
$env:VM_USER = $User
$env:VM_PASS = $Password

# Free the port if something is already on it.
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

Start-Process -FilePath $py -ArgumentList "-u", "finai.py" `
    -WorkingDirectory $root -WindowStyle Hidden

Start-Sleep -Seconds 4

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notlike '127.*' -and
                      $_.IPAddress -notlike '169.254.*' } |
       Select-Object -First 1).IPAddress

Write-Host ""
Write-Host "FinAI is serving on the network." -ForegroundColor Green
Write-Host "  From this machine : http://127.0.0.1:$Port/finai"
Write-Host "  From another PC   : http://${ip}:$Port/finai"
Write-Host "  Sign in as        : $User"
Write-Host ""
Write-Host "If another machine cannot reach it, the firewall rule is missing."
Write-Host "Run this once in an ADMIN PowerShell:" -ForegroundColor Yellow
Write-Host "  New-NetFirewallRule -DisplayName 'FinAI' -Direction Inbound ``"
Write-Host "    -Action Allow -Protocol TCP -LocalPort $Port -Profile Private"
Write-Host ""
Write-Host "To stop it:  Get-NetTCPConnection -LocalPort $Port -State Listen |" -NoNewline
Write-Host " Stop-Process -Id { `$_.OwningProcess } -Force"
