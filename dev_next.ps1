# dev_next.ps1 - agent-controlled
$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

function Send-Output($text) {
    $body = @{ output = $text; timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); computer = $env:COMPUTERNAME; source = 'gmaps_inspect_v3' } | ConvertTo-Json
    try { Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json' | Out-Null } catch {}
}

# Find Python
$python = $null
$candidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "C:\Python312\python.exe",
    "C:\Program Files\Python312\python.exe",
    "C:\ProgramData\LifeLog\python\python.exe"
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $python = $c; break }
}
if (-not $python) {
    try {
        $p = (Get-Command python -ErrorAction Stop).Source
        if ($p -notmatch 'WindowsApps') { $python = $p }
    } catch {}
}
if (-not $python) { Send-Output "PYTHON NOT FOUND on $env:COMPUTERNAME"; exit 1 }

# Write inline Python script
$scriptFile = "$env:TEMP\gmaps_inspect_v3.py"
@'
import os, sys, sqlite3, tempfile, json
from pathlib import Path

PASSWORD = "#ngrierBill70"

# Find backup dir
def find_backup():
    bases = []
    up = os.environ.get("USERPROFILE", "")
    if up:
        bases += [
            Path(up) / "Apple" / "MobileSync" / "Backup",
            Path(up) / "AppData" / "Roaming" / "Apple Computer" / "MobileSync" / "Backup",
        ]
    bases += [
        Path("C:/Users") / os.environ.get("USERNAME","") / "Apple" / "MobileSync" / "Backup"
    ]
    best, best_t = None, 0
    for base in bases:
        if not base.exists(): continue
        for d in base.iterdir():
            mp = d / "Manifest.plist"
            if mp.exists():
                t = mp.stat().st_mtime
                if t > best_t:
                    best, best_t = d, t
    return best

backup_dir = find_backup()
print(f"Backup dir: {backup_dir}")
if not backup_dir:
    print("ERROR: No backup found")
    sys.exit(1)

# Check if encrypted
import plistlib
with open(backup_dir / "Manifest.plist", "rb") as f:
    mp = plistlib.load(f)
encrypted = mp.get("IsEncrypted", False)
print(f"Encrypted: {encrypted}")

if encrypted:
    from iphone_backup_decrypt import EncryptedBackup
    backup = EncryptedBackup(backup_directory=str(backup_dir), passphrase=PASSWORD)
    
    # Unlock by extracting podcasts DB
    print("\n=== Step 1: Unlock backup ===")
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        backup.extract_file(
            relative_path="Library/Caches/MTLibrary.sqlite",
            output_filename=tmp_path,
            domain_like="AppDomainGroup-243LU875E5.groups.com.apple.podcasts"
        )
        sz = Path(tmp_path).stat().st_size if Path(tmp_path).exists() else 0
        print(f"Podcasts DB extracted OK, size={sz}")
    except Exception as e:
        print(f"Podcasts extract error: {e}")
    
    # Now the manifest DB should be decrypted - find it
    print("\n=== Step 2: Find decrypted Manifest.db ===")
    manifest_db = None
    
    # Try _manifest_db_path attribute
    for attr in ["_manifest_db_path", "_manifest_db", "manifest_db_path"]:
        val = getattr(backup, attr, None)
        if val and Path(str(val)).exists():
            manifest_db = Path(str(val))
            print(f"Found manifest via backup.{attr}: {manifest_db}")
            break
    
    # Scan temp dirs for decrypted Manifest.db
    if not manifest_db:
        import glob
        tmp_dir = tempfile.gettempdir()
        print(f"Scanning temp dir: {tmp_dir}")
        for f in glob.glob(os.path.join(tmp_dir, "**", "Manifest.db"), recursive=True):
            try:
                conn = sqlite3.connect(f)
                conn.execute("SELECT count(*) FROM Files")
                conn.close()
                manifest_db = Path(f)
                print(f"Found working Manifest.db at: {f}")
                break
            except: pass
        # Also check backup dir itself (sometimes decrypted in place)
        for f in glob.glob(str(backup_dir / "*.db")):
            try:
                conn = sqlite3.connect(f)
                conn.execute("SELECT count(*) FROM Files")
                conn.close()
                print(f"Found working .db in backup dir: {f}")
                if not manifest_db:
                    manifest_db = Path(f)
            except: pass

else:
    manifest_db = backup_dir / "Manifest.db"
    print(f"Unencrypted - using Manifest.db directly: {manifest_db}")

# Query manifest for Google Maps files
print("\n=== Step 3: Query manifest for Google Maps files ===")
if manifest_db and manifest_db.exists():
    try:
        conn = sqlite3.connect(str(manifest_db))
        cur = conn.cursor()
        cur.execute("SELECT domain, relativePath, fileID FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR relativePath LIKE '%google%' OR relativePath LIKE '%maps%' OR relativePath LIKE '%timeline%' ORDER BY domain, relativePath")
        rows = cur.fetchall()
        print(f"Found {len(rows)} Google/Maps files:")
        for domain, rel, fid in rows:
            print(f"  [{domain}] {rel} -> {fid[:8]}...")
        
        # Also search for com.google
        cur.execute("SELECT DISTINCT domain FROM Files WHERE domain LIKE 'AppDomain-com.google%'")
        google_domains = cur.fetchall()
        print(f"\nAll com.google domains ({len(google_domains)}):")
        for (d,) in google_domains:
            cur.execute("SELECT count(*) FROM Files WHERE domain=?", (d,))
            cnt = cur.fetchone()[0]
            print(f"  {d} ({cnt} files)")
        conn.close()
    except Exception as e:
        print(f"Manifest query error: {e}")
else:
    print(f"No manifest DB found! manifest_db={manifest_db}")
    # List all backup attributes for debugging
    if encrypted:
        print("\nBackup object attributes:")
        for a in dir(backup):
            if not a.startswith("__"):
                try:
                    val = getattr(backup, a)
                    if not callable(val):
                        print(f"  {a} = {val}")
                except: pass

print("\nDone!")
'@ | Set-Content $scriptFile -Encoding UTF8

# Install dependency
& $python -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null

$output = & $python $scriptFile 2>&1 | Out-String
Send-Output "Machine: $env:COMPUTERNAME`nPython: $python`n`n$output"
