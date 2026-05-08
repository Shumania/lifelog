# dev_next.ps1 v41 - list podcast domain files via _manifest_db_path
$computer = $env:COMPUTERNAME
Write-Host "[$computer] dev_next.ps1 v41 - list podcast manifest files"

$python = $null
$candidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\andre\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python313\python.exe"
)
foreach ($c in $candidates) { if (Test-Path $c) { $python = $c; break } }
if (-not $python) { throw "Python not found" }
Write-Host "[$computer] Python: $python"

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
Write-Host "[$computer] Backup: $backupBase"

$pyScript = "$env:TEMP\list_manifest.py"
@'
import sys, sqlite3
from iphone_backup_decrypt import EncryptedBackup

backup_path = sys.argv[1]
print(f"Decrypting manifest at: {backup_path}")
backup = EncryptedBackup(backup_directory=backup_path, passphrase="#ngrierBill70")
db_path = backup._manifest_db_path
print(f"Manifest DB path: {db_path}")
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT domain, relativePath FROM Files WHERE domain LIKE '%podcast%' ORDER BY relativePath")
rows = cur.fetchall()
print(f"Found {len(rows)} files in podcasts domain:")
for r in rows:
    print(f"  [{r[0]}] {r[1]}")
conn.close()
'@ | Set-Content $pyScript -Encoding UTF8

Write-Host "[$computer] Running manifest query..."
& $python $pyScript $backupBase
Write-Host "[$computer] v41 complete."
