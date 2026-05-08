# dev_next.ps1 v37 - Direct WAL merge diagnostic
$Machine = $env:COMPUTERNAME
Write-Host "[$Machine] dev_next.ps1 v37 - Direct WAL merge diagnostic"

# Find Python
$PythonPath = $null
$candidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\andre\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python314\python.exe",
    "C:\ProgramData\LifeLog\python\python.exe"
)
foreach ($p in $candidates) {
    if (Test-Path $p) { $PythonPath = $p; break }
}
Write-Host "[$Machine] Python: $PythonPath"

# Find backup dir
$backupRoots = @(
    "C:\Users\andre\Apple\MobileSync\Backup",
    "C:\Users\Shumadmin\Apple\MobileSync\Backup",
    "$env:APPDATA\Apple Computer\MobileSync\Backup",
    "$env:LOCALAPPDATA\Apple Computer\MobileSync\Backup"
)

$backupDir = $null
foreach ($root in $backupRoots) {
    if (Test-Path $root) {
        $dirs = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue
        foreach ($d in $dirs) {
            $backupDir = $d.FullName
            Write-Host "[$Machine] Found backup: $backupDir"
        }
    }
}

if (-not $backupDir) {
    Write-Host "[$Machine] ERROR: No backup directory found!"
} else {
    $diagScript = @"
import sys, os, tempfile, shutil, sqlite3

backup_dir = r'$backupDir'
password = '#ngrierBill70'
print(f'Backup dir: {backup_dir}')

try:
    from iphone_backup_decrypt import WrappedBackup, RelativePath, RelativePathsLike
except ImportError:
    print('ERROR: iphone_backup_decrypt not installed')
    sys.exit(1)

print('Decrypting backup...')
backup = WrappedBackup(backup_folder=backup_dir, passphrase=password)

tmpdir = tempfile.mkdtemp()
print(f'Temp dir: {tmpdir}')

try:
    # Extract main DB
    print('Extracting MTLibrary.sqlite...')
    backup.extract_file(relative_path='Library/Application Support/MTLibrary.sqlite',
                        domain_like='%podcast%',
                        output_filename=os.path.join(tmpdir, 'MTLibrary.sqlite'))

    main_size = os.path.getsize(os.path.join(tmpdir, 'MTLibrary.sqlite'))
    print(f'Main DB size: {main_size:,} bytes ({main_size//1024//1024} MB)')

    # Try to extract WAL
    wal_found = False
    try:
        backup.extract_file(relative_path='Library/Application Support/MTLibrary.sqlite-wal',
                            domain_like='%podcast%',
                            output_filename=os.path.join(tmpdir, 'MTLibrary.sqlite-wal'))
        wal_size = os.path.getsize(os.path.join(tmpdir, 'MTLibrary.sqlite-wal'))
        print(f'WAL sidecar size: {wal_size:,} bytes ({wal_size//1024//1024} MB)')
        wal_found = True
    except Exception as e:
        print(f'WAL sidecar NOT found: {e}')

    # Also try SHM
    try:
        backup.extract_file(relative_path='Library/Application Support/MTLibrary.sqlite-shm',
                            domain_like='%podcast%',
                            output_filename=os.path.join(tmpdir, 'MTLibrary.sqlite-shm'))
        shm_size = os.path.getsize(os.path.join(tmpdir, 'MTLibrary.sqlite-shm'))
        print(f'SHM sidecar size: {shm_size:,} bytes')
    except Exception as e:
        print(f'SHM not found: {e}')

    # Query the DB
    print('Querying merged DB...')
    db_path = os.path.join(tmpdir, 'MTLibrary.sqlite')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL")
    count = cur.fetchone()[0]
    print(f'Episodes with ZLASTDATEPLAYED: {count}')

    cur.execute("""SELECT MIN(ZLASTDATEPLAYED), MAX(ZLASTDATEPLAYED)
                   FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL""")
    mn, mx = cur.fetchone()
    if mn and mx:
        epoch_min = mn + 978307200
        epoch_max = mx + 978307200
        import datetime
        dt_min = datetime.datetime.utcfromtimestamp(epoch_min)
        dt_max = datetime.datetime.utcfromtimestamp(epoch_max)
        print(f'Date range: {dt_min.strftime("%Y-%m-%d")} to {dt_max.strftime("%Y-%m-%d")}')
    else:
        print('No dates found')

    # Show most recent 5
    cur.execute("""SELECT ZLASTDATEPLAYED FROM ZMTEPISODE
                   WHERE ZLASTDATEPLAYED IS NOT NULL
                   ORDER BY ZLASTDATEPLAYED DESC LIMIT 5""")
    print('Most recent timestamps (raw Apple epoch):')
    for row in cur.fetchall():
        ts = row[0] + 978307200
        dt = datetime.datetime.utcfromtimestamp(ts)
        print(f'  {dt.strftime("%Y-%m-%d %H:%M")}')

    conn.close()

except Exception as e:
    import traceback
    print(f'ERROR: {e}')
    traceback.print_exc()
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
    print('Done.')
"@

    & $PythonPath -c $diagScript
}

Write-Host "[$Machine] v37 complete."
