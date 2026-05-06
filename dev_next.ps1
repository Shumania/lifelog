# dev_next.ps1 - VERSION: 2026-05-06-v6-googlemaps
# Tasklet-controlled script. Run by LifeLog-DevLoop.ps1 every 5 min.

$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

Write-Host "dev_next v6 running on $computer at $timestamp"

# Find Python - try where.exe first, skip WindowsApps stub
$pythonExe = $null
try {
    $candidates = where.exe python 2>$null
    foreach ($c in $candidates) {
        if ($c -notlike "*WindowsApps*") {
            $pythonExe = $c.Trim()
            break
        }
    }
    # If all are WindowsApps, still try the first one
    if (-not $pythonExe -and $candidates) {
        $pythonExe = ($candidates | Select-Object -First 1).Trim()
    }
} catch {}

# Fallback: common install paths
if (-not $pythonExe) {
    $fallbacks = @(
        "C:\Python314\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    )
    foreach ($p in $fallbacks) {
        if (Test-Path $p) { $pythonExe = $p; break }
    }
}

if (-not $pythonExe) {
    $errMsg = "Python not found on $computer"
    Write-Host $errMsg
    $body = @{ computer=$computer; timestamp=$timestamp; source="LifeLog-DevLoop"; output=$errMsg } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json"
    exit
}

Write-Host "Using Python: $pythonExe"

try {
    # Install dependency quietly
    & $pythonExe -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null

    $scriptContent = @'
import os, sys, glob, sqlite3, tempfile, shutil

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
    print("ERROR: No backup found")
    sys.exit(1)

print(f"Backup: {backup_path}")

from iphone_backup_decrypt import EncryptedBackup, RelativePath

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
    print(f"Unlock OK: {os.path.getsize(out)} bytes")
except Exception as e:
    print(f"Unlock failed: {e}")

# Step 2: query Manifest.db directly
manifest_src = os.path.join(backup_path, "Manifest.db")
manifest_tmp = os.path.join(tmpdir, "Manifest.db")

if os.path.exists(manifest_src):
    shutil.copy2(manifest_src, manifest_tmp)
    try:
        conn = sqlite3.connect(manifest_tmp)
        cur = conn.cursor()
        cur.execute("""
            SELECT fileID, domain, relativePath
            FROM Files
            WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR domain LIKE '%Maps%'
               OR relativePath LIKE '%google%' OR relativePath LIKE '%maps%' OR relativePath LIKE '%timeline%'
               OR relativePath LIKE '%tlogs%' OR relativePath LIKE '%Maps%'
            ORDER BY domain, relativePath
        """)
        rows = cur.fetchall()
        print(f"\nFound {len(rows)} Google/Maps files in Manifest.db:")
        for fileID, domain, relpath in rows:
            print(f"  [{domain}] {relpath}")

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
    print("Manifest.db not found at backup root")

shutil.rmtree(tmpdir, ignore_errors=True)
print("\nDone.")
'@

    $scriptPath = "$env:TEMP\inspect_maps_v6.py"
    $scriptContent | Out-File -FilePath $scriptPath -Encoding UTF8

    $output = & $pythonExe $scriptPath 2>&1 | Out-String
    Write-Host $output

    $body = @{
        computer  = $computer
        timestamp = $timestamp
        source    = "LifeLog-DevLoop"
        output    = "v6 | Python: $pythonExe`n$output"
    } | ConvertTo-Json -Compress

    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json"
}
catch {
    $errBody = @{
        computer  = $computer
        timestamp = $timestamp
        source    = "LifeLog-DevLoop"
        output    = "v6 ERROR on $computer`: $_"
    } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $errBody -ContentType "application/json"
}
