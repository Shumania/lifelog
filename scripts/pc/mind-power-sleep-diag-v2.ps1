# mind-power-sleep-diag-v2 (2026-11-16 08:15 PT): READ-ONLY. Mind only. Follow-up to diag-v1: (a) why Mind resumed FROM HIBERNATION on Nov 10 16:02 and today 07:34 (power-button / battery / sleep settings + Kernel-Power sleep reasons), (b) crash-dump + auto-reboot config (why the 07:28 BSOD needed a force restart), (c) display driver, (d) 7-day logon history (did Shumadmin auto sign-in work after the Nov 10/12 boots?). Modifies nothing. Never reads a password. Andrew OK'd troubleshooting reads 07:53 PT.
$ErrorActionPreference = "Continue"
if ($env:COMPUTERNAME -ne "MIND") { exit 0 }   # SHUMAFRAME: silent no-op => service skips the webhook post
$sw = [Diagnostics.Stopwatch]::StartNew()
$BUDGET_S = 85
function Budget { param($label) if ($sw.Elapsed.TotalSeconds -gt $BUDGET_S) { [Console]::Out.WriteLine("  (skipped $label : time budget $BUDGET_S s exceeded at $([int]$sw.Elapsed.TotalSeconds) s)"); return $false }; return $true }
function Sec { param($t) Write-Output ""; Write-Output ("--- {0}  [t+{1}s] ---" -f $t, [int]$sw.Elapsed.TotalSeconds) }
function ED { param($e, $name) try { $x = [xml]$e.ToXml(); $d = $x.Event.EventData.Data | Where-Object { $_.Name -eq $name }; if ($d) { return [string]$d.'#text' } } catch {}; return "" }
function Trim1 { param($s, $n = 150) $s = ("$s" -replace "\s+", " ").Trim(); if ($s.Length -gt $n) { return $s.Substring(0, $n) + "..." }; return $s }
function LT { param($t) switch ("$t") { "2" {"2=Interactive"} "3" {"3=Network"} "4" {"4=Batch"} "5" {"5=Service"} "7" {"7=Unlock"} "10" {"10=RDP"} "11" {"11=Cached"} default {"$t"} } }
function ST { param($s) switch ("$s".ToUpper()) { "0XC000006D" {"bad-user-or-pw"} "0XC000006A" {"WRONG-PASSWORD"} "0XC0000234" {"LOCKED-OUT"} "0XC0000071" {"PW-EXPIRED"} "0X0" {"ok"} "" {""} default {"$s"} } }
function ActName { param($i) switch ("$i") { "0" {"Do nothing"} "1" {"Sleep"} "2" {"Hibernate"} "3" {"Shut down"} "4" {"Turn off display"} default {"$i"} } }
function Leaf { param($p) if ("$p") { try { return (Split-Path "$p" -Leaf) } catch { return "$p" } }; return "-" }

$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffK"
Write-Output "=== mind-power-sleep-diag-v2 on $env:COMPUTERNAME at $ts (read-only) ==="
try { $os = Get-CimInstance Win32_OperatingSystem; Write-Output ("  last OS boot: {0} | uptime {1} | RAM {2} GB" -f $os.LastBootUpTime.ToString("yyyy-MM-dd HH:mm:ss"), ((Get-Date) - $os.LastBootUpTime).ToString("d\.hh\:mm\:ss"), [math]::Round($os.TotalVisibleMemorySize/1MB, 1)) } catch {}

Sec "Battery + sleep states"
try { $b = Get-CimInstance Win32_Battery; if ($b) { $b | ForEach-Object { Write-Output ("  battery: {0} status={1} charge={2}% designVoltage={3}" -f $_.Name, $_.BatteryStatus, $_.EstimatedChargeRemaining, $_.DesignVoltage) } } else { Write-Output "  battery: none reported by Win32_Battery" } } catch { Write-Output "  (battery read failed: $($_.Exception.Message))" }
try { $pc = Get-CimInstance Win32_PortableBattery; if ($pc) { $pc | ForEach-Object { Write-Output ("  portable battery: {0} capacity={1} mWh chem={2}" -f $_.Name, $_.DesignCapacity, $_.Chemistry) } } } catch {}
try { Write-Output "  powercfg /a:"; & powercfg /a 2>&1 | ForEach-Object { $l = "$_".Trim(); if ($l) { Write-Output "    $l" } } | Select-Object -First 14 } catch {}
try { $h = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Power" -ErrorAction Stop); Write-Output ("  HibernateEnabled={0} HiberbootEnabled(fast startup)={1}" -f $h.HibernateEnabled, (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power" -ErrorAction SilentlyContinue).HiberbootEnabled) } catch {}

Sec "Active power scheme: button / lid / sleep / hibernate / battery actions (AC and DC)"
try {
  $q = & powercfg /q 2>&1
  $scheme = ($q | Select-String "Power Scheme GUID" | Select-Object -First 1).ToString().Trim(); Write-Output "  $scheme"
  $want = @("Power button action", "Sleep button action", "Lid close action", "Sleep after", "Hibernate after", "Allow hybrid sleep", "Allow wake timers", "Critical battery action", "Low battery action", "Critical battery level", "Low battery level", "Reserve battery level", "Turn off display after", "Allow standby states", "System unattended sleep timeout")
  for ($i = 0; $i -lt $q.Count; $i++) {
    $line = "$($q[$i])"
    foreach ($w in $want) {
      if ($line -match "\(\s*$([regex]::Escape($w))\s*\)") {
        $ac = ""; $dc = ""
        for ($j = $i + 1; $j -lt [math]::Min($q.Count, $i + 40); $j++) {
          $l2 = "$($q[$j])"
          if ($l2 -match "Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)") { $ac = [Convert]::ToInt64($matches[1], 16) }
          elseif ($l2 -match "Current DC Power Setting Index:\s*0x([0-9a-fA-F]+)") { $dc = [Convert]::ToInt64($matches[1], 16); break }
          elseif ($l2 -match "Power Setting GUID") { break }
        }
        if ($w -match "action") { Write-Output ("  {0,-34} AC={1} DC={2}" -f $w, (ActName $ac), (ActName $dc)) }
        else { Write-Output ("  {0,-34} AC={1} DC={2}  (seconds/percent; 0 = never)" -f $w, $ac, $dc) }
      }
    }
  }
} catch { Write-Output "  (powercfg /q parse failed: $($_.Exception.Message))" }

Sec "Sleep / hibernate / resume timeline (System log, last 14 days; Kernel-Power 42=entering sleep w/ reason, 107=resume, 109=shutdown-start, Kernel-Boot 27=boot type, Power-Troubleshooter 1=wake)"
if (Budget "sleep timeline") {
  try {
    $since = (Get-Date).AddDays(-14)
    $evs = @()
    $evs += Get-WinEvent -FilterHashtable @{LogName="System"; ProviderName="Microsoft-Windows-Kernel-Power"; Id=@(42,107,109,41); StartTime=$since} -MaxEvents 60 -ErrorAction SilentlyContinue
    $evs += Get-WinEvent -FilterHashtable @{LogName="System"; ProviderName="Microsoft-Windows-Kernel-Boot"; Id=27; StartTime=$since} -MaxEvents 30 -ErrorAction SilentlyContinue
    $evs += Get-WinEvent -FilterHashtable @{LogName="System"; ProviderName="Microsoft-Windows-Power-Troubleshooter"; Id=1; StartTime=$since} -MaxEvents 30 -ErrorAction SilentlyContinue
    $evs += Get-WinEvent -FilterHashtable @{LogName="System"; ProviderName="Microsoft-Windows-Kernel-General"; Id=@(12,13); StartTime=$since} -MaxEvents 30 -ErrorAction SilentlyContinue
    $evs += Get-WinEvent -FilterHashtable @{LogName="System"; ProviderName="User32"; Id=1074; StartTime=$since} -MaxEvents 20 -ErrorAction SilentlyContinue
    $evs += Get-WinEvent -FilterHashtable @{LogName="System"; ProviderName="EventLog"; Id=@(6005,6006,6008); StartTime=$since} -MaxEvents 30 -ErrorAction SilentlyContinue
    $evs = @($evs | Where-Object { $_ -ne $null })
    Write-Output ("  {0} events" -f $evs.Count)
    $evs | Sort-Object TimeCreated -Descending | Select-Object -First 70 | ForEach-Object {
      $e = $_; $t = $e.TimeCreated.ToString("MM-dd HH:mm:ss"); $m = Trim1 $e.Message 120
      switch ($e.Id) {
        42  { $m = "ENTERING-SLEEP reason=" + (Trim1 ((($e.Message -split "`n") | Where-Object { $_ -match "Reason" }) -join " ") 80) }
        107 { $m = "RESUMED-FROM-SLEEP" }
        109 { $m = "KERNEL-SHUTDOWN-START reason=" + (ED $e "Reason") }
        41  { $m = "KERNEL-POWER-41 unexpected: bugcheck=" + (ED $e "BugcheckCode") + " powerButton=" + (ED $e "PowerButtonTimestamp") }
        27  { $bt = ED $e "BootType"; if (-not $bt) { $bt = Trim1 $e.Message 40 }; $m = "BOOT-TYPE " + $bt + "  (0=cold, 1=fast-startup, 2=resume-from-hibernation)" }
        1   { $m = "WAKE: " + (Trim1 ((($e.Message -split "`n") | Where-Object { $_ -match "Sleep Time|Wake Time|Wake Source" }) -join " | ") 160) }
        12  { $m = "OS-STARTED" }
        13  { $m = "OS-SHUTTING-DOWN" }
        1074 { $m = "SHUTDOWN-INITIATED " + (Trim1 $e.Message 110) }
        6005 { $m = "eventlog-started" }
        6006 { $m = "eventlog-stopped (clean)" }
        6008 { $m = "UNEXPECTED-SHUTDOWN " + (Trim1 $e.Message 90) }
      }
      Write-Output ("  {0} {1,-5} {2}" -f $t, $e.Id, $m)
    }
  } catch { Write-Output "  (sleep timeline failed: $($_.Exception.Message))" }
}

Sec "Crash dump + auto-reboot config (why a BSOD might sit waiting for a manual restart)"
try {
  $cc = Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl" -ErrorAction Stop
  Write-Output ("  CrashDumpEnabled={0} (0=none 1=complete 2=kernel 3=small 7=automatic) AutoReboot={1} DumpFile={2} MinidumpDir={3} AlwaysKeepMemoryDump={4} DedicatedDumpFile={5}" -f $cc.CrashDumpEnabled, $cc.AutoReboot, $cc.DumpFile, $cc.MinidumpDir, $cc.AlwaysKeepMemoryDump, $cc.DedicatedDumpFile)
  $pf = Get-CimInstance Win32_PageFileUsage -ErrorAction SilentlyContinue; if ($pf) { $pf | ForEach-Object { Write-Output ("  pagefile: {0} allocated={1} MB current={2} MB peak={3} MB" -f $_.Name, $_.AllocatedBaseSize, $_.CurrentUsage, $_.PeakUsage) } } else { Write-Output "  pagefile: none reported" }
  $pfs = Get-CimInstance Win32_PageFileSetting -ErrorAction SilentlyContinue; if ($pfs) { $pfs | ForEach-Object { Write-Output ("  pagefile setting: {0} init={1} max={2} (0/0 = system managed)" -f $_.Name, $_.InitialSize, $_.MaximumSize) } } else { Write-Output "  pagefile setting: system-managed (no explicit entry)" }
  $cs = Get-CimInstance Win32_ComputerSystem; Write-Output ("  AutomaticManagedPagefile={0}" -f $cs.AutomaticManagedPagefile)
  foreach ($f in @("C:\WINDOWS\MEMORY.DMP")) { if (Test-Path $f) { $fi = Get-Item $f; Write-Output ("  {0}: {1} MB, written {2}" -f $f, [math]::Round($fi.Length/1MB), $fi.LastWriteTime) } else { Write-Output "  $f : absent" } }
  $md = "C:\WINDOWS\Minidump"; if (Test-Path $md) { $n = (Get-ChildItem $md -File -ErrorAction SilentlyContinue | Measure-Object).Count; Write-Output "  $md : $n file(s)"; Get-ChildItem $md -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 5 | ForEach-Object { Write-Output ("    {0} {1} KB {2}" -f $_.Name, [math]::Round($_.Length/1KB), $_.LastWriteTime) } } else { Write-Output "  $md : absent" }
  $free = (Get-PSDrive C -ErrorAction SilentlyContinue).Free; if ($free) { Write-Output ("  C: free = {0} GB" -f [math]::Round($free/1GB, 1)) }
} catch { Write-Output "  (crash config read failed: $($_.Exception.Message))" }

Sec "Display adapter driver (win32k crashes are usually graphics-driver adjacent)"
try { Get-CimInstance Win32_VideoController | ForEach-Object { $dd = "?"; if ($_.DriverDate) { $dd = $_.DriverDate.ToString("yyyy-MM-dd") }; Write-Output ("  {0} | driver {1} ({2}) | status={3} | {4}x{5}" -f $_.Name, $_.DriverVersion, $dd, $_.Status, $_.CurrentHorizontalResolution, $_.CurrentVerticalResolution) } } catch { Write-Output "  (video controller read failed: $($_.Exception.Message))" }
try { $gpuEv = Get-WinEvent -FilterHashtable @{LogName="System"; Id=@(4101,4102,4103,14,13); StartTime=(Get-Date).AddDays(-14)} -MaxEvents 10 -ErrorAction SilentlyContinue | Where-Object { $_.ProviderName -match "Display|dxgkrnl|igfx|Intel" }; if ($gpuEv) { $gpuEv | ForEach-Object { Write-Output ("  {0} {1} {2}: {3}" -f $_.TimeCreated.ToString("MM-dd HH:mm:ss"), $_.ProviderName, $_.Id, (Trim1 $_.Message 110)) } } else { Write-Output "  no display-driver reset/TDR events (4101/4102/4103) in 14 days" } } catch {}

Sec "Logon history, 7 days: did the automatic sign-in work after the Nov 10 / Nov 12 boots? (4624 interactive/unlock, 4625 failures, 4740 lockouts, 4767 unlocks; service/noise accounts filtered)"
if (Budget "logon history") {
  try {
    $since = (Get-Date).AddDays(-7)
    $ev = Get-WinEvent -FilterHashtable @{LogName="Security"; Id=@(4624,4625,4740,4767); StartTime=$since} -MaxEvents 400 -ErrorAction SilentlyContinue
    $rows = @()
    foreach ($e in $ev) {
      $id = $e.Id
      if ($id -eq 4624) {
        $u = ED $e "TargetUserName"; $lt = ED $e "LogonType"
        if ($u -match '^(DWM-|UMFD-|SYSTEM$|LOCAL SERVICE|NETWORK SERVICE|ANONYMOUS|MIND\$|WsiAccount|defaultuser)' ) { continue }
        if ("$lt" -notin @("2","7","10","11")) { continue }
        $rows += ("  {0} 4624 OK      user={1,-32} type={2,-14} proc={3}" -f $e.TimeCreated.ToString("MM-dd HH:mm:ss"), $u, (LT $lt), (Leaf (ED $e "ProcessName")))
      } elseif ($id -eq 4625) {
        $u = ED $e "TargetUserName"; $lt = ED $e "LogonType"; $sub = ED $e "SubStatus"; $st = ED $e "Status"
        $rows += ("  {0} 4625 FAIL    user={1,-32} type={2,-14} {3}/{4} proc={5} logonProc={6}" -f $e.TimeCreated.ToString("MM-dd HH:mm:ss"), $u, (LT $lt), (ST $st), (ST $sub), (Leaf (ED $e "ProcessName")), (ED $e "LogonProcessName"))
      } elseif ($id -eq 4740) {
        $rows += ("  {0} 4740 LOCKOUT user={1} caller={2}" -f $e.TimeCreated.ToString("MM-dd HH:mm:ss"), (ED $e "TargetUserName"), (ED $e "TargetDomainName"))
      } elseif ($id -eq 4767) {
        $rows += ("  {0} 4767 UNLOCK  user={1} by={2}" -f $e.TimeCreated.ToString("MM-dd HH:mm:ss"), (ED $e "TargetUserName"), (ED $e "SubjectUserName"))
      }
    }
    Write-Output ("  {0} relevant rows (of {1} fetched, cap 400) — newest first, max 60" -f $rows.Count, $ev.Count)
    $rows | Select-Object -First 60 | ForEach-Object { Write-Output $_ }
  } catch { Write-Output "  (logon history failed: $($_.Exception.Message))" }
}

Sec "ZoomUpdateTaskUser trigger (was 07:22:49 a logon or a daily timer?)"
try { $t = Get-ScheduledTask -TaskName "ZoomUpdateTaskUser-*" -ErrorAction Stop | Where-Object { $_.Principal.UserId -match "Shumadmin" } | Select-Object -First 1; if (-not $t) { throw "no ZoomUpdateTaskUser task for Shumadmin" }; $t.Triggers | ForEach-Object { Write-Output ("  trigger: {0} enabled={1} start={2} repeat={3}" -f $_.CimClass.CimClassName, $_.Enabled, $_.StartBoundary, $_.Repetition.Interval) } } catch { Write-Output "  (task read failed: $($_.Exception.Message))" }

Write-Output ""
Write-Output ("=== mind-power-sleep-diag-v2 done in {0}s ===" -f [int]$sw.Elapsed.TotalSeconds)
