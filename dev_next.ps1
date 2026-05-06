$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

try {
    # Install dependencies
    pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null

    $scriptContent = @'
import os, sys, glob, sqlite3, tempfile, shutil, struct, json, requests
from iphone_backup_decrypt import EncryptedBackup, RelativePath

WEBHOOK = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
PASSWORD = "#ngrierBill70"

def find_backup():
    for base in [
        os.path.join(os.environ.get("USERPROFILE",""), "Apple", "MobileSync", "Backup"),
        os.path.join(os.environ.get("APPDATA",""), "Apple Computer", "MobileSync", "Backup"),
    ]:
        if os.path.isdir(base):
            dirs = sorted([d for d in glob.glob(os.path.join(base, "*")) if os.path.isdir(d)],
                          key=lambda d: os.path.getmtime(d), reverse=True)
            if dirs:
                return dirs[0]
    return None

backup_path = find_backup()
if not backup_path:
    print("No backup found")
    sys.exit(1)

print(f"Backup: {backup_path}")

backup = EncryptedBackup(backup_directory=backup_path, passphrase=PASSWORD)

# Step 1: unlock by extracting podcasts DB
tmpdir = tempfile.mkdtemp()
try:
    out = os.path.join(tmpdir, "podcasts.sqlite")
    backup.extract_file(
        relative_name="Library/Application Support/Podcasts/MTLibrary.sqlite",
        domain_like="AppDomainGroup-%groups.com.apple.podcasts",
        output_filename=out
    )
    print(f"Podcasts unlock OK: {os.path.getsize(out)} bytes")
except Exception as e:
    print(f"Podcasts unlock failed: {e}")

# Step 2: Extract Manifest.db to temp and query it directly with sqlite3
manifest_src = os.path.join(backup_path, "Manifest.db")
manifest_tmp = os.path.join(tmpdir, "Manifest.db")

output_lines = []

if os.path.exists(manifest_src):
    shutil.copy2(manifest_src, manifest_tmp)
    try:
        conn = sqlite3.connect(manifest_tmp)
        cur = conn.cursor()
        # Search for Google Maps related files
        cur.execute("""
            SELECT fileID, domain, relativePath, file
            FROM Files
            WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR domain LIKE '%Maps%'
               OR relativePath LIKE '%google%' OR relativePath LIKE '%maps%' OR relativePath LIKE '%timeline%'
               OR relativePath LIKE '%tlogs%' OR relativePath LIKE '%Maps%'
            ORDER BY domain, relativePath
        """)
        rows = cur.fetchall()
        print(f"\nFound {len(rows)} Google/Maps files in Manifest.db:")
        for fileID, domain, relpath, fileblob in rows:
            # Try to get file size from plist blob
            size_str = ""
            try:
                blob_str = str(fileblob)
                size_str = f" (blob {len(fileblob)} bytes)"
            except:
                pass
            line = f"  [{domain}] {relpath}{size_str}"
            print(line)
            output_lines.append({"fileID": fileID, "domain": domain, "relativePath": relpath})
        
        # Also check all unique domains containing 'google' or 'maps'
        cur.execute("""
            SELECT DISTINCT domain FROM Files 
            WHERE domain LIKE '%oogle%' OR domain LIKE '%aps%'
            ORDER BY domain
        """)
        domains = cur.fetchall()
        print(f"\nAll matching domains ({len(domains)}):")
        for (d,) in domains:
            print(f"  {d}")
        
        conn.close()
    except Exception as e:
        print(f"Manifest.db query error: {e}")
else:
    print("Manifest.db not found at backup root - may be fully encrypted")
    # Try to find it via backup object
    try:
        # List all files via the backup's internal manifest
        import contextlib
        print("\nTrying backup._manifest_db_conn context manager...")
        with backup._manifest_db_conn as conn:
            cur = conn.cursor()
            cur.execute("SELECT fileID, domain, relativePath FROM Files WHERE domain LIKE '%oogle%' OR domain LIKE '%aps%' LIMIT 50")
            rows = cur.fetchall()
            print(f"Found {len(rows)} rows via context manager")
            for row in rows:
                print(f"  {row}")
    except Exception as e2:
        print(f"Context manager also failed: {e2}")

shutil.rmtree(tmpdir, ignore_errors=True)
print("\nDone.")
'@

    $scriptPath = "$env:TEMP\inspect_maps.py"
    $scriptContent | Out-File -FilePath $scriptPath -Encoding UTF8

    $output = python $scriptPath 2>&1 | Out-String
    Write-Host $output

    $body = @{
        computer  = $computer
        timestamp = $timestamp
        source    = "LifeLog-DevLoop"
        output    = $output
    } | ConvertTo-Json -Compress

    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json"
}
catch {
    $errBody = @{
        computer  = $computer
        timestamp = $timestamp
        source    = "LifeLog-DevLoop"
        output    = "ERROR: $_"
    } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $errBody -ContentType "application/json"
}
