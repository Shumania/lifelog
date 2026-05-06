$WEBHOOK = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

try {
    $python = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $python) { throw "Python not found in PATH" }

    # Find backup
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

    $script = @"
import sys, os, json, sqlite3, tempfile
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
    print('Podcasts DB extracted - backup is now unlocked')
except Exception as e:
    print(f'WARNING: Could not extract podcasts DB: {e}')

# Now query the manifest DB directly
try:
    manifest_db = os.path.join(backup_path, 'Manifest.db')
    conn = sqlite3.connect(manifest_db)
    cur = conn.cursor()
    
    # Get ALL domains and file counts
    print('\n=== ALL DOMAINS IN MANIFEST ===')
    cur.execute('SELECT domain, COUNT(*) as cnt FROM Files GROUP BY domain ORDER BY cnt DESC LIMIT 30')
    for row in cur.fetchall():
        print(f'  {row[1]:5d} files: {row[0]}')
    
    # Search for Google/Maps related
    print('\n=== GOOGLE/MAPS FILES ===')
    cur.execute("SELECT domain, relativePath, flags, file FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR relativePath LIKE '%maps%' OR relativePath LIKE '%timeline%' OR relativePath LIKE '%location%' ORDER BY domain, relativePath")
    rows = cur.fetchall()
    print(f'Found {len(rows)} Google/Maps/Location related files:')
    for row in rows:
        domain, path, flags, filedata = row
        size_info = f'{len(filedata)} bytes metadata' if filedata else 'no metadata'
        print(f'  [{domain}] {path} (flags={flags}, {size_info})')
    
    conn.close()
except Exception as e:
    print(f'ERROR querying manifest: {e}')
    import traceback
    traceback.print_exc()
"@

    $tmpScript = [System.IO.Path]::GetTempFileName() + '.py'
    $script | Out-File -FilePath $tmpScript -Encoding utf8
    $output = & $python $tmpScript 2>&1 | Out-String
    Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue

    $body = @{ log = "FROM: $computer at $ts`n`n$output"; exitCode = 0 } | ConvertTo-Json
} catch {
    $body = @{ log = "FROM: $computer at $ts`n`nERROR: $_"; exitCode = 1 } | ConvertTo-Json
}

Invoke-RestMethod -Uri $WEBHOOK -Method POST -Body $body -ContentType 'application/json'
