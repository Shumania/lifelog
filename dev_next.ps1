$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computerName = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
$pythonExe = if ($pythonCmd) { $pythonCmd.Source } else { $null }
if (-not $pythonExe) {
    $pythonCmd3 = Get-Command python3 -ErrorAction SilentlyContinue
    $pythonExe = if ($pythonCmd3) { $pythonCmd3.Source } else { $null }
}

if (-not $pythonExe) {
    $output = "FROM: $computerName at $timestamp`n`nERROR: Python not found in PATH. Please install Python from https://python.org and check 'Add to PATH'."
} else {
    # Find most recent backup
    $backupRoots = @(
        "$env:USERPROFILE\Apple\MobileSync\Backup",
        "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup"
    )
    $backupDir = $null
    foreach ($root in $backupRoots) {
        if (Test-Path $root) {
            $latest = Get-ChildItem $root -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($latest) { $backupDir = $latest.FullName; break }
        }
    }

    if (-not $backupDir) {
        $output = "FROM: $computerName at $timestamp`n`nERROR: No iPhone backup found."
    } else {
        $pyScript = @"
import sys
print('Python:', sys.executable)
print('Backup:', r'$backupDir')

try:
    from iphone_backup_decrypt import EncryptedBackup
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'iphone-backup-decrypt', '-q'])
    from iphone_backup_decrypt import EncryptedBackup

try:
    backup = EncryptedBackup(backup_directory=r'$backupDir', passphrase='#ngrierBill70')
    print('Backup object created OK')
except Exception as e:
    print(f'ERROR creating backup: {e}')
    sys.exit(1)

# Unlock by extracting podcasts DB
try:
    import tempfile, os
    tmp = tempfile.mkdtemp()
    out = backup.extract_file(
        relative_path='Library/Application Support/com.apple.podcasts/MTLibrary.sqlite',
        output_filename=os.path.join(tmp, 'MTLibrary.sqlite')
    )
    print(f'Unlock via podcasts DB: {out}')
except Exception as e:
    print(f'Podcasts unlock failed: {e}')

# Now query manifest for Google Maps files
try:
    import sqlite3
    manifest_conn = getattr(backup, '_manifest_db_conn', None)
    if manifest_conn is None:
        import os
        manifest_path = os.path.join(r'$backupDir', 'Manifest.db')
        if os.path.exists(manifest_path):
            manifest_conn = sqlite3.connect(manifest_path)
            print('Using unencrypted Manifest.db directly')
        else:
            print('No manifest connection available')

    if manifest_conn:
        cur = manifest_conn.cursor()
        cur.execute("SELECT fileID, domain, relativePath FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR domain LIKE '%Google%' OR relativePath LIKE '%google%' OR relativePath LIKE '%Maps%' OR relativePath LIKE '%timeline%' OR relativePath LIKE '%Timeline%' ORDER BY domain, relativePath LIMIT 100")
        rows = cur.fetchall()
        print(f'\nFound {len(rows)} Google/Maps related files:')
        for row in rows:
            print(f'  domain={row[1]} | path={row[2]} | id={row[0][:8]}...')
        if not rows:
            cur.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
            domains = [r[0] for r in cur.fetchall()]
            print(f'\nNo Google/Maps files found. All {len(domains)} domains in backup:')
            for d in domains:
                print(f'  {d}')
except Exception as e:
    print(f'Manifest query error: {e}')
    import traceback
    traceback.print_exc()
"@

        $output = "FROM: $computerName at $timestamp`n`n"
        $output += ($pyScript | & $pythonExe -u - 2>&1 | Out-String)
    }
}

$body = @{ output = $output; timestamp = $timestamp; computer = $computerName; source = 'LifeLog-DevLoop' } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json' | Out-Null
Write-Host $output
