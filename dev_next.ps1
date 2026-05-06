# dev_next.ps1 - VERSION: 2026-05-06-v9-googlemaps
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
    $body = @{ computer=$computer; timestamp=$timestamp; source="LifeLog-DevLoop"; output="v9 ERROR: Python not found" } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json"
    exit
}

& $pythonExe -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null

$scriptContent = @'
import os, sys, glob, tempfile, shutil, sqlite3, time

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

# Step 1: unlock by extracting podcasts DB
tmpdir = tempfile.mkdtemp()
t_before = time.time()
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

# Step 2: dump all attributes of the backup object to find manifest connection
print("\n--- backup object attributes ---")
for attr in sorted(dir(backup)):
    if not attr.startswith('__'):
        try:
            val = getattr(backup, attr)
            if not callable(val):
                print(f"  {attr} = {repr(val)[:120]}")
        except Exception as e:
            print(f"  {attr} => ERROR: {e}")

# Step 3: scan temp dir for .db files created/modified after backup init
print("\n--- Temp dir .db/.sqlite files modified in last 120s ---")
tempbase = tempfile.gettempdir()
now = time.time()
for fname in os.listdir(tempbase):
    fpath = os.path.join(tempbase, fname)
    if fname.lower().endswith(('.db', '.sqlite')):
        try:
            mtime = os.path.getmtime(fpath)
            if now - mtime < 120:
                print(f"  {fpath}  ({os.path.getsize(fpath)} bytes, age={now-mtime:.0f}s)")
        except:
            pass

shutil.rmtree(tmpdir, ignore_errors=True)
print("\nDone.")
'@

$scriptPath = "$env:TEMP\inspect_maps_v9.py"
$scriptContent | Out-File -FilePath $scriptPath -Encoding UTF8

$output = & $pythonExe $scriptPath 2>&1 | Out-String

$body = @{
    computer  = $computer
    timestamp = $timestamp
    source    = "LifeLog-DevLoop"
    output    = "v9 | Python: $pythonExe`n$output"
} | ConvertTo-Json -Compress

Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json"
