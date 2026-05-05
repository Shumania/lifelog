# dev_next.ps1 - controlled by LifeLog agent
# Purpose: unlock backup properly, then list ALL Google Maps files

$WEBHOOK = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$LIFELOG_PYTHON = "C:\ProgramData\LifeLog\python\python.exe"

function Find-Python {
    if (Test-Path $LIFELOG_PYTHON) { return $LIFELOG_PYTHON }
    $sys = Get-Command python -ErrorAction SilentlyContinue
    if ($sys) { return $sys.Source }
    return $null
}

$python = Find-Python
if (-not $python) {
    $out = "ERROR: Python not found. Please run Install-LifeLog.ps1 first."
    $body = @{ output=$out; computer=$env:COMPUTERNAME; source="dev_next" } | ConvertTo-Json
    Invoke-RestMethod -Uri $WEBHOOK -Method POST -Body $body -ContentType "application/json"
    exit 1
}

$script = @'
import sys, os, json, sqlite3, tempfile, shutil
sys.path.insert(0, r"C:\ProgramData\LifeLog\python\Lib\site-packages")

try:
    import iphone_backup_decrypt
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "iphone_backup_decrypt", "-q"])
    import iphone_backup_decrypt

from iphone_backup_decrypt import EncryptedBackup
import pathlib, glob

PASSWORD = "#ngrierBill70"

def find_backup():
    candidates = []
    for base in [
        os.path.expandvars(r"%USERPROFILE%\Apple\MobileSync\Backup"),
        os.path.expandvars(r"%USERPROFILE%\Apple Devices\Backup"),
        r"C:\ProgramData\LifeLog\backup_tmp",
    ]:
        if os.path.isdir(base):
            for d in os.listdir(base):
                full = os.path.join(base, d)
                if os.path.isdir(full) and os.path.exists(os.path.join(full, "Manifest.plist")):
                    mtime = os.path.getmtime(os.path.join(full, "Manifest.plist"))
                    candidates.append((mtime, full))
    if candidates:
        return sorted(candidates)[-1][1]
    return None

backup_dir = find_backup()
print(f"Backup: {backup_dir}")

if not backup_dir:
    print("ERROR: No backup found!")
    sys.exit(1)

# Step 1: unlock by extracting a file we know exists (podcasts DB)
print("\n=== Step 1: Unlock backup ===")
try:
    backup = EncryptedBackup(backup_directory=backup_dir, passphrase=PASSWORD)
    with tempfile.TemporaryDirectory() as tmp:
        out_file = os.path.join(tmp, "test_unlock.sqlite")
        backup.extract_file(
            domain="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
            relative_path="Library/Caches/MTLibrary.sqlite",
            output_filename=out_file
        )
        if os.path.exists(out_file):
            print(f"Unlock SUCCESS - podcasts DB extracted ({os.path.getsize(out_file)} bytes)")
        else:
            print("Unlock attempt done but file not found at expected path")
except Exception as e:
    print(f"Unlock step error: {e}")
    import traceback; traceback.print_exc()

# Step 2: enumerate all Google Maps files via manifest DB
print("\n=== Step 2: Enumerate all Google Maps files in manifest ===")
try:
    conn = backup._manifest_db_conn
    if conn is None:
        print("Manifest DB connection is still None - trying direct sqlite open")
        mdb_path = os.path.join(backup_dir, "Manifest.db")
        conn = sqlite3.connect(mdb_path)
    
    cur = conn.cursor()
    cur.execute("SELECT domain, relativePath, fileID FROM Files WHERE domain LIKE \'%google%\' OR domain LIKE \'%maps%\' OR relativePath LIKE \'%google%\' OR relativePath LIKE \'%maps%\' OR relativePath LIKE \'%timeline%\'")
    rows = cur.fetchall()
    if rows:
        print(f"Found {len(rows)} Google/Maps related files:")
        for domain, path, fid in rows:
            print(f"  [{domain}] {path} -> {fid}")
    else:
        print("No Google Maps files found in manifest.")
except Exception as e:
    print(f"Manifest query error: {e}")
    import traceback; traceback.print_exc()

# Step 3: also try direct unencrypted manifest read as fallback
print("\n=== Step 3: Direct Manifest.db query (unencrypted fallback) ===")
try:
    mdb_path = os.path.join(backup_dir, "Manifest.db")
    conn2 = sqlite3.connect(mdb_path)
    cur2 = conn2.cursor()
    cur2.execute("SELECT domain, relativePath FROM Files WHERE domain LIKE \'%google%\' OR domain LIKE \'%maps%\' ORDER BY domain, relativePath LIMIT 100")
    rows2 = cur2.fetchall()
    if rows2:
        print(f"{len(rows2)} rows from direct Manifest.db:")
        for domain, path in rows2:
            print(f"  [{domain}] {path}")
    else:
        print("No rows from direct Manifest.db (may be encrypted)")
except Exception as e:
    print(f"Direct manifest error: {e}")

print("\nDone!")
'@

$scriptFile = "$env:TEMP\lifelog_gmaps_inspect.py"
$script | Set-Content $scriptFile -Encoding UTF8

Write-Host "Running Google Maps inspection..."
$output = & $python $scriptFile 2>&1 | Out-String
Write-Host $output

Remove-Item $scriptFile -ErrorAction SilentlyContinue

$body = @{
    output    = $output
    timestamp = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    computer  = $env:COMPUTERNAME
    source    = "gmaps_inspect_v2"
} | ConvertTo-Json -Depth 3

try {
    Invoke-RestMethod -Uri $WEBHOOK -Method POST -Body $body -ContentType "application/json"
    Write-Host "Output sent to Tasklet."
} catch {
    Write-Host "WARNING: Could not send to webhook: $_"
}
