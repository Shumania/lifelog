# tailscale-status-check-v1: verify how Tailscale revived + key expiry (2026-08-06)
$out = @()
$ts = "C:\Program Files\Tailscale\tailscale.exe"
$out += "== tailscale status =="
try { $out += (& $ts status 2>&1 | Out-String) } catch { $out += "status ERR: $_" }
$out += "== tailscale ip -4 =="
try { $out += (& $ts ip -4 2>&1 | Out-String) } catch { $out += "ip ERR: $_" }
$out += "== self key expiry =="
try {
  $j = (& $ts status --json 2>&1 | Out-String) | ConvertFrom-Json
  $out += ("BackendState: " + $j.BackendState)
  $out += ("Self: " + $j.Self.HostName + "  Online: " + $j.Self.Online + "  KeyExpiry: " + $j.Self.KeyExpiry)
} catch { $out += "json ERR: $_" }
$out += "== service =="
try { $out += (Get-Service Tailscale | Format-List Name,Status,StartType | Out-String) } catch { $out += "svc ERR: $_" }
$out -join "`n"
