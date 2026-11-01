# sonos-register-v1: WRITES ONE REGISTRY KEY. Maps sonos:// -> Sonos.exe (launch/raise only, no URL argument) so a browser click can open the Sonos desktop app. Reversible: sonos-unregister-v1.
$ErrorActionPreference = "Continue"
$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffffffK"
Write-Output "=== sonos-register-v1 on $env:COMPUTERNAME at $ts ==="
Write-Output "  running as: $(whoami)"

# --- 1. Locate Sonos.exe: running process first (authoritative), then known install folders ---
$exe = $null
$p = Get-Process -Name Sonos -ErrorAction SilentlyContinue | Where-Object { $_.Path } | Select-Object -First 1
if ($p) { $exe = $p.Path; Write-Output "  Sonos.exe (running process pid=$($p.Id)): $exe" }
if (-not $exe) {
  foreach ($cand in @("C:\Program Files (x86)\SonosV2\Sonos.exe","C:\Program Files\SonosV2\Sonos.exe","C:\Program Files (x86)\Sonos\Sonos.exe","C:\Program Files\Sonos\Sonos.exe")) {
    if (Test-Path $cand) { $exe = $cand; Write-Output "  Sonos.exe (on disk): $exe"; break }
  }
}
if (-not $exe) { Write-Output "  ABORT: Sonos.exe not found. Nothing written."; Write-Output "=== sonos-register-v1 done (NO CHANGE) ==="; exit 0 }

# --- 2. Pick the hive: machine-wide (HKLM) if we may write it, else per-user (HKCU) ---
$hklm = "Registry::HKEY_LOCAL_MACHINE\Software\Classes\sonos"
$hkcu = "Registry::HKEY_CURRENT_USER\Software\Classes\sonos"
$target = $null
try {
  $probe = "Registry::HKEY_LOCAL_MACHINE\Software\Classes\sonos-probe-tmp"
  New-Item -Path $probe -Force -ErrorAction Stop | Out-Null
  Remove-Item -Path $probe -Recurse -Force -ErrorAction Stop
  $target = $hklm
  Write-Output "  hive: HKLM (machine-wide; applies to every Windows user on this PC)"
} catch {
  $target = $hkcu
  Write-Output "  hive: HKCU (per-user; HKLM not writable: $($_.Exception.Message))"
  Write-Output "  NOTE: if 'running as' above is not the account you browse with, this entry will not apply to your browser."
}

# --- 3. Pre-state ---
if (Test-Path $target) {
  $old = (Get-Item (Join-Path $target "shell\open\command") -ErrorAction SilentlyContinue)
  $oldCmd = if ($old) { $old.GetValue('') } else { '(no open command)' }
  Write-Output "  pre-state: key EXISTS; open command = $oldCmd"
} else {
  Write-Output "  pre-state: key absent"
}

# --- 4. Write: URL:Sonos Protocol, URL Protocol flag, icon, open command (no %1 -> launch/raise only) ---
$cmd = '"' + $exe + '"'
try {
  New-Item -Path $target -Force -ErrorAction Stop | Out-Null
  Set-ItemProperty -Path $target -Name "(Default)" -Value "URL:Sonos Protocol" -ErrorAction Stop
  Set-ItemProperty -Path $target -Name "URL Protocol" -Value "" -ErrorAction Stop
  New-Item -Path (Join-Path $target "DefaultIcon") -Force -ErrorAction Stop | Out-Null
  Set-ItemProperty -Path (Join-Path $target "DefaultIcon") -Name "(Default)" -Value ($exe + ",0") -ErrorAction Stop
  New-Item -Path (Join-Path $target "shell\open\command") -Force -ErrorAction Stop | Out-Null
  Set-ItemProperty -Path (Join-Path $target "shell\open\command") -Name "(Default)" -Value $cmd -ErrorAction Stop
  Write-Output "  wrote: $($target.Replace('Registry::','')) -> $cmd"
} catch {
  Write-Output "  WRITE FAILED: $($_.Exception.Message)"
  Write-Output "=== sonos-register-v1 done (FAILED) ==="
  exit 0
}

# --- 5. Read-back verification through HKCR (what the browser actually resolves) ---
$hkcr = "Registry::HKEY_CLASSES_ROOT\sonos"
if (Test-Path $hkcr) {
  $k = Get-Item $hkcr
  $flag = if ($null -ne $k.GetValue("URL Protocol")) { "yes" } else { "MISSING" }
  $rb = (Get-Item (Join-Path $hkcr "shell\open\command")).GetValue("")
  $ok = if ($rb -eq $cmd -and $flag -eq "yes") { "OK" } else { "MISMATCH" }
  Write-Output "  read-back HKCR\sonos: URL Protocol=$flag | open=$rb | $ok"
} else {
  Write-Output "  read-back HKCR\sonos: ABSENT (write did not take)"
}
Write-Output "  rollback: reg delete `"$($target.Replace('Registry::',''))`" /f"
Write-Output "=== sonos-register-v1 done ==="
