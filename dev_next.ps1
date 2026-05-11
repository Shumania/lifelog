# v53 - enumerate manifest to find exact relative_path for podcast SQLite DB

$LifeLogDir = "C:\ProgramData\LifeLog"
$backupRoot = "C:\Users\andre\Apple\MobileSync\Backup"
$backupId   = "00008130-001929983450001C"
$backupPath = "$backupRoot\$backupId"
$password   = "#ngrierBill70"

$script = @'
import sys
from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike, DomainLike

backup_path = sys.argv[1]
password    = sys.argv[2]

print(f"Opening backup: {backup_path}")
backup = EncryptedBackup(backup_directory=backup_path, passphrase=password)

# Query the manifest DB for all files in any podcast-related domain
cur = backup._manifest_db.cursor()
cur.execute("""
    SELECT domain, relativePath, flags, fileID
    FROM Files
    WHERE domain LIKE '%podcast%'
    ORDER BY relativePath
""")
rows = cur.fetchall()
print(f"Found {len(rows)} files in podcast domain(s):")
for row in rows:
    print(f"  domain={row[0]}  path={row[1]}  flags={row[2]}  fileID={row[3][:8]}")
'@

$pyScript = "$env:TEMP\enum_manifest.py"
$script | Set-Content -Path $pyScript -Encoding UTF8

Write-Host "Enumerating manifest for podcast files..."
python $pyScript $backupPath $password
