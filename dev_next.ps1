$webhookUrl = 'https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5'
$computer = $env:COMPUTERNAME
$timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

function Send-Output($output) {
    $body = @{ output = $output; timestamp = $timestamp; computer = $computer; source = 'LifeLog-DevLoop' } | ConvertTo-Json -Depth 3
    try { Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType 'application/json' | Out-Null } catch {}
}

# Find Python
$pythonExe = (Get-Command python -ErrorAction SilentlyContinue)?.Source
if (-not $pythonExe) { $pythonExe = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source }
if (-not $pythonExe) { Send-Output "ERROR: Python not found on $computer"; exit 1 }

# Find backup dir (most recently modified)
$searchPaths = @(
    "$env:USERPROFILE\Apple\MobileSync\Backup",
    "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup"
)
$backupDir = $null
foreach ($p in $searchPaths) {
    if (Test-Path $p) {
        $backupDir = Get-ChildItem $p -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($backupDir) { break }
    }
}
if (-not $backupDir) { Send-Output "ERROR: No backup directory found on $computer"; exit 1 }

$pyScript = @'
import sys, sqlite3, traceback
try:
    from iphone_backup_decrypt import EncryptedBackup
except ImportError as e:
    print(f'ImportError: {e}')
    sys.exit(1)

backup_dir = sys.argv[1]
password = sys.argv[2]
print(f'Python: {sys.executable}')
print(f'Backup: {backup_dir}')

try:
    backup = EncryptedBackup(backup_directory=backup_dir, passphrase=password)
    print('Backup object created OK')
except Exception as e:
    print(f'Backup creation failed: {e}')
    traceback.print_exc()
    sys.exit(1)

# Decrypt the manifest DB
try:
    backup._decrypt_manifest_db_file()
    print('Manifest decrypted OK')
except Exception as e:
    print(f'Manifest decrypt failed: {e}')
    traceback.print_exc()

# Query manifest for all domains and Google Maps files
try:
    cursor = backup.manifest_db_cursor()
    print('Got manifest cursor OK')

    # All unique domains
    cursor.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
    domains = [r[0] for r in cursor.fetchall()]
    print(f'\nTotal domains: {len(domains)}')
    print('\n--- Domains matching google/maps/location/geo ---')
    matches = [d for d in domains if any(k in (d or '').lower() for k in ['google','maps','location','geo','timeline'])]
    if matches:
        for d in matches:
            print(f'  {d}')
    else:
        print('  (none matched)')
        print('\n--- All domains (for reference) ---')
        for d in domains:
            print(f'  {d}')

    # Google Maps specific files
    print('\n--- Files in Google Maps domains ---')
    cursor.execute("""
        SELECT domain, relativePath, flags
        FROM Files
        WHERE domain LIKE '%google%' OR domain LIKE '%maps%'
           OR relativePath LIKE '%google%maps%'
           OR relativePath LIKE '%Timeline%'
        ORDER BY domain, relativePath
    """)
    rows = cursor.fetchall()
    if rows:
        for r in rows:
            print(f'  [{r[0]}] {r[1]}')
    else:
        print('  (none found)')

except Exception as e:
    print(f'Cursor query failed: {e}')
    traceback.print_exc()

print('\nDone.')
'@

$output = $pyScript | & $pythonExe -u - $backupDir.FullName '#ngrierBill70' 2>&1 | Out-String
Write-Host $output
Send-Output $output
