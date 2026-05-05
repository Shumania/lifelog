$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computerName = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

try {
    # Find Python (PS5 compatible - no ?. operator) v4
    $pythonExe = $null
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $pythonExe = $cmd.Source }
    if (-not $pythonExe) {
        $cmd3 = Get-Command python3 -ErrorAction SilentlyContinue
        if ($cmd3) { $pythonExe = $cmd3.Source }
    }
    if (-not $pythonExe) {
        $p313 = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
        $p312 = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
        $p311 = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
        if (Test-Path $p313) { $pythonExe = $p313 }
        elseif (Test-Path $p312) { $pythonExe = $p312 }
        elseif (Test-Path $p311) { $pythonExe = $p311 }
    }
    if (-not $pythonExe) { throw "Python not found. Install from https://www.python.org/downloads/ (check 'Add to PATH')" }

    # Install required packages
    Write-Host "Installing dependencies..."
    & $pythonExe -m pip install iphone_backup_decrypt --quiet --disable-pip-version-check 2>&1 | Out-Null

    # Find backup
    $backupRoots = @(
        "$env:USERPROFILE\Apple\MobileSync\Backup",
        "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup"
    )
    $backupDir = $null
    foreach ($root in $backupRoots) {
        if (Test-Path $root) {
            $latest = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($latest) { $backupDir = $latest.FullName; break }
        }
    }
    if (-not $backupDir) { throw "No iPhone backup found" }

    $pyScript = @"
import sys, os, tempfile
from iphone_backup_decrypt import EncryptedBackup

backup_path = r'$backupDir'
password = '#ngrierBill70'

print('Python: ' + sys.executable)
print('Backup: ' + backup_path)

# Correct constructor: backup_directory
backup = EncryptedBackup(backup_directory=backup_path, passphrase=password)

# Unlock by extracting a known-good file first
print('Unlocking backup...')
try:
    tmp = tempfile.mktemp(suffix='.plist')
    backup.extract_file(
        relative_name='Library/Application Support/CrashReporter/DiagnosticMessagesHistory.plist',
        output_filename=tmp
    )
    if os.path.exists(tmp): os.remove(tmp)
    print('Unlock OK')
except Exception as e:
    print('Unlock probe: ' + str(e))

print('Querying manifest...')
with backup.manifest_db_cursor() as cursor:
    cursor.execute('SELECT COUNT(*) FROM Files')
    total = cursor.fetchone()[0]
    print('Total files in manifest: ' + str(total))

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
    print('Google/Maps related files: ' + str(len(rows)))
    print()

    domains = {}
    for domain, relpath, flags in rows:
        if domain not in domains:
            domains[domain] = []
        domains[domain].append(relpath)

    for domain, paths in sorted(domains.items()):
        print('DOMAIN: ' + domain + ' (' + str(len(paths)) + ' files)')
        for p in paths[:20]:
            print('  ' + p)
        if len(paths) > 20:
            print('  ... and ' + str(len(paths)-20) + ' more')
        print()

    cursor.execute('''SELECT domain, relativePath
                      FROM Files
                      WHERE (domain LIKE "%google%" OR domain LIKE "%maps%")
                        AND (relativePath LIKE "%.sqlite" OR relativePath LIKE "%.db")
                      ORDER BY domain, relativePath''')
    db_rows = cursor.fetchall()
    print('SQLite/DB files in Google domains: ' + str(len(db_rows)))
    for d, p in db_rows:
        print('  [' + d + '] ' + p)

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
