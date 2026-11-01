# sonos-app-probe-v1: READ-ONLY. Is the Sonos desktop app installed on this PC, and does Windows know a sonos:// protocol handler? Modifies nothing.
$ErrorActionPreference = "Continue"
$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffffffK"
Write-Output "=== sonos-app-probe-v1 on $env:COMPUTERNAME at $ts (read-only) ==="

Write-Output ""
Write-Output "--- Protocol handlers: sonos:// and spotify:// (HKCR merges HKLM + HKCU classes) ---"
foreach ($scheme in @("sonos","spotify")) {
  foreach ($root in @("Registry::HKEY_CLASSES_ROOT\$scheme","Registry::HKEY_CURRENT_USER\Software\Classes\$scheme","Registry::HKEY_LOCAL_MACHINE\Software\Classes\$scheme")) {
    if (Test-Path $root) {
      $k = Get-Item $root
      $urlProto = $k.GetValue("URL Protocol")
      $cmd = $null
      $cmdKey = Join-Path $root "shell\open\command"
      if (Test-Path $cmdKey) { $cmd = (Get-Item $cmdKey).GetValue("") }
      $flag = if ($null -ne $urlProto) { "URL Protocol=yes" } else { "URL Protocol=MISSING" }
      Write-Output ("  {0,-8} {1,-55} {2} | open={3}" -f "$($scheme)://", $root.Replace("Registry::",""), $flag, $cmd)
    } else {
      Write-Output ("  {0,-8} {1,-55} (absent)" -f "$($scheme)://", $root.Replace("Registry::",""))
    }
  }
}

Write-Output ""
Write-Output "--- Sonos desktop app: install records (Uninstall keys, per-machine + per-user) ---"
$found = $false
foreach ($u in @("HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall","HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall","HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall")) {
  if (Test-Path $u) {
    Get-ChildItem $u | ForEach-Object {
      $p = Get-ItemProperty $_.PSPath
      if ($p.DisplayName -like "*Sonos*") {
        $found = $true
        Write-Output ("  {0} | v{1} | loc={2} | icon={3}" -f $p.DisplayName, $p.DisplayVersion, $p.InstallLocation, $p.DisplayIcon)
      }
    }
  }
}
if (-not $found) { Write-Output "  (no Sonos entry in Uninstall keys)" }

Write-Output ""
Write-Output "--- Sonos as a Store/MSIX package (Get-AppxPackage) ---"
try {
  $pk = Get-AppxPackage -Name "*Sonos*" -ErrorAction SilentlyContinue
  if ($pk) { $pk | ForEach-Object { Write-Output ("  {0} v{1} | {2}" -f $_.Name, $_.Version, $_.InstallLocation) } } else { Write-Output "  (none)" }
} catch { Write-Output "  (Get-AppxPackage unavailable: $($_.Exception.Message))" }

Write-Output ""
Write-Output "--- Sonos.exe on disk (common locations) ---"
$cands = @("$env:ProgramFiles\Sonos\Sonos.exe","${env:ProgramFiles(x86)}\Sonos\Sonos.exe","$env:LOCALAPPDATA\Programs\Sonos\Sonos.exe","$env:LOCALAPPDATA\Sonos\Sonos.exe")
$hit = $false
foreach ($c in $cands) { if (Test-Path $c) { $hit = $true; $v = (Get-Item $c).VersionInfo; Write-Output ("  {0} | file v{1} | product={2}" -f $c, $v.FileVersion, $v.ProductName) } }
if (-not $hit) { Write-Output "  (not found in the usual places)" }

Write-Output ""
Write-Output "--- Sonos process running right now? ---"
$pr = Get-Process -Name "Sonos*" -ErrorAction SilentlyContinue
if ($pr) { $pr | ForEach-Object { Write-Output ("  {0} pid={1} path={2}" -f $_.ProcessName, $_.Id, $_.Path) } } else { Write-Output "  (no Sonos process)" }

Write-Output ""
Write-Output "--- Default browser (which app will show the 'Open Sonos?' prompt) ---"
try {
  $ua = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice" -ErrorAction Stop
  Write-Output ("  https -> {0}" -f $ua.ProgId)
} catch { Write-Output "  (could not read UserChoice)" }

Write-Output ""
Write-Output "=== sonos-app-probe-v1 done (read-only, nothing modified) ==="
