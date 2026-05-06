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
import os, sys, tempfile, sqlite3

try:
    from iphone_backup_decrypt import EncryptedBackup, RelativePath, DomainLike
except ImportError:
    print("ERROR: iphone-backup-decrypt not installed")
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

password = "#ngrierBill70"
backup = EncryptedBackup(backup_directory=backup_root, passphrase=password)

# Unlock by extracting podcasts DB
with tempfile.TemporaryDirectory() as tmpdir:
    out = os.path.join(tmpdir, "podcasts.sqlite")
    try:
        backup.extract_file(
            relative_path="Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Documents/MTLibrary.sqlite",
            domain_like="AppDomainGroup-243LU875E5",
            output_filename=out
        )
        size = os.path.getsize(out)
        print(f"Podcasts unlock: {size:,} bytes - OK")
    except Exception as e:
        print(f"Podcasts unlock failed: {e}")
        try:
            backup.extract_file(
                relative_path="Library/Preferences/com.apple.mobilephone.plist",
                domain_like="AppDomain-com.apple.mobilephone",
                output_filename=os.path.join(tmpdir, "phone.plist")
            )
            print("Alternate unlock OK")
        except Exception as e2:
            print(f"Alternate unlock also failed: {e2}")

# Query manifest for Google/Maps files
print("\nQuerying manifest for Google/Maps files...")
try:
    with backup._manifest_db_conn as conn:
        domains = conn.execute("SELECT DISTINCT domain FROM Files ORDER BY domain").fetchall()
        print(f"Total domains in backup: {len(domains)}")
        google_domains = [d[0] for d in domains if 'google' in (d[0] or '').lower() or 'maps' in (d[0] or '').lower()]
        print(f"Google/Maps domains: {google_domains}")

        rows = conn.execute("""
            SELECT fileID, domain, relativePath
            FROM Files
            WHERE domain LIKE '%google%' OR domain LIKE '%maps%'
               OR relativePath LIKE '%timeline%' OR relativePath LIKE '%Maps%'
            ORDER BY domain, relativePath
            LIMIT 100
        """).fetchall()
        print(f"\nGoogle/Maps files found: {len(rows)}")
        for fileID, domain, relPath in rows:
            print(f"  [{domain}] {relPath}")
except Exception as e:
    print(f"Manifest query error: {e}")
    import traceback
    traceback.print_exc()

print("\nDone.")
'@

# Write to temp file and run
$tmpFile = [System.IO.Path]::GetTempFileName() + ".py"
$pyScript | Set-Content -Path $tmpFile -Encoding UTF8

$output = python $tmpFile 2>&1 | Out-String
Remove-Item $tmpFile -ErrorAction SilentlyContinue

Write-Host $output
Send-Output $output
