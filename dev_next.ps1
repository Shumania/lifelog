$computer = $env:COMPUTERNAME
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Write-Host "=== DEV_NEXT START on $computer at $ts ==="

try {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) { throw "Python not found in PATH" }
    $python = $pythonCmd.Source
    Write-Host "Python: $(& $python --version 2>&1)"

    # Find most recent backup
    $backupRoots = @(
        "$env:USERPROFILE\Apple\MobileSync\Backup",
        "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup"
    )
    $backupDir = $null
    foreach ($root in $backupRoots) {
        if (Test-Path $root) {
            $found = Get-ChildItem $root -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($found) { $backupDir = $found.FullName; break }
        }
    }
    if (-not $backupDir) { throw "No backup directory found" }
    Write-Host "Backup: $backupDir"

    $script = @"
import sys, os, sqlite3, tempfile, inspect
from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike

backup_path = r'$backupDir'
password = '#ngrierBill70'

print(f'Backup path: {backup_path}')

# Check constructor signature
sig = inspect.signature(EncryptedBackup.__init__)
print(f'Constructor signature: {sig}')

# Try multiple constructor signatures
backup = None
for attempt in [
    lambda: EncryptedBackup(backup_path=backup_path, passphrase=password),
    lambda: EncryptedBackup(backup_directory=backup_path, passphrase=password),
    lambda: EncryptedBackup(backup_path, password),
]:
    try:
        backup = attempt()
        print('EncryptedBackup created OK')
        break
    except TypeError as e:
        print(f'  Attempt failed: {e}')

if backup is None:
    print('ERROR: Could not create EncryptedBackup with any signature')
    sys.exit(1)

# Force unlock by extracting podcasts DB
tmp = tempfile.mkdtemp()
try:
    # Try new API first, then old
    try:
        backup.extract_file(
            relative_name='Library/Application Support/CrashReporter/MTLibrary.sqlite',
            domain_like='AppDomainGroup-243LU875E5.groups.com.apple.podcasts',
            output_filename=os.path.join(tmp, 'podcasts_test.sqlite')
        )
    except TypeError:
        backup.extract_file(
            relative_name='Library/Application Support/CrashReporter/MTLibrary.sqlite',
            output_filename=os.path.join(tmp, 'podcasts_test.sqlite')
        )
    print('Podcasts DB extracted OK - backup unlocked')
except Exception as e:
    print(f'WARNING extracting podcasts DB: {e}')

# Find decrypted Manifest.db in temp dir
manifest_db = None
for attr in dir(backup):
    val = getattr(backup, attr, None)
    if val and isinstance(val, str) and 'manifest' in val.lower():
        print(f'  backup.{attr} = {val}')
        if os.path.exists(val):
            manifest_db = val

# Also search temp dirs
import glob
for pattern in [os.path.join(tempfile.gettempdir(), '**', 'Manifest.db'), os.path.join(tmp, '**', '*.db')]:
    for f in glob.glob(pattern, recursive=True):
        print(f'  Found in temp: {f}')
        if 'manifest' in f.lower() and manifest_db is None:
            manifest_db = f

# Also try unencrypted manifest directly
raw_manifest = os.path.join(backup_path, 'Manifest.db')
if os.path.exists(raw_manifest):
    try:
        conn = sqlite3.connect(raw_manifest)
        conn.execute('SELECT 1 FROM Files LIMIT 1')
        print(f'Raw Manifest.db is readable (unencrypted backup?)')
        manifest_db = raw_manifest
        conn.close()
    except:
        print(f'Raw Manifest.db exists but is encrypted')

if manifest_db:
    print(f'\nQuerying manifest: {manifest_db}')
    try:
        conn = sqlite3.connect(manifest_db)
        cur = conn.cursor()

        print('\n=== TOP DOMAINS IN MANIFEST ===')
        cur.execute('SELECT domain, COUNT(*) as cnt FROM Files GROUP BY domain ORDER BY cnt DESC LIMIT 30')
        for row in cur.fetchall():
            print(f'  {row[1]:5d} files: {row[0]}')

        print('\n=== GOOGLE/MAPS/LOCATION FILES ===')
        cur.execute("SELECT domain, relativePath FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR relativePath LIKE '%maps%' OR relativePath LIKE '%timeline%' OR relativePath LIKE '%location%' ORDER BY domain, relativePath")
        rows = cur.fetchall()
        print(f'Found {len(rows)} files:')
        for row in rows:
            print(f'  [{row[0]}] {row[1]}')
        conn.close()
    except Exception as e:
        import traceback
        print(f'ERROR querying manifest: {e}')
        traceback.print_exc()
else:
    print('ERROR: Could not find decrypted Manifest.db')
"@

    $tmpScript = [System.IO.Path]::GetTempFileName() + '.py'
    $script | Out-File -FilePath $tmpScript -Encoding utf8
    & $python $tmpScript 2>&1
    Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue

} catch {
    Write-Host "ERROR: $_"
}

Write-Host "=== DEV_NEXT END ==="
