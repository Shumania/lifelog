# dev_next.ps1 v3 - controlled by LifeLog agent
# Fix: use _temp_manifest_db_conn, parse binary plist, unlock via extract_file

$WEBHOOK = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

$python = $null
foreach ($cmd in @("python", "python3", "C:\ProgramData\LifeLog\python\python.exe")) {
    try { $v = & $cmd --version 2>&1; if ($v -match 'Python') { $python = $cmd; break } } catch {}
}
if (-not $python) {
    $body = @{ output="ERROR: Python not found"; computer=$env:COMPUTERNAME; source="dev_next_v3" } | ConvertTo-Json
    Invoke-RestMethod -Uri $WEBHOOK -Method POST -Body $body -ContentType "application/json"
    exit 1
}

$script = @'
import sys, os, sqlite3, tempfile, json, plistlib
sys.path.insert(0, r"C:\ProgramData\LifeLog\python\Lib\site-packages")

try:
    import iphone_backup_decrypt
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "iphone_backup_decrypt", "-q"])
    import iphone_backup_decrypt
from iphone_backup_decrypt import EncryptedBackup

PASSWORD = "#ngrierBill70"

def find_backup():
    candidates = []
    for base in [
        os.path.expandvars(r"%USERPROFILE%\Apple\MobileSync\Backup"),
        os.path.expandvars(r"%USERPROFILE%\Apple Devices\Backup"),
    ]:
        if os.path.isdir(base):
            for d in os.listdir(base):
                full = os.path.join(base, d)
                mp = os.path.join(full, "Manifest.plist")
                if os.path.exists(mp):
                    candidates.append((os.path.getmtime(mp), full))
    return sorted(candidates)[-1][1] if candidates else None

backup_dir = find_backup()
print(f"Backup dir: {backup_dir}")
if not backup_dir:
    print("ERROR: No backup found")
    sys.exit(1)

backup = EncryptedBackup(backup_directory=backup_dir, passphrase=PASSWORD)

# Step 1: Trigger unlock by extracting the podcasts DB (known to exist)
print("\n=== Step 1: Unlock backup ===")
with tempfile.TemporaryDirectory() as tmp:
    unlock_file = os.path.join(tmp, "unlock_test.sqlite")
    try:
        backup.extract_file(
            domain="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
            relative_path="Library/Caches/MTLibrary.sqlite",
            output_filename=unlock_file
        )
        if os.path.exists(unlock_file):
            print(f"Unlock SUCCESS (podcasts DB: {os.path.getsize(unlock_file)} bytes)")
            print(f"_unlocked = {backup._unlocked}")
        else:
            print("extract_file ran but output not found")
    except Exception as e:
        print(f"Unlock error: {e}")
        import traceback; traceback.print_exc()

# Step 2: Query manifest for all Google Maps files
print("\n=== Step 2: Query manifest for Google Maps files ===")
try:
    conn = backup._temp_manifest_db_conn
    print(f"_temp_manifest_db_conn: {conn}")
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT domain, relativePath, fileID FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR relativePath LIKE '%timeline%' OR relativePath LIKE '%gmm%' ORDER BY domain, relativePath")
        rows = cur.fetchall()
        print(f"Found {len(rows)} Google/Maps files:")
        for domain, path, fid in rows:
            print(f"  [{domain}] {path}")
        # Also list ALL domains to see what's available
        print("\n=== All domains in backup ===")
        cur.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
        domains = cur.fetchall()
        for (d,) in domains:
            print(f"  {d}")
    else:
        print("ERROR: _temp_manifest_db_conn still None after unlock!")
except Exception as e:
    print(f"Manifest query error: {e}")
    import traceback; traceback.print_exc()

# Step 3: Parse the Google Maps plist (binary plist)
print("\n=== Step 3: Parse com.google.Maps.plist ===")
with tempfile.TemporaryDirectory() as tmp:
    plist_file = os.path.join(tmp, "googlemaps.plist")
    try:
        backup.extract_file(
            domain="AppDomain-com.google.Maps",
            relative_path="Library/Preferences/com.google.Maps.plist",
            output_filename=plist_file
        )
        if os.path.exists(plist_file):
            with open(plist_file, "rb") as f:
                data = plistlib.load(f)
            keys = list(data.keys())
            print(f"Plist parsed OK! {len(keys)} keys.")
            # Print all keys - look for timeline-related ones
            for k in sorted(keys):
                v = data[k]
                print(f"  {k}: {repr(v)[:120]}")
        else:
            print("Plist file not extracted")
    except Exception as e:
        print(f"Plist error: {e}")
        import traceback; traceback.print_exc()

print("\n=== Done ===")
'@

$scriptFile = "$env:TEMP\lifelog_gmaps_v3.py"
$script | Set-Content $scriptFile -Encoding UTF8

Write-Host "[dev_next v3] Running..."
$output = & $python $scriptFile 2>&1 | Out-String
Write-Host $output

Remove-Item $scriptFile -ErrorAction SilentlyContinue

$body = @{
    output    = "[dev_next v3]`n" + $output
    timestamp = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    computer  = $env:COMPUTERNAME
    source    = "dev_next_v3"
} | ConvertTo-Json -Depth 3

try {
    Invoke-RestMethod -Uri $WEBHOOK -Method POST -Body $body -ContentType "application/json"
    Write-Host "Sent to Tasklet."
} catch {
    Write-Host "WARNING: webhook failed: $_"
}
