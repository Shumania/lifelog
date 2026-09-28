# net-diag-v1: READ-ONLY network fingerprint diagnostics (WAN IPv4, ISP/CGNAT hints, LAN gateway, Wi-Fi profiles+BSSIDs, Tailscale exit node). Modifies nothing.
$ErrorActionPreference = 'Continue'
function Sec($t) { Write-Output ""; Write-Output "--- $t ---" }
function Get-Url($u, $t = 8) {
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec $t -Headers @{ 'User-Agent' = 'LifeLog-NetDiag' }
    $sw.Stop(); return @{ ok = $true; body = ([string]$r.Content).Trim(); ms = $sw.ElapsedMilliseconds }
  } catch { $sw.Stop(); return @{ ok = $false; body = $_.Exception.Message; ms = $sw.ElapsedMilliseconds } }
}
Write-Output "=== net-diag-v1 on $env:COMPUTERNAME at $(Get-Date -Format o) (read-only) ==="

Sec "WAN IPv4 - what a browser on this LAN reports to an echo service"
$wan = $null
foreach ($u in @('https://api.ipify.org', 'https://ipv4.icanhazip.com', 'https://checkip.amazonaws.com')) {
  $g = Get-Url $u
  if ($g.ok) {
    Write-Output ("  {0} -> {1} ({2} ms)" -f $u, $g.body, $g.ms)
    if (-not $wan -and $g.body -match '^\d+\.\d+\.\d+\.\d+$') { $wan = $g.body }
  } else { Write-Output ("  {0} -> FAILED ({1} ms): {2}" -f $u, $g.ms, $g.body) }
}

Sec "WAN IPv6 (per-device address; only the prefix is shared by the LAN)"
$g6 = Get-Url 'https://api64.ipify.org'
if ($g6.ok) { Write-Output ("  api64.ipify.org -> " + $g6.body) } else { Write-Output ("  api64.ipify.org FAILED: " + $g6.body) }

Sec "ISP / geo hint for the WAN IPv4 (ipinfo.io, no token) + reverse DNS"
if ($wan) {
  $gi = Get-Url ("https://ipinfo.io/{0}/json" -f $wan)
  if ($gi.ok) {
    try { $j = $gi.body | ConvertFrom-Json; Write-Output ("  org={0} | city={1} region={2} | loc={3} | hostname={4}" -f $j.org, $j.city, $j.region, $j.loc, $j.hostname) }
    catch { Write-Output ("  raw: " + $gi.body) }
  } else { Write-Output ("  ipinfo FAILED: " + $gi.body) }
  try { $ptr = Resolve-DnsName -Name $wan -Type PTR -ErrorAction Stop | Select-Object -First 1 -ExpandProperty NameHost; Write-Output ("  PTR={0}" -f $ptr) }
  catch { Write-Output "  PTR: none" }
} else { Write-Output "  (no WAN IPv4 obtained)" }

Sec "First 3 hops toward 1.1.1.1 (a private/100.64.x SECOND hop = CGNAT or double NAT)"
try {
  $tr = & tracert -d -h 3 -w 700 1.1.1.1 2>&1 | Out-String
  ($tr -split "`r?`n" | Where-Object { $_ -match '^\s*\d+\s' }) | ForEach-Object { Write-Output ("  " + ($_.Trim() -replace '\s+', ' ')) }
} catch { Write-Output ("  tracert failed: " + $_.Exception.Message) }

Sec "LAN: default-route interfaces (more than one = dual-WAN / VPN)"
try {
  Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop | ForEach-Object {
    $ip4 = (Get-NetIPAddress -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty IPAddress)
    Write-Output ("  {0}: gw={1} metric={2} ip={3}" -f $_.InterfaceAlias, $_.NextHop, $_.RouteMetric, $ip4)
  }
} catch { Write-Output ("  Get-NetRoute failed: " + $_.Exception.Message) }

Sec "Wi-Fi: interface state / known profiles / visible BSSIDs with signal"
try {
  $wi = & netsh wlan show interfaces 2>&1 | Out-String
  ($wi -split "`r?`n" | Where-Object { $_ -match '^\s*(Name|State|SSID|BSSID|Signal|Band|Channel)\s*:' }) | ForEach-Object { Write-Output ("  " + ($_.Trim() -replace '\s+', ' ')) }
} catch { Write-Output "  netsh interfaces failed" }
try {
  $wp = & netsh wlan show profiles 2>&1 | Out-String
  $profs = @($wp -split "`r?`n" | Where-Object { $_ -match 'All User Profile\s*:\s*(.+)$' } | ForEach-Object { $Matches[1].Trim() })
  Write-Output ("  known profiles ({0}): {1}" -f $profs.Count, ($profs -join ', '))
} catch { Write-Output "  netsh profiles failed" }
try {
  $wn = & netsh wlan show networks mode=bssid 2>&1 | Out-String
  $cur = ''; $b = ''
  foreach ($ln in ($wn -split "`r?`n")) {
    if ($ln -match '^\s*SSID\s+\d+\s*:\s*(.*)$') { $cur = $Matches[1].Trim(); if (-not $cur) { $cur = '(hidden)' } }
    elseif ($ln -match '^\s*BSSID\s+\d+\s*:\s*([0-9a-fA-F:]+)') { $b = $Matches[1] }
    elseif ($ln -match '^\s*Signal\s*:\s*(\d+%)') { Write-Output ("  {0,-30} {1}  {2}" -f $cur, $b, $Matches[1]) }
  }
} catch { Write-Output "  netsh networks failed" }

Sec "Tailscale (an exit node in use would replace the WAN IP seen by echo services)"
$ts = 'C:\Program Files\Tailscale\tailscale.exe'
if (Test-Path $ts) {
  try {
    $raw = & $ts status --json 2>&1 | Out-String
    $st = $raw | ConvertFrom-Json
    $peerObjs = @()
    if ($st.Peer) { $peerObjs = @($st.Peer.PSObject.Properties | ForEach-Object { $_.Value }) }
    $exit = $peerObjs | Where-Object { $_.ExitNode -eq $true } | Select-Object -First 1
    Write-Output ("  self={0} tailscaleIPs={1} backend={2}" -f $st.Self.HostName, ($st.Self.TailscaleIPs -join ','), $st.BackendState)
    Write-Output ("  exit node in use: {0}" -f $(if ($exit) { $exit.HostName } else { 'none' }))
    $peers = $peerObjs | ForEach-Object { "{0}{1}{2}" -f $_.HostName, $(if ($_.Online) { '' } else { '(offline)' }), $(if ($_.ExitNodeOption) { '[exit-capable]' } else { '' }) }
    Write-Output ("  peers ({0}): {1}" -f $peers.Count, ($peers -join ', '))
  } catch { Write-Output ("  tailscale status failed: " + $_.Exception.Message) }
} else { Write-Output "  tailscale.exe not found" }

Write-Output ""
Write-Output "=== net-diag-v1 done (read-only, nothing modified) ==="
