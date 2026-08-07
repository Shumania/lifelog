# powercfg-nosleep-shumaframe-v1: disable AC sleep/hibernate so the box stays reachable (2026-08-06 17:26)
if ($env:COMPUTERNAME -ne 'SHUMAFRAME') { Write-Output "skip: not SHUMAFRAME ($env:COMPUTERNAME)"; exit 0 }
$out = "== powercfg apply ==`n"
try {
  powercfg /change standby-timeout-ac 0
  $out += "standby-timeout-ac -> 0 (never)`n"
  powercfg /change hibernate-timeout-ac 0
  $out += "hibernate-timeout-ac -> 0 (never)`n"
  powercfg /change monitor-timeout-ac 10
  $out += "monitor-timeout-ac -> 10 min (display can sleep; box cannot)`n"
} catch { $out += "powercfg ERROR: $_`n" }
$out += "`n== verify (active scheme sleep settings) ==`n"
try {
  $q = powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 2>&1 | Out-String
  $out += ($q -split "`n" | Where-Object { $_ -match 'Power Setting|Current AC' }) -join "`n"
} catch { $out += "query ERROR: $_`n" }
$out
