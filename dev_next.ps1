$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

$py = @"
import sys, os, json, tempfile, sqlite3
from pathlib import Path

try:
    from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike
except ImportError as e:
    print(f'IMPORT ERROR: {e}')
    sys.exit(1)

# Find backup
backup_root = Path(os.environ.get('USERPROFILE', '')) / 'Apple' / 'MobileSync' / 'Backup'
if not backup_root.exists():
    backup_root = Path(os.environ.get('APPDATA', '')) / 'Apple Computer' / 'MobileSync' / 'Backup'

if not backup_root.exists():
    print('ERROR: No backup root found')
    sys.exit(1)

backups = sorted([d for d in backup_root.iterdir() if d.is_dir()], key=lambda d: d.stat().st_mtime, reverse=True)
if not backups:
    print('ERROR: No backups found')
    sys.exit(1)

backup_path = str(backups[0])
print(f'Backup: {backup_path}')

password = '#ngrierBill70'

try:
    backup = EncryptedBackup(backup_directory=backup_path, passphrase=password)
except Exception as e:
    print(f'ERROR creating backup object: {e}')
    sys.exit(1)

# UNLOCK: extract a known file to trigger decryption
print('Unlocking backup...')
tmpdir = tempfile.mkdtemp()
try:
    backup.extract_file(
        relative_name='Library/Preferences/com.apple.springboard.plist',
        output_filename=os.path.join(tmpdir, 'unlock_test.plist')
    )
    print('Unlock succeeded via springboard.plist')
except Exception as e:
    print(f'Springboard unlock failed: {e}, trying podcasts DB...')
    try:
        backup.extract_file(
            relative_name='Library/Application Support/Podcasts/MTLibrary.sqlite',
            domain='AppDomainGroup-243LU875E5.groups.com.apple.podcasts',
            output_filename=os.path.join(tmpdir, 'podcasts.sqlite')
        )
        print('Unlock succeeded via podcasts DB')
    except Exception as e2:
        print(f'Podcasts unlock also failed: {e2}')

print(f'_unlocked = {backup._unlocked}')

# Now enumerate ALL Google Maps domain files
print('\n=== QUERYING MANIFEST FOR GOOGLE MAPS FILES ===')
try:
    # Try manifest DB
    manifest_db = Path(backup_path) / 'Manifest.db'
    conn = sqlite3.connect(str(manifest_db))
    cur = conn.cursor()
    
    # Get all domains first
    cur.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
    domains = [r[0] for r in cur.fetchall()]
    google_domains = [d for d in domains if d and 'google' in d.lower()]
    print(f'Google-related domains: {google_domains}')
    
    # Get all files in Google Maps domains
    for domain in google_domains:
        cur.execute("SELECT relativePath, fileID, flags FROM Files WHERE domain = ? ORDER BY relativePath", (domain,))
        rows = cur.fetchall()
        print(f'\nDomain: {domain} ({len(rows)} files)')
        for path, fid, flags in rows[:50]:
            print(f'  {path} [{fid[:8] if fid else "none"}]')
        if len(rows) > 50:
            print(f'  ... and {len(rows)-50} more')
    
    conn.close()
except Exception as e:
    print(f'Manifest DB query failed: {e}')
    # Try via backup object if manifest DB didn't work
    print('Trying backup._manifest_db_conn...')
    try:
        cur = backup._manifest_db_conn.cursor()
        cur.execute("SELECT DISTINCT domain FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' ORDER BY domain")
        domains = [r[0] for r in cur.fetchall()]
        print(f'Google/Maps domains via object: {domains}')
        for domain in domains:
            cur.execute("SELECT relativePath, fileID FROM Files WHERE domain = ? ORDER BY relativePath", (domain,))
            rows = cur.fetchall()
            print(f'Domain {domain}: {len(rows)} files')
            for path, fid in rows[:30]:
                print(f'  {path}')
    except Exception as e2:
        print(f'Object manifest also failed: {e2}')

print('\nDone.')
"@

try {
    $pyPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $pyPath) { $pyPath = (Get-Command python3 -ErrorAction SilentlyContinue).Source }
    if (-not $pyPath) { throw 'Python not found in PATH' }
    
    Write-Host "Python: $pyPath"
    $scriptFile = [System.IO.Path]::GetTempFileName() + '.py'
    $py | Set-Content -Path $scriptFile -Encoding UTF8
    $output = & $pyPath $scriptFile 2>&1 | Out-String
    Remove-Item $scriptFile -ErrorAction SilentlyContinue
} catch {
    $output = "ERROR: $_"
}

$body = @{ log = "FROM: $computer at $timestamp`n`n$output"; exitCode = 0 } | ConvertTo-Json
Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType 'application/json'
Write-Host "Sent at $timestamp"
