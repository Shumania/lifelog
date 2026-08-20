# dev_next: 2026-08-19 locate iPhone backup on Mind (read-only inspection, Mind only)
if ($env:COMPUTERNAME -notlike "*mind*") { exit 0 }
$ErrorActionPreference = "Continue"
Write-Output ("=== iPhone backup locator ({0}) ===" -f $env:COMPUTERNAME)
Write-Output ("service running as: {0}\{1}" -f $env:USERDOMAIN, $env:USERNAME)
Write-Output ("APPDATA:      {0}" -f $env:APPDATA)
Write-Output ("USERPROFILE:  {0}" -f $env:USERPROFILE)
Write-Output ("LOCALAPPDATA: {0}" -f $env:LOCALAPPDATA)
Write-Output ""
Write-Output "--- extractor's three search paths ---"
$paths = @(
  (Join-Path $env:APPDATA "Apple Computer\MobileSync\Backup"),
  (Join-Path $env:USERPROFILE "Apple\MobileSync\Backup"),
  (Join-Path $env:LOCALAPPDATA "Apple\MobileSync\Backup")
)
foreach ($p in $paths) {
  if (Test-Path $p) { Write-Output ("EXISTS: {0}" -f $p) } else { Write-Output ("absent: {0}" -f $p) }
}
Write-Output ""
Write-Output "--- scanning ALL user profiles for MobileSync backups ---"
$found = 0
foreach ($u in (Get-ChildItem "C:\Users" -Directory -ErrorAction SilentlyContinue)) {
  $cands = @(
    (Join-Path $u.FullName "AppData\Roaming\Apple Computer\MobileSync\Backup"),
    (Join-Path $u.FullName "Apple\MobileSync\Backup"),
    (Join-Path $u.FullName "AppData\Local\Apple\MobileSync\Backup")
  )
  foreach ($c in $cands) {
    if (Test-Path $c) {
      Write-Output ("FOUND BASE: {0}" -f $c)
      foreach ($d in (Get-ChildItem $c -Directory -ErrorAction SilentlyContinue)) {
        $man = Join-Path $d.FullName "Manifest.db"
        $manP = Join-Path $d.FullName "Manifest.plist"
        $stamp = "no manifest"
        if (Test-Path $man) { $stamp = ("Manifest.db {0:u}" -f (Get-Item $man).LastWriteTime) }
        elseif (Test-Path $manP) { $stamp = ("Manifest.plist {0:u}" -f (Get-Item $manP).LastWriteTime) }
        $size = [math]::Round(((Get-ChildItem $d.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1GB), 2)
        Write-Output ("  {0}  |  {1}  |  ~{2} GB" -f $d.Name, $stamp, $size)
        $found++
      }
    }
  }
}
if (Test-Path "C:\ProgramData\LifeLog\backup_tmp") {
  Write-Output "FOUND: C:\ProgramData\LifeLog\backup_tmp"
  Get-ChildItem "C:\ProgramData\LifeLog\backup_tmp" -Directory -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ("  {0}" -f $_.Name); $found++ }
}
if ($found -eq 0) { Write-Output "NO BACKUPS FOUND anywhere under C:\Users\* or backup_tmp" }
Write-Output ""
Write-Output "--- Apple backup software installed? ---"
foreach ($exe in @("iTunes","Apple Devices","AppleMobileBackup")) {
  $hit = Get-ChildItem "C:\Program Files","C:\Program Files (x86)","C:\Program Files\WindowsApps" -Directory -Filter ("*{0}*" -f $exe) -ErrorAction SilentlyContinue | Select-Object -First 2
  foreach ($h in $hit) { Write-Output ("installed: {0}" -f $h.FullName) }
}
Write-Output "=== locator done (read-only, nothing modified) ==="
