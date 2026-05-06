$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

$script = @'
import sys, os, sqlite3, tempfile, shutil, json, requests
from iphone_backup_decrypt import EncryptedBackup, RelativePath, DomainLike

webhook = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
computer = os.environ.get("COMPUTERNAME", "unknown")

def post(output):
    try:
        requests.post(webhook, json={"computer": computer, "timestamp": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "source": "inspect_googlemaps_backup", "output": output}, timeout=10)
    except Exception as e:
        print(f"Post failed: {e}")

lines = []
log = lambda s: [lines.append(s), print(s)]

# Find backup
roots = [
    os.path.join(os.environ.get("USERPROFILE",""), "Apple", "MobileSync", "Backup"),
    os.path.join(os.environ.get("USERPROFILE",""), "AppData", "Roaming", "Apple Computer", "MobileSync", "Backup"),
]
backup_path = None
for root in roots:
    if os.path.isdir(root):
        dirs = [(d, os.path.getmtime(os.path.join(root,d))) for d in os.listdir(root) if os.path.isdir(os.path.join(root,d))]
        if dirs:
            backup_path = os.path.join(root, sorted(dirs, key=lambda x: x[1], reverse=True)[0][0])
            break

if not backup_path:
    log("ERROR: No backup found")
    post("\n".join(lines))
    sys.exit(1)

log(f"Backup: {backup_path}")

backup = EncryptedBackup(backup_directory=backup_path, passphrase="#ngrierBill70")

# Step 1: Extract a real file to trigger unlock
log("\n=== Step 1: Extracting podcasts DB to trigger unlock ===")
tmp = tempfile.mkdtemp()
try:
    out = os.path.join(tmp, "MTLibrary.sqlite")
    backup.extract_file(
        relative_path="Library/Database/MTLibrary.sqlite",
        domain_like="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
        output_filename=out
    )
    log(f"Extracted podcasts DB: {os.path.getsize(out)} bytes")
    log(f"Unlocked: {backup._unlocked}")
except Exception as e:
    log(f"Extraction failed: {e}")

# Step 2: Query manifest DB for ALL Google Maps files
log("\n=== Step 2: Querying manifest for Google Maps files ===")
try:
    manifest_path = backup._temp_decrypted_manifest_db_path
    log(f"Manifest path: {manifest_path}")
    if manifest_path and os.path.exists(manifest_path):
        conn = sqlite3.connect(manifest_path)
        # All files in any google/maps domain
        rows = conn.execute("""
            SELECT domain, relativePath, flags, file
            FROM Files
            WHERE lower(domain) LIKE '%google%' OR lower(domain) LIKE '%maps%'
            ORDER BY domain, relativePath
        """).fetchall()
        log(f"Found {len(rows)} Google/Maps files in manifest")
        for domain, path, flags, _ in rows:
            log(f"  [{domain}] {path}")
        conn.close()
    else:
        log(f"Manifest not found at {manifest_path}")
except Exception as e:
    log(f"Manifest query failed: {e}")

# Step 3: Also list ALL unique domains so we can see everything available
log("\n=== Step 3: All unique domains in backup ===")
try:
    manifest_path = backup._temp_decrypted_manifest_db_path
    if manifest_path and os.path.exists(manifest_path):
        conn = sqlite3.connect(manifest_path)
        domains = conn.execute("SELECT DISTINCT domain FROM Files ORDER BY domain").fetchall()
        log(f"Total domains: {len(domains)}")
        for (d,) in domains:
            log(f"  {d}")
        conn.close()
except Exception as e:
    log(f"Domain list failed: {e}")

log("\nDone!")
post("\n".join(lines))
'@

$tmpScript = Join-Path $env:TEMP "dev_next_inspect.py"
$script | Out-File -FilePath $tmpScript -Encoding UTF8

$output = & python $tmpScript 2>&1 | Out-String

# Also post from PowerShell in case Python didn't post
$body = @{ computer = $computer; timestamp = $timestamp; source = 'LifeLog-DevLoop'; output = $output } | ConvertTo-Json -Depth 3
Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json' | Out-Null
Write-Host $output
