# dev_next.ps1 v45 - extract MTLibrary.sqlite with CORRECT path, check date range
$computer = $env:COMPUTERNAME
Write-Host "[$computer] dev_next.ps1 v45 - extract with correct path + date range check"

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

$pyScript = "$env:TEMP\extract_check_v45.py"
@'
import sys, os, tempfile, shutil, sqlite3, datetime
from iphone_backup_decrypt import EncryptedBackup

backup_path = sys.argv[1]
print(f"Decrypting: {backup_path}")
backup = EncryptedBackup(backup_directory=backup_path, passphrase="#ngrierBill70")

tmp = tempfile.mkdtemp()
try:
    main_db = os.path.join(tmp, "MTLibrary.sqlite")

    print("Extracting MTLibrary.sqlite (correct path: Documents/MTLibrary.sqlite)...")
    backup.extract_file(
        relative_path="Documents/MTLibrary.sqlite",
        domain_like="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
        output_filename=main_db
    )
    size_mb = os.path.getsize(main_db) / 1024 / 1024
    print(f"Main DB size: {size_mb:.1f} MB")

    # Try WAL too
    wal_db = os.path.join(tmp, "MTLibrary.sqlite-wal")
    try:
        backup.extract_file(
            relative_path="Documents/MTLibrary.sqlite-wal",
            domain_like="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
            output_filename=wal_db
        )
        print(f"WAL size: {os.path.getsize(wal_db)/1024/1024:.1f} MB")
    except Exception as e:
        print(f"WAL not in backup: {e}")

    conn = sqlite3.connect(main_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), MIN(ZLASTDATEPLAYED), MAX(ZLASTDATEPLAYED) FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL")
    count, mn, mx = cur.fetchone()
    min_date = datetime.datetime.utcfromtimestamp(mn + 978307200).strftime('%Y-%m-%d')
    max_date = datetime.datetime.utcfromtimestamp(mx + 978307200).strftime('%Y-%m-%d')
    print(f"Episodes with play date: {count}")
    print(f"Date range: {min_date} to {max_date}")
    print("Most recent 5:")
    cur.execute("SELECT ZTITLE, ZLASTDATEPLAYED FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL ORDER BY ZLASTDATEPLAYED DESC LIMIT 5")
    for row in cur.fetchall():
        d = datetime.datetime.utcfromtimestamp(row[1] + 978307200).strftime('%Y-%m-%d')
        print(f"  {d}: {row[0]}")
    conn.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)
'@ | Set-Content $pyScript -Encoding UTF8

Write-Host "[$computer] Running extraction + date check..."
& $python $pyScript $backupBase
Write-Host "[$computer] v45 complete."
