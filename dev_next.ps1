# dev_next.ps1 v39 - WAL diagnostic, positional args + introspection
$machine = $env:COMPUTERNAME
Write-Host "[$machine] dev_next.ps1 v39 - WAL diagnostic (positional args)"

$python = $null
$candidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python314\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python313\python.exe"
)
foreach ($p in $candidates) {
    if (Test-Path $p) { $python = $p; break }
}
if (-not $python) { throw "[$machine] No Python found" }
Write-Host "[$machine] Python: $python"

$backupRoots = @(
    "C:\Users\andre\Apple\MobileSync\Backup",
    "C:\Users\Shumadmin\Apple\MobileSync\Backup",
    "$env:APPDATA\Apple Computer\MobileSync\Backup"
)
$backupDir = $null
foreach ($root in $backupRoots) {
    if (Test-Path $root) {
        $dirs = Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue
        foreach ($d in $dirs) { $backupDir = $d.FullName; break }
    }
    if ($backupDir) { break }
}
if (-not $backupDir) { throw "[$machine] No backup directory found" }
Write-Host "[$machine] Found backup: $backupDir"

$pyScript = @'
import sys, os, tempfile, shutil, sqlite3, inspect
sys.path.insert(0, r'C:\ProgramData\LifeLog')

try:
    from iphone_backup_decrypt import EncryptedBackup
except ImportError:
    print("ERROR: iphone_backup_decrypt not installed")
    sys.exit(1)

# Print extract_file signature for debugging
try:
    sig = inspect.signature(EncryptedBackup.extract_file)
    print(f"extract_file signature: {sig}")
except:
    pass

backup_dir = sys.argv[1]
password = "#ngrierBill70"

print(f"Decrypting backup at: {backup_dir}")
try:
    backup = EncryptedBackup(backup_directory=backup_dir, passphrase=password)
except Exception as e:
    print(f"ERROR decrypting: {e}")
    sys.exit(1)

tmpdir = tempfile.mkdtemp()
DOMAIN = "AppDomainGroup-243LU875E5.groups.com.apple.podcasts"
DB_REL  = "Library/Application Support/Podcasts/MTLibrary.sqlite"
WAL_REL = "Library/Application Support/Podcasts/MTLibrary.sqlite-wal"
SHM_REL = "Library/Application Support/Podcasts/MTLibrary.sqlite-shm"

def try_extract(rel_name, out_path):
    """Try multiple calling conventions."""
    # Style 1: keyword args (newer lib)
    try:
        backup.extract_file(relative_name=rel_name, domain=DOMAIN, output_filename=out_path)
        return True
    except TypeError:
        pass
    # Style 2: positional (domain first)
    try:
        backup.extract_file(DOMAIN, rel_name, out_path)
        return True
    except TypeError:
        pass
    # Style 3: positional (rel_name first)
    try:
        backup.extract_file(rel_name, DOMAIN, out_path)
        return True
    except TypeError:
        pass
    # Style 4: just relative_name + output (no domain)
    try:
        backup.extract_file(relative_name=rel_name, output_filename=out_path)
        return True
    except TypeError:
        pass
    return False

try:
    db_out  = os.path.join(tmpdir, "MTLibrary.sqlite")
    wal_out = os.path.join(tmpdir, "MTLibrary.sqlite-wal")
    shm_out = os.path.join(tmpdir, "MTLibrary.sqlite-shm")

    if try_extract(DB_REL, db_out):
        print(f"Main DB extracted: {os.path.getsize(db_out):,} bytes")
    else:
        print("ERROR: Could not extract main DB - no working calling convention found")
        sys.exit(1)

    if try_extract(WAL_REL, wal_out) and os.path.exists(wal_out):
        print(f"WAL sidecar extracted: {os.path.getsize(wal_out):,} bytes")
    else:
        print("WAL sidecar: not found in backup")

    if try_extract(SHM_REL, shm_out) and os.path.exists(shm_out):
        print(f"SHM extracted: {os.path.getsize(shm_out):,} bytes")

    conn = sqlite3.connect(db_out)
    cur = conn.cursor()
    import datetime
    epoch = datetime.datetime(2001, 1, 1)

    cur.execute("SELECT COUNT(*) FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL")
    total = cur.fetchone()[0]
    print(f"Episodes with ZLASTDATEPLAYED: {total}")

    cur.execute("SELECT MIN(ZLASTDATEPLAYED), MAX(ZLASTDATEPLAYED) FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL")
    mn, mx = cur.fetchone()
    if mn and mx:
        print(f"Earliest played: {(epoch + datetime.timedelta(seconds=mn)).strftime('%Y-%m-%d')}")
        print(f"Latest played:   {(epoch + datetime.timedelta(seconds=mx)).strftime('%Y-%m-%d')}")

    cur.execute("SELECT ZTITLE, ZLASTDATEPLAYED FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL ORDER BY ZLASTDATEPLAYED DESC LIMIT 5")
    print("Top 5 most recent:")
    for title, ts in cur.fetchall():
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
Write-Host "[$machine] v39 complete."
