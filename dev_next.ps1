# dev_next.ps1 v47 - list exact podcast file paths from manifest
$version = "dev_next.ps1 v47 - list exact podcast file paths from manifest"
Write-Host "[$env:COMPUTERNAME] $version"

$python = $null
$candidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\andre\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\ProgramData\LifeLog\python\python.exe"
)
foreach ($c in $candidates) { if (Test-Path $c) { $python = $c; break } }
if (-not $python) { throw "Python not found" }
Write-Host "[$env:COMPUTERNAME] Python: $python"

$backupBase = "C:\Users\andre\Apple\MobileSync\Backup"
$backupDir = Get-ChildItem $backupBase -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
Write-Host "[$env:COMPUTERNAME] Backup: $backupDir"

$pyScript = "$env:TEMP\list_paths.py"
@'
import sys
from iphone_backup_decrypt import EncryptedBackup

backup = EncryptedBackup(backup_directory=sys.argv[1], passphrase="#ngrierBill70")
cur = backup._manifest_db.cursor()
cur.execute("SELECT domain, relativePath FROM Files WHERE domain LIKE '%podcast%' ORDER BY domain, relativePath")
rows = cur.fetchall()
print(f"Found {len(rows)} files in podcasts domain(s):")
for r in rows:
    print(f"  [{r[0]}] {r[1]}")
'@ | Set-Content $pyScript -Encoding UTF8

Write-Host "[$env:COMPUTERNAME] Querying manifest..."
& $python $pyScript $backupDir
Write-Host "[$env:COMPUTERNAME] v47 complete."
