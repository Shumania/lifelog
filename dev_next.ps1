# mind-restart-lockout-diag-v1 (2026-11-16 07:55 PT): READ-ONLY. Mind only. Why did Mind need a force restart at ~07:31 PT, and what locked out Shumadmin? Reads event logs, local accounts, auto-logon flags (never the password), tasks/services. Modifies nothing. Andrew OK'd 07:53 PT.
$ErrorActionPreference = "Continue"
if ($env:COMPUTERNAME -ne "MIND") { exit 0 }   # SHUMAFRAME: silent no-op => service skips the webhook post
$sw = [Diagnostics.Stopwatch]::StartNew()
$BUDGET_S = 85   # service kills the script at 120 s and would re-run it every poll; stay well under
function Budget { param($label) if ($sw.Elapsed.TotalSeconds -gt $BUDGET_S) { [Console]::Out.WriteLine("  (skipped $label : time budget $BUDGET_S s exceeded at $([int]$sw.Elapsed.TotalSeconds) s)"); return $false }; return $true }
function Sec { param($t) Write-Output ""; Write-Output ("--- {0}  [t+{1}s] ---" -f $t, [int]$sw.Elapsed.TotalSeconds) }
function ED { param($e, $name) try { $x = [xml]$e.ToXml(); $d = $x.Event.EventData.Data | Where-Object { $_.Name -eq $name }; if ($d) { return [string]$d.'#text' } } catch {}; return "" }
function LT { param($t) switch ("$t") { "2" {"2=Interactive"} "3" {"3=Network"} "4" {"4=Batch/task"} "5" {"5=Service"} "7" {"7=Unlock"} "8" {"8=NetClear"} "9" {"9=NewCred"} "10" {"10=RDP"} "11" {"11=CachedInteractive"} default {"$t"} } }
function ST { param($s) switch ("$s".ToUpper()) { "0XC000006D" {"bad-user-or-pw"} "0XC000006A" {"WRONG-PASSWORD"} "0XC0000234" {"LOCKED-OUT"} "0XC0000071" {"PASSWORD-EXPIRED"} "0XC0000072" {"disabled"} "0XC0000064" {"no-such-user"} "0XC000006E" {"account-restriction"} "0XC0000193" {"account-expired"} "0XC0000224" {"must-change-pw"} "0XC000015B" {"logon-type-not-granted"} "0X0" {"ok"} "" {""} default {"$s"} } }
function Trim1 { param($s, $n = 150) $s = ("$s" -replace "\s+", " ").Trim(); if ($s.Length -gt $n) { return $s.Substring(0, $n) + "..." }; return $s }

$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffK"
Write-Output "=== mind-restart-lockout-diag-v1 on $env:COMPUTERNAME at $ts (read-only) ==="
try { Write-Output ("  running as: {0}" -f [Security.Principal.WindowsIdentity]::GetCurrent().Name) } catch {}

Sec "Machine / boot"
try {
  $os = Get-CimInstance Win32_OperatingSystem
  $cs = Get-CimInstance Win32_ComputerSystem
  $bios = Get-CimInstance Win32_BIOS
  $lastBoot = $os.LastBootUpTime
  Write-Output ("  OS: {0} build {1} | last OS boot: {2} (local) = {3}Z | uptime: {4}" -f $os.Caption, $os.BuildNumber, $lastBoot.ToString("yyyy-MM-dd HH:mm:ss"), $lastBoot.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss"), ((Get-Date) - $lastBoot).ToString("d\.hh\:mm\:ss"))
  Write-Output ("  hardware: {0} {1} | BIOS {2} ({3})" -f $cs.Manufacturer, $cs.Model, $bios.SMBIOSBIOSVersion, $bios.ReleaseDate)
} catch { Write-Output "  (machine info failed: $($_.Exception.Message))"; $lastBoot = (Get-Date).AddHours(-1) }
try { $lw = (& powercfg /lastwake 2>&1 | Select-Object -First 6) -join " / "; Write-Output ("  powercfg /lastwake: {0}" -f (Trim1 $lw 300)) } catch {}

Sec "Local accounts (Get-LocalUser + ADSI lockout state)"
try {
  Get-LocalUser | ForEach-Object {
    Write-Output ("  {0,-16} enabled={1,-5} src={2,-16} pwLastSet={3} pwExpires={4} lastLogon={5} pwRequired={6}" -f $_.Name, $_.Enabled, $_.PrincipalSource, $_.PasswordLastSet, $_.PasswordExpires, $_.LastLogon, $_.PasswordRequired)
  }
} catch { Write-Output "  (Get-LocalUser failed: $($_.Exception.Message))" }
foreach ($n in @("Shumadmin", "andre")) {
  try {
    $u = [ADSI]"WinNT://$env:COMPUTERNAME/$n,user"
    $locked = try { $u.InvokeGet("IsAccountLocked") } catch { "n/a" }
    $pwAgeDays = try { [math]::Round(([int]$u.PasswordAge.Value) / 86400, 1) } catch { "n/a" }
    $maxAgeDays = try { [math]::Round(([int]$u.MaxPasswordAge.Value) / 86400, 1) } catch { "n/a" }
    $lastLogin = try { $u.LastLogin.Value } catch { "never/n-a" }
    Write-Output ("  ADSI {0}: lockedOutNow={1} badPwAttempts={2} passwordExpired={3} pwAgeDays={4} maxPwAgeDays={5} lastLogin={6}" -f $n, $locked, $u.BadPasswordAttempts.Value, $u.PasswordExpired.Value, $pwAgeDays, $maxAgeDays, $lastLogin)
  } catch { Write-Output "  ADSI $n : $($_.Exception.Message)" }
}
try { $adm = (Get-LocalGroupMember -Group Administrators -ErrorAction Stop | ForEach-Object { $_.Name }) -join ", "; Write-Output "  Administrators group: $adm" } catch { Write-Output "  (Administrators group read failed: $($_.Exception.Message))" }

Sec "Lockout / password policy (net accounts)"
try { & net accounts 2>&1 | Where-Object { $_ -match "Lockout|Maximum password age|Minimum password age" } | ForEach-Object { Write-Output "  $_" } } catch {}

Sec "Auto-logon configuration (flags + username only; the password value is never read)"
try {
  $wl = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -ErrorAction Stop
  $hasPw = ($null -ne $wl.PSObject.Properties["DefaultPassword"])
  Write-Output ("  Winlogon: AutoAdminLogon={0} DefaultUserName={1} DefaultDomainName={2} ForceAutoLogon={3} AutoLogonCount={4} AutoLogonSID={5} DefaultPassword-value-present={6}" -f $wl.AutoAdminLogon, $wl.DefaultUserName, $wl.DefaultDomainName, $wl.ForceAutoLogon, $wl.AutoLogonCount, $wl.AutoLogonSID, $hasPw)
} catch { Write-Output "  (Winlogon read failed: $($_.Exception.Message))" }
try { $lsa = Test-Path "HKLM:\SECURITY\Policy\Secrets\DefaultPassword" -ErrorAction Stop; Write-Output "  LSA secret 'DefaultPassword' (Sysinternals Autologon / netplwiz style) present: $lsa  [false can also mean unreadable]" } catch { Write-Output "  (LSA secret check failed: $($_.Exception.Message))" }
try {
  $sidMap = @{}; Get-LocalUser | ForEach-Object { $sidMap[$_.SID.Value] = $_.Name }
  $arso = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\UserARSO"
  if (Test-Path $arso) { Get-ChildItem $arso | ForEach-Object { $sid = $_.PSChildName; $opt = (Get-ItemProperty $_.PSPath).OptOut; $who = $sidMap[$sid]; Write-Output ("  ARSO (auto-finish-setup-after-restart): {0} ({1}) OptOut={2}  [OptOut=1 means OFF]" -f $sid, $who, $opt) } } else { Write-Output "  ARSO: no UserARSO key" }
  $pol = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -ErrorAction SilentlyContinue
  Write-Output ("  Policy DisableAutomaticRestartSignOn={0} | dontdisplaylastusername={1}" -f $pol.DisableAutomaticRestartSignOn, $pol.dontdisplaylastusername)
  $pl = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device" -ErrorAction SilentlyContinue
  Write-Output ("  Passwordless device build flag (2 = Hello-only, hides the netplwiz auto-login box): {0}" -f $pl.DevicePasswordLessBuildVersion)
  $rdp = Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server" -ErrorAction SilentlyContinue
  Write-Output ("  RDP fDenyTSConnections={0} (0 = Remote Desktop enabled)" -f $rdp.fDenyTSConnections)
} catch { Write-Output "  (auto-logon extras failed: $($_.Exception.Message))" }

Sec "Scheduled tasks / services that run as a user account (stale stored passwords lock accounts at boot)"
try {
  $tasks = Get-ScheduledTask -ErrorAction Stop
  $userTasks = $tasks | Where-Object { $u = "$($_.Principal.UserId)"; $u -and ($u -notmatch '^(SYSTEM|LOCAL SERVICE|NETWORK SERVICE|S-1-5-18|S-1-5-19|S-1-5-20|Users|Administrators|Everyone|INTERACTIVE|Authenticated Users|.*\$)$') -and ($u -notmatch '^NT AUTHORITY') }
  foreach ($t in $userTasks) {
    $i = $null; try { $i = $t | Get-ScheduledTaskInfo -ErrorAction Stop } catch {}
    Write-Output ("  TASK {0}{1} | user={2} logonType={3} state={4} lastRun={5} lastResult={6}" -f $t.TaskPath, $t.TaskName, $t.Principal.UserId, $t.Principal.LogonType, $t.State, $i.LastRunTime, $i.LastTaskResult)
  }
  if (-not $userTasks) { Write-Output "  (no scheduled tasks run as a named user account)" }
  $ll = $tasks | Where-Object { $_.TaskName -match 'lifelog' -or ($_.Actions | Where-Object { "$($_.Execute) $($_.Arguments)" -match 'lifelog|service_v' }) }
  foreach ($t in $ll) { $i = $null; try { $i = $t | Get-ScheduledTaskInfo -ErrorAction Stop } catch {}; Write-Output ("  LIFELOG TASK {0}{1} | user={2} logonType={3} runLevel={4} state={5} lastRun={6} lastResult={7}" -f $t.TaskPath, $t.TaskName, $t.Principal.UserId, $t.Principal.LogonType, $t.Principal.RunLevel, $t.State, $i.LastRunTime, $i.LastTaskResult) }
} catch { Write-Output "  (task scan failed: $($_.Exception.Message))" }
try {
  $svcs = Get-CimInstance Win32_Service | Where-Object { $_.StartName -and $_.StartName -notmatch '^(LocalSystem|NT AUTHORITY|NT SERVICE)' }
  foreach ($s in $svcs) { Write-Output ("  SERVICE {0} | runs as {1} | start={2} state={3}" -f $s.Name, $s.StartName, $s.StartMode, $s.State) }
  if (-not $svcs) { Write-Output "  (no services run as a named user account)" }
} catch { Write-Output "  (service scan failed: $($_.Exception.Message))" }

Sec "Security log: lockouts, failed logons, unlocks, password resets (last 3 days)"
if (Budget "security-log") {
  $since = (Get-Date).AddDays(-3)
  $ev = @(Get-WinEvent -FilterHashtable @{ LogName = 'Security'; Id = @(4740, 4625, 4776, 4767, 4724, 4723, 4738, 4722, 4725, 4726, 4720); StartTime = $since } -MaxEvents 120 -ErrorAction SilentlyContinue)
  Write-Output ("  fetched {0} events (cap 120, newest first)" -f $ev.Count)
  $counts = $ev | Group-Object Id | ForEach-Object { "$($_.Name)x$($_.Count)" }; Write-Output ("  by id: {0}" -f ($counts -join " "))
  $shown = 0
  foreach ($e in $ev) {
    if ($shown -ge 45) { Write-Output "  ... (more omitted)"; break }
    $t = $e.TimeCreated.ToString("MM-dd HH:mm:ss")
    switch ($e.Id) {
      4740 { Write-Output ("  {0} 4740 LOCKED-OUT user={1} callerComputer={2}" -f $t, (ED $e 'TargetUserName'), (ED $e 'TargetDomainName')) }
      4625 { Write-Output ("  {0} 4625 FAILED-LOGON user={1}\{2} type={3} status={4}/{5} proc={6} ws={7} ip={8} logonProc={9} authPkg={10}" -f $t, (ED $e 'TargetDomainName'), (ED $e 'TargetUserName'), (LT (ED $e 'LogonType')), (ST (ED $e 'Status')), (ST (ED $e 'SubStatus')), (ED $e 'ProcessName'), (ED $e 'WorkstationName'), (ED $e 'IpAddress'), (ED $e 'LogonProcessName'), (ED $e 'AuthenticationPackageName')) }
      4776 { Write-Output ("  {0} 4776 CRED-VALIDATION user={1} ws={2} status={3}" -f $t, (ED $e 'TargetUserName'), (ED $e 'Workstation'), (ST (ED $e 'Status'))) }
      4767 { Write-Output ("  {0} 4767 UNLOCKED user={1} by={2}" -f $t, (ED $e 'TargetUserName'), (ED $e 'SubjectUserName')) }
      4724 { Write-Output ("  {0} 4724 PASSWORD-RESET user={1} by={2}" -f $t, (ED $e 'TargetUserName'), (ED $e 'SubjectUserName')) }
      4723 { Write-Output ("  {0} 4723 PASSWORD-CHANGE user={1} by={2}" -f $t, (ED $e 'TargetUserName'), (ED $e 'SubjectUserName')) }
      4738 { Write-Output ("  {0} 4738 ACCOUNT-CHANGED user={1} by={2} pwLastSet={3} uac={4} expires={5}" -f $t, (ED $e 'TargetUserName'), (ED $e 'SubjectUserName'), (ED $e 'PasswordLastSet'), (Trim1 (ED $e 'UserAccountControl') 80), (ED $e 'AccountExpires')) }
      default { Write-Output ("  {0} {1} user={2} by={3}" -f $t, $e.Id, (ED $e 'TargetUserName'), (ED $e 'SubjectUserName')) }
    }
    $shown++
  }
  if ($ev.Count -eq 0) { Write-Output "  (none in window)" }
}

Sec "Security log: successful interactive/unlock/RDP logons (last 3 days, max 15)"
if (Budget "logons-ok") {
  $xp = "*[System[EventID=4624 and TimeCreated[timediff(@SystemTime) <= 259200000]] and EventData[Data[@Name='LogonType']='2' or Data[@Name='LogonType']='7' or Data[@Name='LogonType']='10' or Data[@Name='LogonType']='11']]"
  $ok = @(Get-WinEvent -LogName Security -FilterXPath $xp -MaxEvents 15 -ErrorAction SilentlyContinue)
  foreach ($e in $ok) { Write-Output ("  {0} 4624 OK user={1}\{2} type={3} proc={4} ip={5} elevated={6}" -f $e.TimeCreated.ToString("MM-dd HH:mm:ss"), (ED $e 'TargetDomainName'), (ED $e 'TargetUserName'), (LT (ED $e 'LogonType')), (ED $e 'ProcessName'), (ED $e 'IpAddress'), (ED $e 'ElevatedToken')) }
  if ($ok.Count -eq 0) { Write-Output "  (none)" }
}

Sec "Security log: sign-out / lock / unlock session events (last 3 days, max 12)"
if (Budget "session-events") {
  $se = @(Get-WinEvent -FilterHashtable @{ LogName = 'Security'; Id = @(4647, 4800, 4801, 4802, 4803); StartTime = (Get-Date).AddDays(-3) } -MaxEvents 12 -ErrorAction SilentlyContinue)
  foreach ($e in $se) { $what = switch ($e.Id) { 4647 {"SIGN-OUT"} 4800 {"LOCKED"} 4801 {"UNLOCKED"} 4802 {"screensaver-on"} 4803 {"screensaver-off"} }; Write-Output ("  {0} {1} {2} user={3}" -f $e.TimeCreated.ToString("MM-dd HH:mm:ss"), $e.Id, $what, (ED $e 'TargetUserName')) }
  if ($se.Count -eq 0) { Write-Output "  (none)" }
}

Sec "System log: boot / shutdown / crash markers (last 14 days)"
if (Budget "boot-events") {
  $be = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = @(41, 1074, 6005, 6006, 6008, 1001, 27, 12, 13); StartTime = (Get-Date).AddDays(-14) } -MaxEvents 80 -ErrorAction SilentlyContinue)
  $be = $be | Where-Object { $_.Id -notin @(27, 12, 13) -or $_.ProviderName -match 'Kernel-Boot|Kernel-General' }
  $shown = 0
  foreach ($e in $be) {
    if ($shown -ge 40) { Write-Output "  ... (more omitted)"; break }
    $t = $e.TimeCreated.ToString("MM-dd HH:mm:ss")
    switch ($e.Id) {
      41   { Write-Output ("  {0} 41 KERNEL-POWER unexpected restart: BugcheckCode={1} PowerButtonTimestamp={2} (non-zero = power button held) BootAppStatus={3} Checkpoint={4}" -f $t, (ED $e 'BugcheckCode'), (ED $e 'PowerButtonTimestamp'), (ED $e 'BootAppStatus'), (ED $e 'Checkpoint')) }
      1074 { Write-Output ("  {0} 1074 SHUTDOWN-INITIATED: {1}" -f $t, (Trim1 $e.Message 220)) }
      6008 { Write-Output ("  {0} 6008 UNEXPECTED-SHUTDOWN: {1}" -f $t, (Trim1 $e.Message 120)) }
      1001 { Write-Output ("  {0} 1001 {1}: {2}" -f $t, $e.ProviderName, (Trim1 $e.Message 200)) }
      6005 { Write-Output ("  {0} 6005 event-log-started (boot)" -f $t) }
      6006 { Write-Output ("  {0} 6006 event-log-stopped (clean shutdown)" -f $t) }
      27   { Write-Output ("  {0} 27 Kernel-Boot: {1}" -f $t, (Trim1 $e.Message 120)) }
      12   { Write-Output ("  {0} 12 Kernel-General OS-started: {1}" -f $t, (Trim1 $e.Message 100)) }
      13   { Write-Output ("  {0} 13 Kernel-General OS-shutting-down" -f $t) }
    }
    $shown++
  }
  if ($be.Count -eq 0) { Write-Output "  (none)" }
}

Sec "System log: Critical/Error in the 3 hours before the last boot (what was going wrong when it hung?)"
if (Budget "pre-boot-errors") {
  $pe = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; Level = @(1, 2); StartTime = $lastBoot.AddHours(-3); EndTime = $lastBoot.AddSeconds(-1) } -MaxEvents 40 -ErrorAction SilentlyContinue)
  $g = $pe | Group-Object ProviderName, Id | Sort-Object Count -Descending
  foreach ($grp in $g) { $f = $grp.Group[0]; Write-Output ("  x{0} {1} id={2} level={3} last={4}: {5}" -f $grp.Count, $f.ProviderName, $f.Id, $f.LevelDisplayName, $f.TimeCreated.ToString("MM-dd HH:mm:ss"), (Trim1 $f.Message 160)) }
  if ($pe.Count -eq 0) { Write-Output "  (no Critical/Error System events in that window)" }
}

Sec "System log: Critical/Error since the last boot (max 25, grouped)"
if (Budget "post-boot-errors") {
  $po = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; Level = @(1, 2); StartTime = $lastBoot } -MaxEvents 25 -ErrorAction SilentlyContinue)
  $g = $po | Group-Object ProviderName, Id | Sort-Object Count -Descending
  foreach ($grp in $g) { $f = $grp.Group[0]; Write-Output ("  x{0} {1} id={2} last={3}: {4}" -f $grp.Count, $f.ProviderName, $f.Id, $f.TimeCreated.ToString("MM-dd HH:mm:ss"), (Trim1 $f.Message 140)) }
  if ($po.Count -eq 0) { Write-Output "  (none)" }
}

Sec "Hardware errors (WHEA), disk, display driver resets (last 14 days, max 15)"
if (Budget "hw-events") {
  $hw = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = @('Microsoft-Windows-WHEA-Logger', 'disk', 'Disk', 'nvlddmkm', 'amdkmdag', 'Display', 'volmgr', 'Ntfs', 'Microsoft-Windows-Kernel-Processor-Power'); StartTime = (Get-Date).AddDays(-14) } -MaxEvents 15 -ErrorAction SilentlyContinue)
  $hw = $hw | Where-Object { $_.Level -le 3 }
  foreach ($e in $hw) { Write-Output ("  {0} {1} id={2} {3}: {4}" -f $e.TimeCreated.ToString("MM-dd HH:mm:ss"), $e.ProviderName, $e.Id, $e.LevelDisplayName, (Trim1 $e.Message 140)) }
  if (-not $hw) { Write-Output "  (none at Warning or above)" }
}

Sec "Windows Update installs/restarts (last 14 days, max 10) + application crashes of python (max 6)"
if (Budget "wu-events") {
  $wu = @(Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-WindowsUpdateClient/Operational'; Id = @(19, 20, 43, 44); StartTime = (Get-Date).AddDays(-14) } -MaxEvents 10 -ErrorAction SilentlyContinue)
  foreach ($e in $wu) { Write-Output ("  {0} WU {1}: {2}" -f $e.TimeCreated.ToString("MM-dd HH:mm:ss"), $e.Id, (Trim1 $e.Message 140)) }
  if ($wu.Count -eq 0) { Write-Output "  (no WU install/restart events)" }
  $ap = @(Get-WinEvent -FilterHashtable @{ LogName = 'Application'; Id = @(1000, 1001, 1002); StartTime = (Get-Date).AddDays(-14) } -MaxEvents 40 -ErrorAction SilentlyContinue) | Where-Object { $_.Message -match 'python' } | Select-Object -First 6
  foreach ($e in $ap) { Write-Output ("  {0} APP {1} {2}: {3}" -f $e.TimeCreated.ToString("MM-dd HH:mm:ss"), $e.ProviderName, $e.Id, (Trim1 $e.Message 160)) }
  if (-not $ap) { Write-Output "  (no python crash/hang records in Application log)" }
}

Write-Output ""
Write-Output ("=== mind-restart-lockout-diag-v1 done in {0}s ===" -f [int]$sw.Elapsed.TotalSeconds)
