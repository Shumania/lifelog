$computer = $env:COMPUTERNAME
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Write-Host "=== DEV_NEXT START on $computer at $ts ==="

try {
    $python = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $python) { throw "Python not found in PATH" }
    Write-Host "Python: $(& $python --version 2>&1)"

    # Find most recent backup
    $backupRoots = @(
        "$env:USERPROFILE\Apple\MobileSync\Backup",
        "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup"
    )
    $backupDir = $null
    foreach ($root in $backupRoots) {
        if (Test-Path $root) {
            $backupDir = Get-ChildItem $root -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
            if ($backupDir) { break }
        }
    }
    if (-not $backupDir) { throw "No backup directory found" }
    Write-Host "Backup: $backupDir"

    $script = @"
import sys, os, sqlite3, tempfile
from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike

backup_path = r'$backupDir'
password = '#ngrierBill70'

print(f'Backup path: {backup_path}')

try:
    backup = EncryptedBackup(backup_path=backup_path, passphrase=password)
    print('EncryptedBackup created')
except Exception as e:
    print(f'ERROR creating backup: {e}')
    sys.exit(1)

# Force unlock by extracting podcasts DB
tmp = tempfile.mkdtemp()
try:
    backup.extract_file(
        relative_name='Library/Application Support/CrashReporter/MTLibrary.sqlite',
        domain_like='AppDomainGroup-243LU875E5.groups.com.apple.podcasts',
        output_filename=os.path.join(tmp, 'podcasts_test.sqlite')
    )
    print('Podcasts DB extracted OK - backup unlocked')
except Exception as e:
    print(f'WARNING extracting podcasts DB: {e}')

# Query Manifest.db directly
try:
    manifest_db = os.path.join(backup_path, 'Manifest.db')
    conn = sqlite3.connect(manifest_db)
    cur = conn.cursor()

    print('\n=== ALL DOMAINS IN MANIFEST ===')
    cur.execute('SELECT domain, COUNT(*) as cnt FROM Files GROUP BY domain ORDER BY cnt DESC LIMIT 30')
    for row in cur.fetchall():
        print(f'  {row[1]:5d} files: {row[0]}')

    print('\n=== GOOGLE/MAPS/LOCATION FILES ===')
    cur.execute("SELECT domain, relativePath, flags FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR relativePath LIKE '%maps%' OR relativePath LIKE '%timeline%' OR relativePath LIKE '%location%' ORDER BY domain, relativePath")
    rows = cur.fetchall()
    print(f'Found {len(rows)} files:')
    for row in rows:
        print(f'  [{row[0]}] {row[1]} (flags={row[2]})')

    conn.close()
except Exception as e:
    print(f'ERROR querying manifest: {e}')
    import traceback
    traceback.print_exc()
"@

    $tmpScript = [System.IO.Path]::GetTempFileName() + '.py'
    $script | Out-File -FilePath $tmpScript -Encoding utf8
    & $python $tmpScript 2>&1
    Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue

} catch {
    Write-Host "ERROR: $_"
}

Write-Host "=== DEV_NEXT END ==="
