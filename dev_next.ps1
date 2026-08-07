# tailscale-revive-shumaframe-v3: gated re-auth with reusable key (2026-08-06 17:17)
if ($env:COMPUTERNAME -ne 'SHUMAFRAME') { Write-Output "skip: not SHUMAFRAME ($env:COMPUTERNAME)"; exit 0 }
$ts = "C:\Program Files\Tailscale\tailscale.exe"
$out = "== tailscale up (re-auth v3) ==`n"
try {
  $out += (& $ts up --authkey=tskey-auth-kmcqoSQ7Xr11CNTRL-xM85UcSqKE1TgxJQjTzxD1Tti9h6D4oe --unattended 2>&1 | Out-String)
} catch { $out += "up ERROR: $_`n" }
Start-Sleep -Seconds 5
$out += "`n== status after ==`n"
try { $out += (& $ts status 2>&1 | Out-String) } catch { $out += "status ERROR: $_`n" }
$out += "`n== ip ==`n"
try { $out += (& $ts ip -4 2>&1 | Out-String) } catch { $out += "ip ERROR: $_`n" }
$out += "`n== key expiry ==`n"
try {
  $st = & $ts status --json 2>&1 | Out-String | ConvertFrom-Json
  $out += "BackendState: $($st.BackendState)`nSelf: $($st.Self.HostName)  Online: $($st.Self.Online)  KeyExpiry: $($st.Self.KeyExpiry)`n"
} catch { $out += "json ERROR: $_`n" }
$out
