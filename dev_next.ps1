# v54 - enumerate manifest using _manifest_db_path + sqlite3

$backupRoot = "C:\Users\andre\Apple\MobileSync\Backup"
$backupId   = "00008130-001929983450001C"
$backupPath = "$backupRoot\$backupId"
$password   = "#ngrierBill70"

$script = @'
import sys, sqlite3
from iphone_backup_decrypt import EncryptedBackup

backup_path = sys.argv[1]
password    = sys.argv[2]

print(f"Opening backup: {backup_path}")
backup = EncryptedBackup(backup_directory=backup_path, passphrase=password)

# Show available attributes for diagnosis
attrs = [a for a in dir(backup) if 'manifest' in a.lower() or 'db' in a.lower()]
print(f"Relevant attributes: {attrs}")

# Try _manifest_db_path
db_path = backup._manifest_db_path
print(f"Manifest DB path: {db_path}")

# Open with sqlite3 directly
con = sqlite3.connect(db_path)
cur = con.cursor()
cur.execute("""
    SELECT domain, relativePath, flags, fileID
    FROM Files
    WHERE domain LIKE '%podcast%'
    ORDER BY relativePath
""")
rows = cur.fetchall()
print(f"Found {len(rows)} files in podcast domain(s):")
for row in rows:
    print(f"  domain={row[0]}  path={row[1]}  flags={row[2]}  id={row[3][:8]}")
con.close()
'@

$pyScript = "$env:TEMP\enum_manifest2.py"
$script | Set-Content -Path $pyScript -Encoding UTF8

Write-Host "Enumerating manifest for podcast files..."
python $pyScript $backupPath $password
