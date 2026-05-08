# dev_next.ps1 v38 - WAL merge diagnostic (fixed SQL quoting)
$machine = $env:COMPUTERNAME
Write-Host "[$machine] dev_next.ps1 v38 - WAL merge diagnostic"

$python = $null
$candidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python314\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python313\python.exe"
)
foreach ($p in $candidates) {
    if (Test-Path $p) { $python = $p; break }
}
if (-not $python) {
    throw "[$machine] No Python found"
}
Write-Host "[$machine] Python: $python"

# Find backup
$backupRoots = @(
    "C:\Users\andre\Apple\MobileSync\Backup",
    "C:\Users\Shumadmin\Apple\MobileSync\Backup",
    "$env:APPDATA\Apple Computer\MobileSync\Backup"
)
$backupDir = $null
foreach ($root in $backupRoots) {
    if (Test-Path $root) {
        $dirs = Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue
        foreach ($d in $dirs) {
            $backupDir = $d.FullName
            break
        }
    }
    if ($backupDir) { break }
}
if (-not $backupDir) {
    throw "[$machine] No backup directory found"
}
Write-Host "[$machine] Found backup: $backupDir"

# Write Python diagnostic script to a temp file
$pyScript = @'
import sys, os, tempfile, shutil, sqlite3
sys.path.insert(0, r'C:\ProgramData\LifeLog')

try:
    from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike
except ImportError:
    print("ERROR: iphone_backup_decrypt not installed")
    sys.exit(1)

backup_dir = sys.argv[1]
password = "#ngrierBill70"

print(f"Decrypting backup at: {backup_dir}")
try:
    backup = EncryptedBackup(backup_directory=backup_dir, passphrase=password)
except Exception as e:
    print(f"ERROR decrypting: {e}")
    sys.exit(1)

tmpdir = tempfile.mkdtemp()
try:
    # Extract main DB
    db_out = os.path.join(tmpdir, "MTLibrary.sqlite")
    backup.extract_file(
        relative_name="Library/Application Support/Podcasts/MTLibrary.sqlite",
        domain="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
        output_filename=db_out
    )
    print(f"Main DB extracted: {os.path.getsize(db_out):,} bytes")

    # Try WAL sidecar
    wal_out = os.path.join(tmpdir, "MTLibrary.sqlite-wal")
    try:
        backup.extract_file(
            relative_name="Library/Application Support/Podcasts/MTLibrary.sqlite-wal",
            domain="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
            output_filename=wal_out
        )
        if os.path.exists(wal_out):
            print(f"WAL sidecar extracted: {os.path.getsize(wal_out):,} bytes")
        else:
            print("WAL sidecar: not found in backup")
    except Exception as e:
        print(f"WAL sidecar: not found ({e})")

    # Also try SHM
    shm_out = os.path.join(tmpdir, "MTLibrary.sqlite-shm")
    try:
        backup.extract_file(
            relative_name="Library/Application Support/Podcasts/MTLibrary.sqlite-shm",
            domain="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
            output_filename=shm_out
        )
        if os.path.exists(shm_out):
            print(f"SHM extracted: {os.path.getsize(shm_out):,} bytes")
    except:
        print("SHM: not found")

    # Query merged DB
    conn = sqlite3.connect(db_out)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL")
    total = cur.fetchone()[0]
    print(f"Episodes with ZLASTDATEPLAYED: {total}")

    cur.execute("SELECT MIN(ZLASTDATEPLAYED), MAX(ZLASTDATEPLAYED) FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL")
    mn, mx = cur.fetchone()
    if mn and mx:
        import datetime
        epoch = datetime.datetime(2001, 1, 1)
        mn_dt = epoch + datetime.timedelta(seconds=mn)
        mx_dt = epoch + datetime.timedelta(seconds=mx)
        print(f"Earliest played: {mn_dt.strftime('%Y-%m-%d')}")
        print(f"Latest played:   {mx_dt.strftime('%Y-%m-%d')}")

    # Show 5 most recent
    cur.execute("SELECT ZTITLE, ZLASTDATEPLAYED FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL ORDER BY ZLASTDATEPLAYED DESC LIMIT 5")
    rows = cur.fetchall()
    print("Top 5 most recent episodes:")
    for title, ts in rows:
        dt = epoch + datetime.timedelta(seconds=ts)
        print(f"  {dt.strftime('%Y-%m-%d')} - {title}")

    conn.close()

finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
'@

$pyFile = [System.IO.Path]::GetTempFileName() + ".py"
$pyScript | Set-Content -Path $pyFile -Encoding UTF8
Write-Host "[$machine] Running diagnostic..."

& $python $pyFile $backupDir
Remove-Item $pyFile -ErrorAction SilentlyContinue

Write-Host "[$machine] v38 complete."
