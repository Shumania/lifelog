$computer = $env:COMPUTERNAME
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Write-Host "=== DEV_NEXT START on $computer at $ts ==="

try {
    $python = (Get-Command python -ErrorAction Stop).Source
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
import sys, os, sqlite3, tempfile
from iphone_backup_decrypt import EncryptedBackup

backup_path = r'$backupDir'
password = '#ngrierBill70'

print(f'Backup path: {backup_path}')

# Correct API: backup_directory=, passphrase=
backup = EncryptedBackup(backup_directory=backup_path, passphrase=password)
print('EncryptedBackup created OK')

# Force unlock by extracting podcasts DB using correct arg: relative_path=
tmp = tempfile.mkdtemp()
try:
    backup.extract_file(
        relative_path='Library/Application Support/CrashReporter/MTLibrary.sqlite',
        domain_like='AppDomainGroup-243LU875E5.groups.com.apple.podcasts',
        output_filename=os.path.join(tmp, 'podcasts_test.sqlite')
    )
    print('Podcasts DB extracted OK - backup unlocked')
except Exception as e:
    print(f'WARNING extracting podcasts DB: {e}')

# Get decrypted manifest path
manifest_db = getattr(backup, '_temp_decrypted_manifest_db_path', None)
print(f'Manifest DB path: {manifest_db}')
print(f'Manifest DB exists: {os.path.exists(manifest_db) if manifest_db else False}')

if manifest_db and os.path.exists(manifest_db):
    conn = sqlite3.connect(manifest_db)
    cur = conn.cursor()

    print('\n=== TOP DOMAINS IN MANIFEST ===')
    cur.execute('SELECT domain, COUNT(*) as cnt FROM Files GROUP BY domain ORDER BY cnt DESC LIMIT 40')
    for row in cur.fetchall():
        print(f'  {row[1]:5d}  {row[0]}')

    print('\n=== GOOGLE/MAPS/LOCATION FILES ===')
    cur.execute("""SELECT domain, relativePath FROM Files
        WHERE domain LIKE '%google%' OR domain LIKE '%maps%'
           OR relativePath LIKE '%maps%' OR relativePath LIKE '%timeline%'
           OR relativePath LIKE '%location%' OR relativePath LIKE '%Location%'
        ORDER BY domain, relativePath""")
    rows = cur.fetchall()
    print(f'Found {len(rows)} matching files:')
    for row in rows:
        print(f'  [{row[0]}] {row[1]}')
    conn.close()
else:
    print('ERROR: Manifest not available')
"@

    $tmpScript = [System.IO.Path]::GetTempFileName() + '.py'
    $script | Out-File -FilePath $tmpScript -Encoding utf8
    & $python $tmpScript 2>&1
    Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue

} catch {
    Write-Host "ERROR: $_"
}

Write-Host "=== DEV_NEXT END ==="
