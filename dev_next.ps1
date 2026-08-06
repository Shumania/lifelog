# tailscale-diag-shumaframe-v1: diagnose + revive Tailscale (RDP unreachable 2026-08-06)
if ($env:COMPUTERNAME -ne 'SHUMAFRAME') { exit 0 }  # wrong machine: silent no-op (empty output = no webhook post)

Write-Output "COMPUTER: $env:COMPUTERNAME"
Write-Output "SCRIPT: tailscale-diag-shumaframe-v1"

Write-Output "== 1. Windows service state =="
try {
    $svc = Get-Service -Name 'Tailscale' -ErrorAction Stop
    Write-Output "Service 'Tailscale': Status=$($svc.Status) StartType=$($svc.StartType)"
} catch {
    Write-Output "Get-Service Tailscale FAILED: $_"
    Write-Output "-- listing any service matching *tail* --"
    Get-Service | Where-Object { $_.Name -like '*tail*' -or $_.DisplayName -like '*tail*' } | ForEach-Object { Write-Output "  $($_.Name) [$($_.DisplayName)] = $($_.Status) / $($_.StartType)" }
    $svc = $null
}

if ($svc -and $svc.Status -ne 'Running') {
    Write-Output "== 2. Service not running -> attempting Start-Service =="
    try {
        Start-Service -Name 'Tailscale' -ErrorAction Stop
        Start-Sleep -Seconds 5
        $svc = Get-Service -Name 'Tailscale'
        Write-Output "After start attempt: Status=$($svc.Status)"
    } catch {
        Write-Output "Start-Service FAILED: $_"
    }
} else {
    Write-Output "== 2. Service already running (or missing) -> no start attempt =="
}

Write-Output "== 3. tailscale CLI status =="
$ts = 'C:\Program Files\Tailscale\tailscale.exe'
if (-not (Test-Path $ts)) {
    Write-Output "tailscale.exe NOT FOUND at $ts -- searching..."
    $found = Get-ChildItem 'C:\Program Files*' -Recurse -Filter 'tailscale.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { $ts = $found.FullName; Write-Output "found: $ts" } else { Write-Output "tailscale.exe not found anywhere under Program Files"; $ts = $null }
}
if ($ts) {
    try {
        $status = & $ts status 2>&1 | Out-String
        Write-Output "-- tailscale status --"
        Write-Output $status
        $ip = & $ts ip -4 2>&1 | Out-String
        Write-Output "-- tailscale ip -4 --"
        Write-Output $ip
    } catch {
        Write-Output "tailscale CLI FAILED: $_"
    }
}

Write-Output "== 4. GUI tray process check =="
$gui = Get-Process -Name 'tailscale-ipn' -ErrorAction SilentlyContinue
if ($gui) { Write-Output "tailscale-ipn (tray GUI) running, PID $($gui.Id)" } else { Write-Output "tailscale-ipn (tray GUI) NOT running (normal for headless; service matters, not tray)" }

Write-Output "== 5. RDP service check (bonus) =="
try {
    $rdp = Get-Service -Name 'TermService'
    Write-Output "TermService (RDP): $($rdp.Status)"
    $fDeny = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections).fDenyTSConnections
    Write-Output "fDenyTSConnections = $fDeny (0 = RDP allowed)"
} catch {
    Write-Output "RDP check FAILED: $_"
}

Write-Output "== DONE tailscale-diag-shumaframe-v1 =="
