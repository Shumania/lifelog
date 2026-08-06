# tailscale-revive-shumaframe-v1: re-auth Tailscale with fresh auth key (diag showed NeedsLogin, 2026-08-06)
if ($env:COMPUTERNAME -ne 'SHUMAFRAME') { exit 0 }  # wrong machine: silent no-op

Write-Output "COMPUTER: $env:COMPUTERNAME"
Write-Output "SCRIPT: tailscale-revive-shumaframe-v1"

$ts = 'C:\Program Files\Tailscale\tailscale.exe'
if (-not (Test-Path $ts)) { Write-Output "tailscale.exe NOT FOUND at $ts -- aborting"; Write-Output "== DONE tailscale-revive-shumaframe-v1 =="; exit 0 }

Write-Output "== 1. tailscale up --authkey (unattended) =="
try {
    $out = & $ts up --authkey=tskey-auth-kKnegNhHgt11CNTRL-jsC4Ficbfj68zLKVhGoQj6VKZPGN5pVDN --unattended 2>&1 | Out-String
    Write-Output $out
} catch {
    Write-Output "tailscale up threw: $_"
}

Start-Sleep -Seconds 8
Write-Output "== 2. status after up =="
try { Write-Output ((& $ts status 2>&1) | Out-String) } catch { Write-Output "status threw: $_" }
Write-Output "-- tailscale ip -4 --"
try { Write-Output ((& $ts ip -4 2>&1) | Out-String) } catch { Write-Output "ip threw: $_" }

Write-Output "== 3. powercfg: never sleep/hibernate on AC (headless box, pending fix) =="
try {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    Write-Output "powercfg applied: standby-timeout-ac=0, hibernate-timeout-ac=0"
} catch {
    Write-Output "powercfg FAILED: $_"
}

Write-Output "== DONE tailscale-revive-shumaframe-v1 =="
