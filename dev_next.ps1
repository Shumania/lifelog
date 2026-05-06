# dev_next.ps1 - Extract Google Maps Timeline protobuf and upload to webhook
$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

try {
    # Find Python
    $python = Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
    if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source }
    if (-not $python) { $python = "C:\ProgramData\LifeLog\python\python.exe" }

    # Download extraction script
    $scriptPath = "$env:TEMP\extract_tlogs.py"
    $scriptContent = @'
import sys, os, base64, json, urllib.request, urllib.error

webhook_url = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
computer = os.environ.get("COMPUTERNAME", "unknown")

try:
    import iphone_backup_decrypt
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "iphone-backup-decrypt", "-q"])
    import iphone_backup_decrypt

from iphone_backup_decrypt import EncryptedBackup, RelativePath, DomainLike

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

if not backup_path:
    print("ERROR: No backup found")
    sys.exit(1)

print(f"Backup: {backup_path}")

backup = EncryptedBackup(backup_directory=backup_path, passphrase="#ngrierBill70")

# Unlock by extracting a known file first
try:
    backup.extract_file(
        relative_path="Library/ApplicationSupport/MTLibrary.sqlite",
        domain_like="%podcasts%",
        output_filename=os.path.join(os.environ.get("TEMP","/tmp"), "_unlock_test.sqlite")
    )
    print("Unlocked via podcasts DB")
except Exception as e:
    print(f"Podcasts unlock attempt: {e}")

# Try extracting the tlogs file
tlogs_path = os.path.join(os.environ.get("TEMP","/tmp"), "tlogs_offline_storage.binaryproto")
try:
    backup.extract_file(
        relative_path="Library/Application Support/tlogs_offline_storage.binaryproto",
        domain_like="%google%maps%",
        output_filename=tlogs_path
    )
    print(f"Extracted tlogs: {os.path.getsize(tlogs_path):,} bytes")
except Exception as e:
    print(f"Extract with domain failed: {e}")
    # Try without domain restriction
    try:
        backup.extract_file(
            relative_path="Library/Application Support/tlogs_offline_storage.binaryproto",
            output_filename=tlogs_path
        )
        print(f"Extracted tlogs (no domain): {os.path.getsize(tlogs_path):,} bytes")
    except Exception as e2:
        print(f"Extract without domain also failed: {e2}")

# Also try extract_files with domain pattern to list all Google Maps files
print("\nListing all Google Maps files in manifest...")
try:
    cursor = backup.manifest_db_cursor()
    cursor.execute("SELECT fileID, domain, relativePath FROM Files WHERE domain LIKE '%google%maps%' OR domain LIKE '%GoogleMaps%' ORDER BY relativePath")
    rows = cursor.fetchall()
    print(f"Found {len(rows)} Google Maps files:")
    for r in rows:
        print(f"  [{r[1]}] {r[2]}")
except Exception as e:
    print(f"Manifest query error: {e}")

# Upload tlogs file as base64
if os.path.exists(tlogs_path):
    with open(tlogs_path, "rb") as f:
        raw = f.read()
    b64 = base64.b64encode(raw).decode("ascii")
    payload = json.dumps({
        "source": "LifeLog-DevLoop",
        "computer": computer,
        "timestamp": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file": "tlogs_offline_storage.binaryproto",
        "size": len(raw),
        "data_b64": b64
    }).encode()
    req = urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=30)
    print(f"Uploaded {len(raw):,} bytes of tlogs data")
else:
    # Send diagnostic output anyway
    payload = json.dumps({
        "source": "LifeLog-DevLoop",
        "computer": computer,
        "timestamp": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file": "tlogs_offline_storage.binaryproto",
        "size": 0,
        "error": "File not extracted"
    }).encode()
    req = urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=30)
    print("Sent diagnostic (no file)")
'@
    Set-Content -Path $scriptPath -Value $scriptContent -Encoding UTF8

    $output = & $python $scriptPath 2>&1 | Out-String
    Write-Host $output

    # Also send stdout summary via normal webhook
    $body = @{ source="LifeLog-DevLoop"; computer=$computer; timestamp=$timestamp; output=$output } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json" | Out-Null

} catch {
    $errMsg = $_.Exception.Message
    Write-Host "ERROR: $errMsg"
    $body = @{ source="LifeLog-DevLoop"; computer=$computer; timestamp=$timestamp; output="ERROR: $errMsg" } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json" | Out-Null
}
