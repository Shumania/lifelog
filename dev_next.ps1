# v52 - diagnostic: show cursor, hash status, and top 5 newest episodes from DB
$version = "v52"
Write-Host "[$env:COMPUTERNAME] dev_next.ps1 $version"

$cursorFile = "C:\ProgramData\LifeLog\last_podcast_cursor.txt"
$hashFile   = "C:\ProgramData\LifeLog\last_backup_hash.txt"
$pyExe      = "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe"
$backupDir  = "C:\Users\andre\Apple\MobileSync\Backup\00008130-001929983450001C"

# Show cursor and hash status
if (Test-Path $cursorFile) {
    $cursor = (Get-Content $cursorFile -Raw).Trim()
    Write-Host "[$env:COMPUTERNAME] Cursor: $cursor"
} else {
    Write-Host "[$env:COMPUTERNAME] No cursor file"
}
if (Test-Path $hashFile) {
    Write-Host "[$env:COMPUTERNAME] Hash file present (unchanged-backup guard active)"
} else {
    Write-Host "[$env:COMPUTERNAME] No hash file (will run extraction)"
}

# Quick inline script: decrypt podcast DB and show top 5 newest episode timestamps
$pyScript = @'
import sys, os, json, datetime, tempfile, shutil
sys.path.insert(0, r"C:\ProgramData\LifeLog")
from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike
import sqlite3

BACKUP_DIR = r"C:\Users\andre\Apple\MobileSync\Backup\00008130-001929983450001C"
PASSWORD   = "#ngrierBill70"
DOMAIN     = "AppDomainGroup-243LU875E5.groups.com.apple.podcasts"
REL_PATH   = "Library/Application Support/com.apple.podcasts/MTLibrary.sqlite"
APPLE_EPOCH_OFFSET = 978307200

tmpdir = tempfile.mkdtemp(prefix="lifelog_diag_")
try:
    backup = EncryptedBackup(backup_directory=BACKUP_DIR, passphrase=PASSWORD)
    backup.extract_file(relative_path=REL_PATH, domain_like=DOMAIN, output_filename=os.path.join(tmpdir, "MTLibrary.sqlite"))
    conn = sqlite3.connect(os.path.join(tmpdir, "MTLibrary.sqlite"))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT e.ZTITLE as title, p.ZTITLE as show, e.ZLASTDATEPLAYED as apple_epoch
        FROM ZMTEPISODE e
        LEFT JOIN ZMTPODCAST p ON e.ZPODCAST = p.Z_PK
        WHERE e.ZLASTDATEPLAYED IS NOT NULL
        ORDER BY e.ZLASTDATEPLAYED DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    for r in rows:
        unix_ts = float(r["apple_epoch"]) + APPLE_EPOCH_OFFSET
        dt = datetime.datetime.utcfromtimestamp(unix_ts).strftime("%Y-%m-%d %H:%M")
        title = (r["title"] or "")[:40]
        show  = (r["show"]  or "")[:30]
        print(f"  apple_epoch={r['apple_epoch']:.0f}  date={dt}  show={show}  ep={title}")
    conn.close()
    print(f"DONE: showed top {len(rows)} newest episodes")
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
'@

$pyScript | & $pyExe -
Write-Host "[$env:COMPUTERNAME] $version complete."
