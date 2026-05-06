$WEBHOOK = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:MM:ss"

function Send-Output($text) {
    $body = @{ log = $text; exitCode = 0; computer = $computer; timestamp = $timestamp } | ConvertTo-Json
    Invoke-RestMethod -Uri $WEBHOOK -Method Post -Body $body -ContentType "application/json" | Out-Null
}

try {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) { $python = $pythonCmd.Source } else { $python = $null }
    if (-not $python) {
        Send-Output "Python not found. Please install from https://www.python.org/downloads/"
        exit 0
    }

    $pyVersion = & $python --version 2>&1
    & $python -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null

    $script = @'
import os, sys, sqlite3, plistlib, tempfile
from iphone_backup_decrypt import EncryptedBackup, RelativePath, DomainLike

PASSWORD = '#ngrierBill70'
results = []

def find_backup():
    for base in [
        os.path.join(os.environ.get('USERPROFILE',''), 'Apple', 'MobileSync', 'Backup'),
        os.path.join(os.environ.get('USERPROFILE',''), 'AppData', 'Roaming', 'Apple Computer', 'MobileSync', 'Backup'),
    ]:
        if os.path.isdir(base):
            backups = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
            if backups:
                backups.sort(key=lambda d: os.path.getmtime(os.path.join(base, d)), reverse=True)
                return os.path.join(base, backups[0])
    return None

backup_dir = find_backup()
if not backup_dir:
    print('ERROR: No backup found')
    sys.exit(1)

print(f'Backup: {backup_dir}')

# Step 1: Unlock by extracting a known file
print('\n=== Unlocking backup ===')
try:
    backup = EncryptedBackup(backup_directory=backup_dir, passphrase=PASSWORD)
    with tempfile.TemporaryDirectory() as tmpdir:
        backup.extract_file(
            relative_path='Library/Database/MTLibrary.sqlite',
            domain_like='AppDomainGroup-243LU875E5.groups.com.apple.podcasts',
            output_filename=os.path.join(tmpdir, 'unlock_test.db')
        )
    print(f'Unlocked! _unlocked={backup._unlocked}')
except Exception as e:
    print(f'Unlock error: {e}')

# Step 2: Enumerate ALL com.google.Maps files from manifest
print('\n=== All files in AppDomain-com.google.Maps ===')
try:
    manifest_conn = backup._temp_manifest_db_conn
    if not manifest_conn:
        # Try direct connection to decrypted manifest
        manifest_path = backup._temp_decrypted_manifest_db_path
        if os.path.exists(manifest_path):
            manifest_conn = sqlite3.connect(manifest_path)
            print(f'Connected directly to: {manifest_path}')
    
    if manifest_conn:
        cur = manifest_conn.cursor()
        cur.execute("SELECT domain, relativePath, flags, file FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' ORDER BY domain, relativePath")
        rows = cur.fetchall()
        print(f'Found {len(rows)} Google-related files:')
        for domain, path, flags, _ in rows:
            print(f'  [{domain}] {path}')
    else:
        print('No manifest connection available after unlock')
except Exception as e:
    print(f'Manifest error: {e}')

# Step 3: Extract and parse the plist we know exists
print('\n=== Parsing com.google.Maps.plist ===')
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, 'maps.plist')
        backup.extract_file(
            relative_path='Library/Preferences/com.google.Maps.plist',
            domain_like='AppDomain-com.google.Maps',
            output_filename=out
        )
        with open(out, 'rb') as f:
            data = plistlib.load(f)
        # Print all keys
        print(f'Plist keys ({len(data)}):')
        for k, v in sorted(data.items()):
            val_str = str(v)[:120]
            print(f'  {k}: {val_str}')
except Exception as e:
    print(f'Plist error: {e}')

print('\nDone!')
'@

    $tmpPy = "$env:TEMP\inspect_maps2.py"
    $script | Set-Content -Path $tmpPy -Encoding UTF8
    $output = & $python $tmpPy 2>&1 | Out-String
    Send-Output "$pyVersion`n`n$output"
} catch {
    Send-Output "ERROR: $_"
}
