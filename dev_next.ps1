$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

function Send-Output($text) {
    $body = @{ computer=$computer; timestamp=$timestamp; source="LifeLog-DevLoop"; output=$text } | ConvertTo-Json
    Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType "application/json" | Out-Null
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Send-Output "ERROR: Python not found on $computer."
    exit 1
}

python -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null

$pyScript = @'
import os, sys, tempfile, sqlite3, traceback

try:
    from iphone_backup_decrypt import EncryptedBackup, RelativePath, DomainLike
    import iphone_backup_decrypt
    print(f"iphone_backup_decrypt version: {iphone_backup_decrypt.__version__ if hasattr(iphone_backup_decrypt, '__version__') else 'unknown'}")
except ImportError as e:
    print(f"ERROR: iphone-backup-decrypt not installed: {e}")
    sys.exit(1)

# Find most recent backup
backup_root = None
for base in [
    os.path.join(os.environ.get("USERPROFILE",""), "Apple", "MobileSync", "Backup"),
    os.path.join(os.environ.get("USERPROFILE",""), "AppData", "Roaming", "Apple Computer", "MobileSync", "Backup"),
    os.path.join(os.environ.get("USERPROFILE",""), "AppData", "Roaming", "Apple", "MobileSync", "Backup"),
]:
    if os.path.isdir(base):
        candidates = [os.path.join(base,d) for d in os.listdir(base) if os.path.isdir(os.path.join(base,d))]
        if candidates:
            backup_root = max(candidates, key=lambda p: os.path.getmtime(p))
            break

if not backup_root:
    print("ERROR: No backup found")
    sys.exit(1)

print(f"Backup: {backup_root}")

backup = EncryptedBackup(backup_directory=backup_root, passphrase="#ngrierBill70")

# Print all attributes to understand the API
attrs = [a for a in dir(backup) if not a.startswith('__')]
print(f"\nBackup object attributes: {attrs}")

# Print manifest path
if hasattr(backup, '_manifest_db_path'):
    print(f"Manifest DB path: {backup._manifest_db_path}")
    print(f"Manifest DB exists: {os.path.exists(backup._manifest_db_path)}")

# Try to unlock by extracting podcasts DB (no domain_like)
with tempfile.TemporaryDirectory() as tmpdir:
    out = os.path.join(tmpdir, "podcasts.sqlite")
    try:
        backup.extract_file(
            relative_path="Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Documents/MTLibrary.sqlite",
            output_filename=out
        )
        size = os.path.getsize(out)
        print(f"\nPodcasts unlock: {size:,} bytes - OK")
    except Exception as e:
        print(f"\nPodcasts unlock failed: {type(e).__name__}: {e}")
        traceback.print_exc()

# Now try to query manifest using _manifest_db_path
print("\nQuerying manifest DB...")
try:
    manifest_path = backup._manifest_db_path
    conn = sqlite3.connect(manifest_path)
    try:
        domains = conn.execute("SELECT DISTINCT domain FROM Files ORDER BY domain").fetchall()
        print(f"Total domains: {len(domains)}")
        google_domains = [d[0] for d in domains if d[0] and ('google' in d[0].lower() or 'maps' in d[0].lower())]
        print(f"Google/Maps domains: {google_domains}")

        rows = conn.execute("""
            SELECT fileID, domain, relativePath
            FROM Files
            WHERE domain LIKE '%google%' OR domain LIKE '%maps%'
               OR relativePath LIKE '%timeline%' OR relativePath LIKE '%Maps%'
            ORDER BY domain, relativePath
            LIMIT 100
        """).fetchall()
        print(f"Google/Maps files: {len(rows)}")
        for fileID, domain, relPath in rows:
            print(f"  [{domain}] {relPath}")
    except sqlite3.DatabaseError as e:
        print(f"Manifest DB not readable directly (likely encrypted): {e}")
        # Try alternate: look for decrypted manifest in temp
        import glob
        tmps = glob.glob(os.path.join(tempfile.gettempdir(), '**', 'Manifest*.db'), recursive=True)
        print(f"Decrypted manifest candidates in temp: {tmps}")
    finally:
        conn.close()
except Exception as e:
    print(f"Manifest error: {type(e).__name__}: {e}")
    traceback.print_exc()

print("\nDone.")
'@

$tmpFile = [System.IO.Path]::GetTempFileName() + ".py"
$pyScript | Set-Content -Path $tmpFile -Encoding UTF8
$output = python $tmpFile 2>&1 | Out-String
Remove-Item $tmpFile -ErrorAction SilentlyContinue
Write-Host $output
Send-Output $output
