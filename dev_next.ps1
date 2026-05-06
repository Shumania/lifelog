# dev_next.ps1 - List all Google Maps files in backup
$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

try {
    $python = Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
    if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source }
    if (-not $python) { throw "Python not found" }

    $scriptPath = "$env:TEMP\list_gmaps_files.py"
    $scriptContent = @'
import sys, os, json, urllib.request
webhook_url = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
computer = os.environ.get("COMPUTERNAME", "unknown")

try:
    import iphone_backup_decrypt
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "iphone-backup-decrypt", "-q"])
    import iphone_backup_decrypt

from iphone_backup_decrypt import EncryptedBackup

# Find backup
backup_path = None
for base in [
    os.path.join(os.environ.get("USERPROFILE",""), "Apple", "MobileSync", "Backup"),
    os.path.join(os.environ.get("USERPROFILE",""), "AppData", "Roaming", "Apple Computer", "MobileSync", "Backup"),
]:
    if os.path.isdir(base):
        subdirs = [os.path.join(base, d) for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        if subdirs:
            backup_path = max(subdirs, key=lambda p: os.path.getmtime(p))
            break

print(f"Backup: {backup_path}")

backup = EncryptedBackup(backup_directory=backup_path, passphrase="#ngrierBill70")

# Unlock
try:
    backup.extract_file(
        relative_path="Library/ApplicationSupport/MTLibrary.sqlite",
        domain_like="%podcasts%",
        output_filename=os.path.join(os.environ.get("TEMP","/tmp"), "_unlock.sqlite")
    )
    print("Unlocked OK")
except Exception as e:
    print(f"Unlock: {e}")

# Query manifest for ALL Google Maps related files
results = []
try:
    import sqlite3, tempfile, shutil
    # Try to access the manifest DB
    manifest_path = os.path.join(backup_path, "Manifest.db")
    
    # The manifest might be encrypted too - try via backup object
    for attr in dir(backup):
        if 'manifest' in attr.lower() or 'db' in attr.lower():
            print(f"  backup.{attr} = {type(getattr(backup, attr)).__name__}")
    
    # Try direct sqlite on the decrypted manifest
    # First extract it
    try:
        manifest_out = os.path.join(os.environ.get("TEMP","/tmp"), "Manifest_decrypted.db")
        backup.extract_file(
            relative_path="Manifest.db",
            output_filename=manifest_out
        )
        print(f"Extracted manifest: {os.path.getsize(manifest_out)} bytes")
        conn = sqlite3.connect(manifest_out)
        cur = conn.cursor()
        # All files
        cur.execute("SELECT fileID, domain, relativePath FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%Google%' OR domain LIKE '%maps%' OR domain LIKE '%Maps%' ORDER BY domain, relativePath")
        rows = cur.fetchall()
        print(f"\nFound {len(rows)} Google/Maps files:")
        for r in rows:
            fid, domain, path = r
            # Get file size from the backup file
            file_path = os.path.join(backup_path, fid[:2], fid)
            size = os.path.getsize(file_path) if os.path.exists(file_path) else -1
            print(f"  [{domain}] {path} ({size:,} bytes)")
            results.append({"domain": domain, "path": path, "size": size, "fileID": fid})
        conn.close()
    except Exception as e:
        print(f"Manifest extract failed: {e}")
        # Try raw manifest
        if os.path.exists(manifest_path):
            try:
                conn = sqlite3.connect(manifest_path)
                cur = conn.cursor()
                cur.execute("SELECT fileID, domain, relativePath FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%Google%' ORDER BY domain, relativePath")
                rows = cur.fetchall()
                print(f"Raw manifest: {len(rows)} Google files")
                for r in rows:
                    print(f"  [{r[1]}] {r[2]}")
                conn.close()
            except Exception as e2:
                print(f"Raw manifest also failed: {e2}")
except Exception as e:
    print(f"Error: {e}")
    import traceback; traceback.print_exc()

# Send results
output = "\n".join([str(r) for r in results]) if results else "No results found"
payload = json.dumps({
    "source": "LifeLog-DevLoop",
    "computer": computer,
    "timestamp": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "output": output
}).encode()
req = urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
urllib.request.urlopen(req, timeout=30)
print("Sent!")
'@
    Set-Content -Path $scriptPath -Value $scriptContent -Encoding UTF8

    $output = & $python $scriptPath 2>&1 | Out-String
    Write-Host $output

    $body = @{ source="LifeLog-DevLoop"; computer=$computer; timestamp=$timestamp; output=$output } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json" | Out-Null

} catch {
    $errMsg = $_.Exception.Message
    $body = @{ source="LifeLog-DevLoop"; computer=$computer; timestamp=$timestamp; output="ERROR: $errMsg" } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json" | Out-Null
}
