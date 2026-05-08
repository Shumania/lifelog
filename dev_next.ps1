# dev_next.ps1 v40 - WAL diagnostic with keyword-only extract_file args
$label = "[" + $env:COMPUTERNAME + "]"

# Find Python
$pythonPaths = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\andre\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\ProgramData\LifeLog\python\python.exe"
)
$python = $null
foreach ($p in $pythonPaths) {
    if (Test-Path $p) { $python = $p; break }
}
if (-not $python) { throw "$label No Python found." }
Write-Host "$label dev_next.ps1 v40 - WAL diagnostic (keyword-only args)"
Write-Host "$label Python: $python"

# Find backup
$backupRoots = @(
    "C:\Users\andre\Apple\MobileSync\Backup",
    "C:\Users\Shumadmin\Apple\MobileSync\Backup",
    "C:\Users\andre\AppData\Roaming\Apple Computer\MobileSync\Backup",
    "C:\Users\Shumadmin\AppData\Roaming\Apple Computer\MobileSync\Backup"
)
$backupDir = $null
foreach ($root in $backupRoots) {
    if (Test-Path $root) {
        $found = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $backupDir = $found.FullName; break }
    }
}
if (-not $backupDir) { throw "$label No backup directory found." }
Write-Host "$label Found backup: $backupDir"

# Write diagnostic Python script
$pyScript = "$env:TEMP\lifelog_wal_diag_v40.py"
@'
import sys
import os
import tempfile
import shutil
import sqlite3
from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike

BACKUP_PATH = sys.argv[1]
PASSWORD = "#ngrierBill70"

print(f"Decrypting backup at: {BACKUP_PATH}")
backup = EncryptedBackup(backup_directory=BACKUP_PATH, passphrase=PASSWORD)

tmp = tempfile.mkdtemp()
try:
    main_db = os.path.join(tmp, "MTLibrary.sqlite")
    wal_file = os.path.join(tmp, "MTLibrary.sqlite-wal")
    shm_file = os.path.join(tmp, "MTLibrary.sqlite-shm")

    # Extract main DB using keyword-only args
    print("Extracting main DB...")
    backup.extract_file(
        relative_path="Library/Application Support/com.apple.podcasts/MTLibrary.sqlite",
        domain_like="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
        output_filename=main_db
    )
    main_size = os.path.getsize(main_db) if os.path.exists(main_db) else 0
    print(f"Main DB: {main_size // 1024 // 1024} MB")

    # Try WAL
    try:
        backup.extract_file(
            relative_path="Library/Application Support/com.apple.podcasts/MTLibrary.sqlite-wal",
            domain_like="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
            output_filename=wal_file
        )
        wal_size = os.path.getsize(wal_file) if os.path.exists(wal_file) else 0
        print(f"WAL sidecar: {wal_size // 1024 // 1024} MB")
    except Exception as e:
        print(f"WAL not found: {e}")

    # Try SHM
    try:
        backup.extract_file(
            relative_path="Library/Application Support/com.apple.podcasts/MTLibrary.sqlite-shm",
            domain_like="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
            output_filename=shm_file
        )
        print(f"SHM found: {os.path.getsize(shm_file)} bytes")
    except Exception as e:
        print(f"SHM not found: {e}")

    # Query merged DB
    if os.path.exists(main_db):
        conn = sqlite3.connect(main_db)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL")
        count = cur.fetchone()[0]
        cur.execute("SELECT MIN(ZLASTDATEPLAYED), MAX(ZLASTDATEPLAYED) FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL")
        row = cur.fetchone()
        # Apple Core Data epoch: Jan 1 2001 = Unix 978307200
        if row[0]:
            import datetime
            mn = datetime.datetime.utcfromtimestamp(row[0] + 978307200).strftime('%Y-%m-%d')
            mx = datetime.datetime.utcfromtimestamp(row[1] + 978307200).strftime('%Y-%m-%d')
            print(f"Episodes with played date: {count}")
            print(f"Date range: {mn} to {mx}")
        # 5 most recent
        cur.execute("""
            SELECT ZTITLE, ZLASTDATEPLAYED
            FROM ZMTEPISODE
            WHERE ZLASTDATEPLAYED IS NOT NULL
            ORDER BY ZLASTDATEPLAYED DESC
            LIMIT 5
        """)
        print("Most recent 5 episodes:")
        for r in cur.fetchall():
            dt = datetime.datetime.utcfromtimestamp(r[1] + 978307200).strftime('%Y-%m-%d')
            print(f"  {dt}: {r[0]}")
        conn.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("Done.")
'@ | Set-Content $pyScript -Encoding UTF8

Write-Host "$label Running diagnostic..."
& $python $pyScript $backupDir
Remove-Item $pyScript -ErrorAction SilentlyContinue
Write-Host "$label v40 complete."
