# dev_next.ps1 v42 - introspect EncryptedBackup to find decrypted manifest
$computer = $env:COMPUTERNAME
Write-Host "[$computer] dev_next.ps1 v42 - introspect backup object"

$python = $null
$candidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\andre\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python313\python.exe"
)
foreach ($c in $candidates) { if (Test-Path $c) { $python = $c; break } }
if (-not $python) { throw "Python not found" }

$backupBase = $null
$backupCandidates = @(
    "C:\Users\andre\Apple\MobileSync\Backup",
    "C:\Users\Shumadmin\Apple\MobileSync\Backup",
    "C:\Users\andre\AppData\Roaming\Apple Computer\MobileSync\Backup"
)
foreach ($b in $backupCandidates) {
    if (Test-Path $b) {
        $dirs = Get-ChildItem -Path $b -Directory | Sort-Object LastWriteTime -Descending
        if ($dirs.Count -gt 0) { $backupBase = $dirs[0].FullName; break }
    }
}
if (-not $backupBase) { throw "No backup folder found" }

$pyScript = "$env:TEMP\introspect_backup.py"
@'
import sys, sqlite3, inspect
from iphone_backup_decrypt import EncryptedBackup

backup_path = sys.argv[1]
backup = EncryptedBackup(backup_directory=backup_path, passphrase="#ngrierBill70")

print("=== All attributes/methods ===")
for name in dir(backup):
    if not name.startswith("__"):
        val = getattr(backup, name)
        t = type(val).__name__
        print(f"  {name}: {t}")

print()
print("=== Looking for sqlite connections ===")
for name in dir(backup):
    if not name.startswith("__"):
        val = getattr(backup, name)
        if hasattr(val, 'cursor') or 'sqlite' in str(type(val)).lower() or 'connect' in str(type(val)).lower():
            print(f"  FOUND DB-like: {name} = {val}")

print()
print("=== Source file ===")
print(inspect.getfile(EncryptedBackup))
'@ | Set-Content $pyScript -Encoding UTF8

Write-Host "[$computer] Introspecting..."
& $python $pyScript $backupBase
Write-Host "[$computer] v42 complete."
