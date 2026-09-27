# dev_next: 2026-09-27 geo-diag-v1 - read-only Windows Location Platform probe (why did the UX pick Roaming on reboot?)
$ErrorActionPreference = "Continue"
Write-Output ("=== geo-diag-v1 on {0} as {1}\{2} at {3} ===" -f $env:COMPUTERNAME, $env:USERDOMAIN, $env:USERNAME, (Get-Date -Format o))

# --- zones (geo_zones.json v5) + the page's _houseDecide verdict, reproduced -----------------
function DistM($lat1, $lon1, $lat2, $lon2) {
  $R = 6371000.0; $toRad = [Math]::PI / 180.0
  $dLat = ($lat2 - $lat1) * $toRad; $dLon = ($lon2 - $lon1) * $toRad
  $a = [Math]::Sin($dLat / 2) * [Math]::Sin($dLat / 2) + [Math]::Cos($lat1 * $toRad) * [Math]::Cos($lat2 * $toRad) * [Math]::Sin($dLon / 2) * [Math]::Sin($dLon / 2)
  return 2 * $R * [Math]::Asin([Math]::Sqrt($a))
}
function Verdict($lat, $lon, $acc) {
  $dV = DistM $lat $lon 47.365742 (-122.4681911)
  $dC = DistM $lat $lon 47.62734 (-122.30392)
  $line = ("  dist->Vashon centroid={0:F0}m  dist->Cap Hill={1:F0}m  acc={2:F0}m" -f $dV, $dC, $acc)
  if ($acc -gt 300) { return $line + "  => page verdict: NO DECISION (acc > 300m gate; view unchanged)" }
  $inV = ($dV - $acc) -le 250; $inC = ($dC - $acc) -le 200
  if ($inV -and $inC) { return $line + "  => page verdict: NO DECISION (both zones?!)" }
  if ($inC) { return $line + "  => page verdict: caphill" }
  if ($inV) { return $line + "  => page verdict: vashon" }
  return $line + "  => page verdict: ROAMING"
}

# --- 0. machine context ------------------------------------------------------------------------
Write-Output "--- machine ---"
try { $os = Get-CimInstance Win32_OperatingSystem; $up = (Get-Date) - $os.LastBootUpTime; Write-Output ("machine boot: {0:u}  uptime: {1:F0} min  os: {2} {3}" -f $os.LastBootUpTime.ToUniversalTime(), $up.TotalMinutes, $os.Caption, $os.Version) } catch { Write-Output "uptime ERR: $_" }
try { $e = @(Get-Process msedge -ErrorAction SilentlyContinue); Write-Output ("msedge processes: {0}" -f $e.Count); if ($e.Count -gt 0) { $st = ($e | Sort-Object StartTime | Select-Object -First 1).StartTime; Write-Output ("  oldest msedge start: {0:u}" -f $st.ToUniversalTime()) } } catch { Write-Output "msedge ERR: $_" }
try { Write-Output ("sessions: " + ((quser 2>&1 | Out-String).Trim() -replace "\s{2,}", " | ")) } catch { Write-Output "quser ERR: $_" }

# --- 1. Geolocation service --------------------------------------------------------------------
Write-Output "--- lfsvc (Geolocation Service) ---"
try { Get-Service lfsvc -ErrorAction Stop | ForEach-Object { Write-Output ("lfsvc: Status={0} StartType={1}" -f $_.Status, $_.StartType) } } catch { Write-Output "lfsvc ERR: $_" }

# --- 2. Location consent: system toggle + every loaded user hive + desktop apps that used it ----
Write-Output "--- location consent ---"
try { $sys = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\location" -ErrorAction Stop; Write-Output ("HKLM location Value = {0}" -f $sys.Value) } catch { Write-Output "HKLM consent ERR: $_" }
foreach ($hive in (Get-ChildItem Registry::HKEY_USERS -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -like 'S-1-5-21-*' -and $_.PSChildName -notlike '*_Classes' })) {
  $sid = $hive.PSChildName
  $base = "Registry::HKEY_USERS\$sid\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\location"
  try {
    $u = Get-ItemProperty $base -ErrorAction Stop
    Write-Output ("user ...{0}: location Value = {1}" -f $sid.Substring($sid.Length - 6), $u.Value)
    $np = Join-Path $base "NonPackaged"
    if (Test-Path $np) {
      foreach ($app in (Get-ChildItem $np -ErrorAction SilentlyContinue)) {
        $p = Get-ItemProperty $app.PSPath
        $name = $app.PSChildName -replace '#', '\'
        $start = '-'; $stop = '-'
        if ($p.LastUsedTimeStart) { $start = [DateTime]::FromFileTimeUtc([int64]$p.LastUsedTimeStart).ToString('u') }
        if ($p.LastUsedTimeStop)  { $stop  = [DateTime]::FromFileTimeUtc([int64]$p.LastUsedTimeStop).ToString('u') }
        Write-Output ("  desktop app used location: {0}  start={1} stop={2}" -f $name, $start, $stop)
      }
    }
  } catch { Write-Output ("user ...{0}: no consent key" -f $sid.Substring($sid.Length - 6)) }
}

# --- 3. Edge profile: geolocation permission for the LifeLog origin ----------------------------
Write-Output "--- Edge geolocation site permissions (shumania.github.io) ---"
foreach ($u in (Get-ChildItem "C:\Users" -Directory -ErrorAction SilentlyContinue)) {
  foreach ($prof in (Get-ChildItem (Join-Path $u.FullName "AppData\Local\Microsoft\Edge\User Data") -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'Default' -or $_.Name -like 'Profile *' })) {
    $pf = Join-Path $prof.FullName "Preferences"
    if (-not (Test-Path $pf)) { continue }
    try {
      $j = Get-Content $pf -Raw -ErrorAction Stop | ConvertFrom-Json
      $geo = $j.profile.content_settings.exceptions.geolocation
      if ($geo) {
        $hits = $geo.PSObject.Properties | Where-Object { $_.Name -like '*shumania*' }
        if ($hits) { foreach ($h in $hits) { Write-Output ("{0}\{1}: {2} -> setting={3} last_modified={4}" -f $u.Name, $prof.Name, $h.Name, $h.Value.setting, $h.Value.last_modified) } }
        else { Write-Output ("{0}\{1}: no shumania geolocation entry ({2} entries total)" -f $u.Name, $prof.Name, @($geo.PSObject.Properties).Count) }
      } else { Write-Output ("{0}\{1}: no geolocation exceptions block" -f $u.Name, $prof.Name) }
    } catch { Write-Output ("{0}\{1}: Preferences parse ERR: {2}" -f $u.Name, $prof.Name, $_.Exception.Message) }
  }
}

# --- 4. Adapters: is there a Wi-Fi radio Windows can position with? ----------------------------
Write-Output "--- adapters ---"
try { Get-NetAdapter -ErrorAction Stop | ForEach-Object { Write-Output ("{0} | {1} | {2} | {3} | {4}" -f $_.Name, $_.InterfaceDescription, $_.Status, $_.PhysicalMediaType, $_.LinkSpeed) } } catch { Write-Output "Get-NetAdapter ERR: $_" }
Write-Output "--- wlan interfaces ---"
try { Write-Output ((netsh wlan show interfaces 2>&1 | Out-String).Trim()) } catch { Write-Output "netsh interfaces ERR: $_" }
Write-Output "--- visible Wi-Fi networks ---"
try {
  $nets = netsh wlan show networks mode=bssid 2>&1 | Out-String
  $ssids = @([regex]::Matches($nets, '(?m)^SSID \d+ : (.*)$') | ForEach-Object { $_.Groups[1].Value.Trim() })
  $bssids = [regex]::Matches($nets, '(?m)^\s+BSSID \d+\s*:').Count
  Write-Output ("SSIDs visible: {0}   BSSIDs visible: {1}" -f $ssids.Count, $bssids)
  $ssids | Select-Object -First 12 | ForEach-Object { Write-Output ("  {0}" -f $_) }
  if ($ssids.Count -eq 0) { Write-Output ("netsh said: " + (($nets.Trim() -split "`n") | Select-Object -First 2 | Out-String).Trim()) }
} catch { Write-Output "netsh networks ERR: $_" }

# --- 5. WinRT Geolocator: the platform API Edge/Chromium use on Windows; exposes PositionSource -
Write-Output "--- WinRT Geolocator (DesiredAccuracy=Default, same as the page's enableHighAccuracy:false) ---"
try {
  [Windows.Devices.Geolocation.Geolocator,Windows.Devices.Geolocation,ContentType=WindowsRuntime] | Out-Null
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $script:asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  function Await($WinRtTask, $ResultType) {
    $asTask = $script:asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    if (-not $netTask.Wait(20000)) { throw "timed out after 20s" }
    return $netTask.Result
  }
  try {
    $access = Await ([Windows.Devices.Geolocation.Geolocator]::RequestAccessAsync()) ([Windows.Devices.Geolocation.GeolocationAccessStatus])
    Write-Output ("RequestAccessAsync: {0}" -f $access)
  } catch { Write-Output ("RequestAccessAsync ERR (normal outside an interactive session): {0}" -f $_.Exception.Message) }
  $geo = New-Object Windows.Devices.Geolocation.Geolocator
  $geo.DesiredAccuracy = [Windows.Devices.Geolocation.PositionAccuracy]::Default
  Write-Output ("LocationStatus before: {0}" -f $geo.LocationStatus)
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $pos = Await ($geo.GetGeopositionAsync()) ([Windows.Devices.Geolocation.Geoposition])
  $sw.Stop()
  $c = $pos.Coordinate
  Write-Output ("FIX: lat={0:F6} lon={1:F6} acc={2:F0}m source={3} ts={4:u} ({5} ms)" -f $c.Point.Position.Latitude, $c.Point.Position.Longitude, $c.Accuracy, $c.PositionSource, $c.Timestamp.UtcDateTime, $sw.ElapsedMilliseconds)
  Write-Output (Verdict $c.Point.Position.Latitude $c.Point.Position.Longitude $c.Accuracy)
  Write-Output ("LocationStatus after: {0}" -f $geo.LocationStatus)
  # second sample with high accuracy requested, to see whether a better source exists at all
  try {
    $geo2 = New-Object Windows.Devices.Geolocation.Geolocator
    $geo2.DesiredAccuracy = [Windows.Devices.Geolocation.PositionAccuracy]::High
    $pos2 = Await ($geo2.GetGeopositionAsync()) ([Windows.Devices.Geolocation.Geoposition])
    $c2 = $pos2.Coordinate
    Write-Output ("FIX(high): lat={0:F6} lon={1:F6} acc={2:F0}m source={3}" -f $c2.Point.Position.Latitude, $c2.Point.Position.Longitude, $c2.Accuracy, $c2.PositionSource)
  } catch { Write-Output ("FIX(high) ERR: {0}" -f $_.Exception.Message) }
} catch {
  $msg = $_.Exception.Message; if ($_.Exception.InnerException) { $msg = $msg + " / " + $_.Exception.InnerException.Message }
  Write-Output ("WinRT Geolocator ERR: {0}" -f $msg)
}

# --- 6. .NET GeoCoordinateWatcher (older Location API) - second opinion -------------------------
Write-Output "--- System.Device GeoCoordinateWatcher ---"
try {
  Add-Type -AssemblyName System.Device
  $wt = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::Default)
  $started = $wt.TryStart($false, [TimeSpan]::FromSeconds(15))
  Write-Output ("TryStart={0} Status={1} Permission={2}" -f $started, $wt.Status, $wt.Permission)
  $loc = $wt.Position.Location
  if ($loc -and -not $loc.IsUnknown) {
    Write-Output ("FIX2: lat={0:F6} lon={1:F6} acc={2:F0}m ts={3:u}" -f $loc.Latitude, $loc.Longitude, $loc.HorizontalAccuracy, $wt.Position.Timestamp.UtcDateTime)
    Write-Output (Verdict $loc.Latitude $loc.Longitude $loc.HorizontalAccuracy)
  } else { Write-Output "FIX2: unknown (no position)" }
  $wt.Stop(); $wt.Dispose()
} catch { Write-Output "GeoCoordinateWatcher ERR: $_" }

Write-Output "=== geo-diag-v1 done (read-only, nothing modified) ==="
