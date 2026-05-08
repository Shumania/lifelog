# dev_next.ps1 v44 - list podcast files via manifest_db_cursor() as context manager
$computer = $env:COMPUTERNAME
Write-Host "[$computer] dev_next.ps1 v44 - list podcast manifest files"

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

$pyScript = "$env:TEMP\list_manifest_v44.py"
@'
import sys
from iphone_backup_decrypt import EncryptedBackup

backup_path = sys.argv[1]
print(f"Decrypting: {backup_path}")
backup = EncryptedBackup(backup_directory=backup_path, passphrase="#ngrierBill70")

with backup.manifest_db_cursor() as cur:
    cur.execute("SELECT domain, relativePath FROM Files WHERE domain LIKE '%podcast%' ORDER BY relativePath")
    rows = cur.fetchall()
    print(f"Found {len(rows)} files in podcasts domain:")
    for r in rows:
        print(f"  [{r[0]}] {r[1]}")
'@ | Set-Content $pyScript -Encoding UTF8

Write-Host "[$computer] Querying manifest..."
& $python $pyScript $backupBase
Write-Host "[$computer] v44 complete."
