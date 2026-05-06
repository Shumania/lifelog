$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

$pythonExe = (Get-Command python -ErrorAction SilentlyContinue)?.Source
if (-not $pythonExe) {
    $pythonExe = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source
}
if (-not $pythonExe) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\ProgramData\LifeLog\python\python.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { $pythonExe = $c; break } }
}

$output = "Python: $pythonExe`n"

# Find backup
$backupRoot = "$env:USERPROFILE\Apple\MobileSync\Backup"
if (-not (Test-Path $backupRoot)) {
    $backupRoot = "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup"
}
$backupDir = Get-ChildItem $backupRoot -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$output += "Backup: $($backupDir?.FullName)`n"

if (-not $pythonExe -or -not $backupDir) {
    $output += "ERROR: Missing Python or backup directory`n"
} else {
    $pyScript = @"
import sys, os, traceback
sys.path.insert(0, r'C:\ProgramData\LifeLog')
try:
    import iphone_backup_decrypt
except ImportError:
    # Try pip install
    import subprocess
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'iphone-backup-decrypt', '--quiet'])
    import iphone_backup_decrypt

backup_path = sys.argv[1]
password = sys.argv[2]
print(f'Backup path: {backup_path}')
print(f'Password length: {len(password)}')

try:
    backup = iphone_backup_decrypt.EncryptedBackup(backup_directory=backup_path, passphrase=password)
    print('Backup object created OK')
except Exception as e:
    print(f'Backup creation failed: {e}')
    traceback.print_exc()
    sys.exit(1)

# Try to unlock by extracting podcasts DB
try:
    import tempfile, pathlib
    tmp = tempfile.mkdtemp()
    backup.extract_file(
        relative_name='Library/Application Support/com.apple.podcasts/Documents/MTLibrary.sqlite',
        output_filename=os.path.join(tmp, 'podcasts.sqlite')
    )
    print('Podcasts DB extracted OK - backup is unlocked')
except Exception as e:
    print(f'Podcasts unlock failed: {e}')
    traceback.print_exc()

# Try to access manifest DB
try:
    conn = backup._manifest_db_conn
    if conn:
        cur = conn.cursor()
        # Look for Google Maps / Timeline files
        cur.execute("SELECT fileID, domain, relativePath FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR relativePath LIKE '%google%' OR relativePath LIKE '%timeline%' OR relativePath LIKE '%Maps%' LIMIT 50")
        rows = cur.fetchall()
        print(f'Google Maps manifest entries: {len(rows)}')
        for r in rows:
            print(f'  {r[0][:8]}... | {r[1]} | {r[2]}')
        # Also show all domains for reference
        cur.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
        domains = [r[0] for r in cur.fetchall()]
        print(f'\nAll domains ({len(domains)} total):')
        for d in domains:
            print(f'  {d}')
    else:
        print('manifest_db_conn is None')
except Exception as e:
    print(f'Manifest access failed: {e}')
    traceback.print_exc()
"@

    $output += ($pyScript | & $pythonExe -u - $backupDir.FullName '#ngrierBill70' 2>&1 | Out-String)
}

$body = @{ output = $output; timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); computer = $env:COMPUTERNAME; source = 'LifeLog-DevLoop' } | ConvertTo-Json
Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json' | Out-Null
Write-Host "Sent! Output length: $($output.Length)"
