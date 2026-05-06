# dev_next.ps1 - VERSION: 2026-05-06-v8-googlemaps
$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

# Find Python - skip WindowsApps stub
$pythonExe = $null
try {
    $candidates = @(where.exe python 2>$null)
    foreach ($c in $candidates) {
        if ($c -notlike "*WindowsApps*") { $pythonExe = $c.Trim(); break }
    }
    if (-not $pythonExe -and $candidates) { $pythonExe = $candidates[0].Trim() }
} catch {}
if (-not $pythonExe) {
    $fallbacks = @(
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    )
    foreach ($p in $fallbacks) { if (Test-Path $p) { $pythonExe = $p; break } }
}
if (-not $pythonExe) {
    $body = @{ computer=$computer; timestamp=$timestamp; source="LifeLog-DevLoop"; output="v8 ERROR: Python not found" } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json"
    exit
}

& $pythonExe -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null

$scriptContent = @'
import os, sys, glob, tempfile, shutil, sqlite3

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
    print("ERROR: No backup found"); sys.exit(1)

print(f"Backup: {backup_path}")

from iphone_backup_decrypt import EncryptedBackup

backup = EncryptedBackup(backup_directory=backup_path, passphrase=PASSWORD)

# Step 1: unlock by extracting podcasts DB (forces manifest decryption)
tmpdir = tempfile.mkdtemp()
try:
    out = os.path.join(tmpdir, "podcasts.sqlite")
    backup.extract_file(
        relative_path="Library/Application Support/Podcasts/MTLibrary.sqlite",
        domain_like="AppDomainGroup-%groups.com.apple.podcasts",
        output_filename=out
    )
    print(f"Unlock OK: {os.path.getsize(out)} bytes")
except Exception as e:
    print(f"Unlock warning: {e}")

# Step 2: open the decrypted manifest DB via sqlite3
try:
    manifest_path = backup._manifest_db_path
    print(f"Manifest DB path: {manifest_path}")
    conn = sqlite3.connect(manifest_path)
    cur = conn.cursor()

    # Search for Google/Maps related files
    cur.execute("""
        SELECT fileID, domain, relativePath
        FROM Files
        WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR domain LIKE '%Maps%'
           OR relativePath LIKE '%google%' OR relativePath LIKE '%maps%' OR relativePath LIKE '%timeline%'
           OR relativePath LIKE '%tlogs%' OR relativePath LIKE '%Maps%'
        ORDER BY domain, relativePath
    """)
    rows = cur.fetchall()
    print(f"\nFound {len(rows)} Google/Maps files:")
    for fileID, domain, relpath in rows:
        print(f"  [{domain}] {relpath}  (id={fileID[:8]}...)")

    # All distinct domains containing 'google' or 'aps' (captures Maps/Apple)
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
    print(f"Manifest query error: {e}")
    import traceback; traceback.print_exc()

shutil.rmtree(tmpdir, ignore_errors=True)
print("\nDone.")
'@

$scriptPath = "$env:TEMP\inspect_maps_v8.py"
$scriptContent | Out-File -FilePath $scriptPath -Encoding UTF8

$output = & $pythonExe $scriptPath 2>&1 | Out-String

$body = @{
    computer  = $computer
    timestamp = $timestamp
    source    = "LifeLog-DevLoop"
    output    = "v8 | Python: $pythonExe`n$output"
} | ConvertTo-Json -Compress

Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json"
