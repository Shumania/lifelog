$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computerName = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

try {
    # Find Python
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $pythonExe) {
        $pythonExe = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source
    }
    if (-not $pythonExe) {
        $pythonExe = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
        if (-not (Test-Path $pythonExe)) { $pythonExe = $null }
    }

    if (-not $pythonExe) {
        throw "Python not found. Run Install-LifeLog.ps1 first."
    }

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
    if (-not $backupDir) { throw "No iPhone backup found" }

    $pyScript = @"
import sys, json
from iphone_backup_decrypt import EncryptedBackup, RelativePath

backup_path = r'$backupDir'
password = '#ngrierBill70'

print(f'Python: {sys.executable}')
print(f'Backup: {backup_path}')

backup = EncryptedBackup(backup_path=backup_path, passphrase=password)

# Unlock by extracting a known-good file first
print('Unlocking backup...')
try:
    import tempfile, os
    tmp = tempfile.mktemp(suffix='.sqlite')
    backup.extract_file(
        relative_name='Library/Application Support/CrashReporter/DiagnosticMessagesHistory.plist',
        output_filename=tmp
    )
    if os.path.exists(tmp): os.remove(tmp)
except Exception as e:
    print(f'Unlock probe failed (ok): {e}')

print('Querying manifest...')
# Use context manager correctly
with backup.manifest_db_cursor() as cursor:
    # Show total file count
    cursor.execute('SELECT COUNT(*) FROM Files')
    total = cursor.fetchone()[0]
    print(f'Total files in manifest: {total}')
    
    # Search for Google Maps related domains/files
    cursor.execute('''SELECT domain, relativePath, flags 
                      FROM Files 
                      WHERE domain LIKE "%google%" 
                         OR domain LIKE "%maps%"
                         OR relativePath LIKE "%google%"
                         OR relativePath LIKE "%maps%"
                         OR relativePath LIKE "%timeline%"
                         OR relativePath LIKE "%gmm%"
                      ORDER BY domain, relativePath
                      LIMIT 200''')
    rows = cursor.fetchall()
    print(f'Google/Maps related files: {len(rows)}')
    print()
    
    # Group by domain
    domains = {}
    for domain, relpath, flags in rows:
        if domain not in domains:
            domains[domain] = []
        domains[domain].append(relpath)
    
    for domain, paths in sorted(domains.items()):
        print(f'DOMAIN: {domain} ({len(paths)} files)')
        for p in paths[:20]:
            print(f'  {p}')
        if len(paths) > 20:
            print(f'  ... and {len(paths)-20} more')
        print()
    
    # Also check for any SQLite/DB files in Google domains
    cursor.execute('''SELECT domain, relativePath 
                      FROM Files 
                      WHERE (domain LIKE "%google%" OR domain LIKE "%maps%")
                        AND (relativePath LIKE "%.sqlite" OR relativePath LIKE "%.db")
                      ORDER BY domain, relativePath''')
    db_rows = cursor.fetchall()
    print(f'SQLite/DB files in Google domains: {len(db_rows)}')
    for d, p in db_rows:
        print(f'  [{d}] {p}')

print('Done!')
"@

    $output = "FROM: $computerName at $timestamp`r`n`r`n"
    $output += ($pyScript | & $pythonExe -u - 2>&1 | Out-String)

} catch {
    $output = "FROM: $computerName at $timestamp`r`nERROR: $_`r`n"
}

$body = @{ output = $output; timestamp = $timestamp; computer = $computerName; source = "LifeLog-DevLoop" } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json" | Out-Null
Write-Host "Sent!"
