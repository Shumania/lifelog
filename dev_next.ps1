# dev_next.ps1 - controlled by Tasklet agent
# Writes all output to stdout so LifeLog-DevLoop captures and posts it.

$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Output "FROM: $computer at $timestamp"

try {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) { $python = $pythonCmd.Source } else { $python = $null }
    if (-not $python) {
        Write-Output "Python not found. Please install from https://www.python.org/downloads/"
        exit 0
    }
    $pyVersion = & $python --version 2>&1
    Write-Output "Python: $pyVersion"

    # Install dependency quietly
    & $python -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null

    $script = @'
import os, sys, sqlite3, plistlib, tempfile
from iphone_backup_decrypt import EncryptedBackup, RelativePath, DomainLike

PASSWORD = '#ngrierBill70'

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

# Step 1: Unlock by extracting podcasts DB
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

# Step 2: Enumerate ALL com.google.Maps files
print('\n=== All files in AppDomain-com.google.Maps ===')
try:
    manifest_conn = getattr(backup, '_temp_manifest_db_conn', None)
    if not manifest_conn:
        manifest_path = getattr(backup, '_temp_decrypted_manifest_db_path', None)
        if manifest_path and os.path.exists(manifest_path):
            manifest_conn = sqlite3.connect(manifest_path)
            print(f'Connected directly to decrypted manifest: {manifest_path}')
    if manifest_conn:
        cur = manifest_conn.cursor()
        cur.execute("SELECT domain, relativePath FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR domain LIKE '%Maps%' ORDER BY domain, relativePath")
        rows = cur.fetchall()
        print(f'Found {len(rows)} Google/Maps files:')
        for domain, path in rows:
            print(f'  [{domain}] {path}')
    else:
        print('No manifest connection available - trying direct Manifest.db read')
        manifest_raw = os.path.join(backup_dir, 'Manifest.db')
        if os.path.exists(manifest_raw):
            try:
                conn2 = sqlite3.connect(manifest_raw)
                cur2 = conn2.cursor()
                cur2.execute("SELECT domain, relativePath FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR domain LIKE '%Maps%' ORDER BY domain, relativePath")
                rows2 = cur2.fetchall()
                print(f'Found {len(rows2)} Google/Maps files (from raw Manifest.db):')
                for domain, path in rows2:
                    print(f'  [{domain}] {path}')
            except Exception as e2:
                print(f'Raw manifest error: {e2}')
except Exception as e:
    print(f'Manifest error: {e}')

# Step 3: Extract and parse the plist
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
        print(f'Plist keys ({len(data)}):')
        for k, v in sorted(data.items()):
            print(f'  {k}: {str(v)[:120]}')
except Exception as e:
    print(f'Plist error: {e}')

print('\nDone!')
'@

    $tmpPy = "$env:TEMP\inspect_maps2.py"
    $script | Set-Content -Path $tmpPy -Encoding UTF8
    $output = & $python $tmpPy 2>&1 | Out-String
    Write-Output $output

} catch {
    Write-Output "ERROR: $_"
}
