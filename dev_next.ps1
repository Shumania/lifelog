$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

function Send-Output($text) {
    $body = @{ computer=$computer; timestamp=$timestamp; source="LifeLog-DevLoop"; output=$text } | ConvertTo-Json -Depth 3
    Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType "application/json" | Out-Null
}

$log = @()
$log += "=== SYSINFO ==="
$log += "Computer: $computer"
$log += "User: $env:USERNAME"
$log += "Time: $timestamp"
$log += ""

# Find real Python (not Windows Store stub)
$pythonExe = $null
$log += "=== PYTHON SEARCH ==="

# Check all python locations, skip Store stub
$candidates = @(
    (Get-Command python -ErrorAction SilentlyContinue).Source,
    (Get-Command python3 -ErrorAction SilentlyContinue).Source,
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe",
    "C:\Python310\python.exe",
    "C:\ProgramData\LifeLog\python\python.exe"
)

foreach ($c in $candidates) {
    if ($c -and (Test-Path $c) -and $c -notlike "*WindowsApps*") {
        $pythonExe = $c
        $log += "Found Python: $c"
        break
    } elseif ($c) {
        $log += "Skipped: $c"
    }
}

if (-not $pythonExe) {
    $log += "ERROR: No real Python found (WindowsApps stub doesn't count)"
    $log += "Please install Python from https://python.org - make sure to check 'Add to PATH'"
    Send-Output ($log -join "`n")
    exit 1
}

# Verify Python actually works
$ver = & $pythonExe --version 2>&1 | Out-String
$log += "Python version: $ver"

# Install package
$log += ""
$log += "=== PIP INSTALL ==="
$pipOut = & $pythonExe -m pip install iphone-backup-decrypt 2>&1 | Out-String
$log += $pipOut

$pyScript = @'
import os, sys, tempfile, sqlite3, traceback

try:
    from iphone_backup_decrypt import EncryptedBackup, RelativePath, DomainLike
    import iphone_backup_decrypt
    print(f"iphone_backup_decrypt version: {iphone_backup_decrypt.__version__ if hasattr(iphone_backup_decrypt, '__version__') else 'unknown'}")
except ImportError as e:
    print(f"ERROR: iphone-backup-decrypt not installed: {e}")
    sys.exit(1)

# Find most recent backup
backup_root = None
for base in [
    os.path.join(os.environ.get("USERPROFILE",""), "Apple", "MobileSync", "Backup"),
    os.path.join(os.environ.get("USERPROFILE",""), "AppData", "Roaming", "Apple Computer", "MobileSync", "Backup"),
    os.path.join(os.environ.get("USERPROFILE",""), "AppData", "Roaming", "Apple", "MobileSync", "Backup"),
]:
    if os.path.isdir(base):
        candidates = [os.path.join(base,d) for d in os.listdir(base) if os.path.isdir(os.path.join(base,d))]
        if candidates:
            backup_root = max(candidates, key=lambda p: os.path.getmtime(p))
            break

if not backup_root:
    print("ERROR: No backup found in any standard location")
    for base in [
        os.path.join(os.environ.get("USERPROFILE",""), "Apple", "MobileSync", "Backup"),
        os.path.join(os.environ.get("USERPROFILE",""), "AppData", "Roaming", "Apple Computer", "MobileSync", "Backup"),
    ]:
        print(f"  Checked: {base} -> exists={os.path.isdir(base)}")
    sys.exit(1)

print(f"Backup: {backup_root}")

try:
    backup = EncryptedBackup(backup_directory=backup_root, passphrase="#ngrierBill70")
except Exception as e:
    print(f"ERROR creating EncryptedBackup: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

# Print all attributes
attrs = [a for a in dir(backup) if not a.startswith('__')]
print(f"Backup attributes: {attrs}")

# Try to unlock by extracting podcasts DB
with tempfile.TemporaryDirectory() as tmpdir:
    out = os.path.join(tmpdir, "podcasts.sqlite")
    try:
        backup.extract_file(
            relative_path="Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Documents/MTLibrary.sqlite",
            output_filename=out
        )
        size = os.path.getsize(out)
        print(f"Podcasts unlock: {size:,} bytes - OK")
    except Exception as e:
        print(f"Podcasts unlock failed: {type(e).__name__}: {e}")
        traceback.print_exc()

# Query manifest
print("\nQuerying manifest...")
try:
    manifest_path = backup._manifest_db_path
    print(f"Manifest path: {manifest_path}")
    print(f"Manifest exists: {os.path.exists(manifest_path)}")
    conn = sqlite3.connect(manifest_path)
    try:
        domains = conn.execute("SELECT DISTINCT domain FROM Files ORDER BY domain").fetchall()
        print(f"Total domains: {len(domains)}")
        google_domains = [d[0] for d in domains if d[0] and ('google' in d[0].lower() or 'maps' in d[0].lower())]
        print(f"Google/Maps domains: {google_domains}")
        rows = conn.execute("""
            SELECT fileID, domain, relativePath FROM Files
            WHERE domain LIKE '%google%' OR domain LIKE '%maps%'
               OR relativePath LIKE '%timeline%' OR relativePath LIKE '%Maps%'
            ORDER BY domain, relativePath LIMIT 100
        """).fetchall()
        print(f"Google/Maps files: {len(rows)}")
        for fileID, domain, relPath in rows:
            print(f"  [{domain}] {relPath}")
    except sqlite3.DatabaseError as e:
        print(f"Manifest not readable (encrypted?): {e}")
    finally:
        conn.close()
except Exception as e:
    print(f"Manifest error: {type(e).__name__}: {e}")
    traceback.print_exc()

print("Done.")
'@

$tmpFile = [System.IO.Path]::GetTempFileName() + ".py"
$pyScript | Set-Content -Path $tmpFile -Encoding UTF8

$log += ""
$log += "=== PYTHON SCRIPT OUTPUT ==="
$output = & $pythonExe $tmpFile 2>&1 | Out-String
Remove-Item $tmpFile -ErrorAction SilentlyContinue
$log += $output

$fullOutput = $log -join "`n"
Write-Host $fullOutput
Send-Output $fullOutput
