$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

$pythonExe = $null
$cmd = Get-Command python -ErrorAction SilentlyContinue
if ($cmd) { $pythonExe = $cmd.Source }
if (-not $pythonExe) {
    $cmd3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($cmd3) { $pythonExe = $cmd3.Source }
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
if ($backupDir) {
    $output += "Backup: $($backupDir.FullName)`n"
} else {
    $output += "Backup: NOT FOUND`n"
}

if (-not $pythonExe -or -not $backupDir) {
    $output += "ERROR: Missing Python or backup directory`n"
} else {
    $pyScript = @"
import sys, os, traceback
sys.path.insert(0, r'C:\ProgramData\LifeLog')
try:
    import iphone_backup_decrypt
except ImportError:
    import subprocess
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'iphone-backup-decrypt', '--quiet'])
    import iphone_backup_decrypt

backup_path = sys.argv[1]
password = sys.argv[2]
print('Backup path: ' + backup_path)
print('Password length: ' + str(len(password)))

try:
    backup = iphone_backup_decrypt.EncryptedBackup(backup_directory=backup_path, passphrase=password)
    print('Backup object created OK')
except Exception as e:
    print('Backup creation failed: ' + str(e))
    traceback.print_exc()
    sys.exit(1)

try:
    import tempfile
    tmp = tempfile.mkdtemp()
    backup.extract_file(
        relative_name='Library/Application Support/com.apple.podcasts/Documents/MTLibrary.sqlite',
        output_filename=os.path.join(tmp, 'podcasts.sqlite')
    )
    print('Podcasts DB extracted OK - backup is unlocked')
except Exception as e:
    print('Podcasts unlock failed: ' + str(e))
    traceback.print_exc()

try:
    conn = backup._manifest_db_conn
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT fileID, domain, relativePath FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR relativePath LIKE '%google%' OR relativePath LIKE '%timeline%' OR relativePath LIKE '%Maps%' LIMIT 50")
        rows = cur.fetchall()
        print('Google Maps manifest entries: ' + str(len(rows)))
        for r in rows:
            print('  ' + r[0][:8] + '... | ' + str(r[1]) + ' | ' + str(r[2]))
        cur.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
        domains = [r[0] for r in cur.fetchall()]
        print('All domains (' + str(len(domains)) + ' total):')
        for d in domains:
            print('  ' + str(d))
    else:
        print('manifest_db_conn is None')
except Exception as e:
    print('Manifest access failed: ' + str(e))
    traceback.print_exc()
"@

    $output += ($pyScript | & $pythonExe -u - $backupDir.FullName '#ngrierBill70' 2>&1 | Out-String)
}

$body = @{ output = $output; timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); computer = $env:COMPUTERNAME; source = 'LifeLog-DevLoop' } | ConvertTo-Json
Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json' | Out-Null
Write-Host "Sent! Output length: $($output.Length)"
