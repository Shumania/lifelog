$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

function Send-Output($text) {
    $body = @{ computer=$computer; timestamp=$timestamp; source="LifeLog-DevLoop"; output=$text } | ConvertTo-Json
    Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType "application/json" | Out-Null
}

# Check Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Send-Output "ERROR: Python not found on $computer. Please install from https://python.org and check 'Add to PATH'."
    exit 1
}

# Install iphone-backup-decrypt if needed
python -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null

$script = @'
import os, sys, tempfile, json, sqlite3, glob

try:
    from iphone_backup_decrypt import EncryptedBackup, RelativePath, DomainLike
except ImportError:
    print("ERROR: iphone-backup-decrypt not installed")
    sys.exit(1)

# Find backup
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

password = "#ngrierBill70"
backup = EncryptedBackup(backup_directory=backup_root, passphrase=password)

# Unlock by extracting podcasts DB
with tempfile.TemporaryDirectory() as tmpdir:
    out = os.path.join(tmpdir, "podcasts.sqlite")
    try:
        backup.extract_file(
            relative_name="Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Documents/MTLibrary.sqlite",
            domain_like="AppDomainGroup-243LU875E5",
            output_filename=out
        )
        size = os.path.getsize(out)
        print(f"Podcasts unlock: {size:,} bytes - OK")
    except Exception as e:
        print(f"Podcasts unlock failed: {e}")

# Query manifest using context manager correctly
print("\nQuerying manifest for Google Maps files...")
try:
    with backup._manifest_db_conn as conn:
        rows = conn.execute("""
            SELECT fileID, domain, relativePath 
            FROM Files 
            WHERE (domain LIKE '%google%' OR domain LIKE '%maps%' OR relativePath LIKE '%google%' OR relativePath LIKE '%maps%' OR relativePath LIKE '%timeline%')
            ORDER BY domain, relativePath
        """).fetchall()
        if rows:
            print(f"Found {len(rows)} Google/Maps related files:")
            for fileID, domain, relPath in rows:
                print(f"  [{domain}] {relPath} => {fileID}")
        else:
            print("No Google Maps files found in manifest.")
            # Show all unique domains so we can see what's there
            domains = conn.execute("SELECT DISTINCT domain FROM Files ORDER BY domain").fetchall()
            print(f"\nAll {len(domains)} domains in backup:")
            for (d,) in domains:
                print(f"  {d}")
except Exception as e:
    print(f"Manifest query error: {e}")
    import traceback
    traceback.print_exc()
    # Try direct sqlite on Manifest.db
    manifest_path = os.path.join(backup_root, "Manifest.db")
    if os.path.exists(manifest_path):
        try:
            conn2 = sqlite3.connect(manifest_path)
            rows = conn2.execute("SELECT fileID, domain, relativePath FROM Files WHERE domain LIKE '%google%' OR relativePath LIKE '%google%' LIMIT 20").fetchall()
            print(f"Direct Manifest.db query: {len(rows)} rows")
            for r in rows:
                print(f"  {r}")
        except Exception as e2:
            print(f"Direct manifest also failed: {e2}")
'@

$output = python -c $script 2>&1 | Out-String
Write-Host $output
Send-Output $output
