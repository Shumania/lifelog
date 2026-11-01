# sonos-unregister-v1: ROLLBACK for sonos-register-v1. Deletes the sonos:// protocol key from HKLM and HKCU if present. Nothing else touched.
$ErrorActionPreference = "Continue"
$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffffffK"
Write-Output "=== sonos-unregister-v1 on $env:COMPUTERNAME at $ts ==="
Write-Output "  running as: $(whoami)"
foreach ($k in @("Registry::HKEY_LOCAL_MACHINE\Software\Classes\sonos","Registry::HKEY_CURRENT_USER\Software\Classes\sonos")) {
  if (Test-Path $k) {
    try { Remove-Item -Path $k -Recurse -Force -ErrorAction Stop; Write-Output "  removed: $($k.Replace('Registry::',''))" }
    catch { Write-Output "  REMOVE FAILED $($k.Replace('Registry::','')): $($_.Exception.Message)" }
  } else { Write-Output "  absent:  $($k.Replace('Registry::',''))" }
}
$left = Test-Path "Registry::HKEY_CLASSES_ROOT\sonos"
Write-Output "  read-back HKCR\sonos present: $left"
Write-Output "=== sonos-unregister-v1 done ==="
