$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

function Send-Output($text) {
    $body = @{ computer=$computer; timestamp=$timestamp; source="LifeLog-DevLoop"; output=$text } | ConvertTo-Json -Depth 3
    Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType "application/json" | Out-Null
}

$log = @()
$log += "=== SYSINFO ==="
$log += "Computer: $computer | User: $env:USERNAME | Time: $timestamp"
$log += ""

# Find real Python (not Windows Store stub)
$pythonExe = $null
$candidates = @(
    (Get-Command python -ErrorAction SilentlyContinue).Source,
    (Get-Command python3 -ErrorAction SilentlyContinue).Source,
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "C:\Python312\python.exe",
    "C:\ProgramData\LifeLog\python\python.exe"
)
foreach ($c in $candidates) {
    if ($c -and (Test-Path $c) -and $c -notlike "*WindowsApps*") {
        $pythonExe = $c
        $log += "Python: $c"
        break
    }
}
if (-not $pythonExe) {
    Send-Output (($log + @("ERROR: No real Python found. Install from https://python.org and check 'Add to PATH'")) -join "`n")
    exit 1
}

& $pythonExe -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null

$pyScript = @'
import os, sys, tempfile, sqlite3, traceback

try:
    from iphone_backup_decrypt import EncryptedBackup
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
    print("ERROR: No backup found")
    sys.exit(1)

print(f"Backup: {backup_root}")

try:
    backup = EncryptedBackup(backup_directory=backup_root, passphrase="#ngrierBill70")
except Exception as e:
    print(f"ERROR creating backup: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

# Step 1: Unlock the backup using test_decryption()
print("Unlocking keybag...")
try:
    result = backup.test_decryption()
    print(f"test_decryption() result: {result}")
except Exception as e:
    print(f"test_decryption() failed: {type(e).__name__}: {e}")
    traceback.print_exc()

print(f"_unlocked: {backup._unlocked}")

# Step 2: Use manifest_db_cursor() to query decrypted manifest
print("\nQuerying manifest via manifest_db_cursor()...")
try:
    cursor = backup.manifest_db_cursor()
    print(f"Got cursor: {cursor}")

    # All domains
    domains = cursor.execute("SELECT DISTINCT domain FROM Files ORDER BY domain").fetchall()
    print(f"Total domains: {len(domains)}")

    # Google/Maps domains
    google_domains = [d[0] for d in domains if d[0] and ('google' in d[0].lower() or 'maps' in d[0].lower())]
    print(f"Google/Maps domains: {google_domains}")

    # Google/Maps files
    rows = cursor.execute("""
        SELECT fileID, domain, relativePath FROM Files
        WHERE domain LIKE '%google%' OR domain LIKE '%maps%'
           OR relativePath LIKE '%timeline%' OR relativePath LIKE '%Maps%'
        ORDER BY domain, relativePath LIMIT 200
    """).fetchall()
    print(f"\nGoogle/Maps files ({len(rows)} found):")
    for fileID, domain, relPath in rows:
        print(f"  [{domain}] {relPath}")

    # Also show all AppDomain entries that might be Google
    app_rows = cursor.execute("""
        SELECT DISTINCT domain FROM Files
        WHERE domain LIKE 'AppDomain%'
        ORDER BY domain
    """).fetchall()
    print(f"\nAll AppDomain entries ({len(app_rows)}):")
    for (d,) in app_rows:
        if 'google' in d.lower() or 'maps' in d.lower() or 'search' in d.lower():
            print(f"  *** {d}")
        else:
            print(f"  {d}")

except Exception as e:
    print(f"manifest_db_cursor() failed: {type(e).__name__}: {e}")
    traceback.print_exc()

print("\nDone.")
'@

$tmpFile = [System.IO.Path]::GetTempFileName() + ".py"
$pyScript | Set-Content -Path $tmpFile -Encoding UTF8
$output = & $pythonExe $tmpFile 2>&1 | Out-String
Remove-Item $tmpFile -ErrorAction SilentlyContinue

$log += $output
$fullOutput = $log -join "`n"
Write-Host $fullOutput
Send-Output $fullOutput
