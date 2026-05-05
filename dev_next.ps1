$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$machine = $env:COMPUTERNAME

$cmd = Get-Command python -ErrorAction SilentlyContinue
$pythonExe = if ($cmd) { $cmd.Source } else { $null }
if (-not $pythonExe) {
    $cmd3 = Get-Command python3 -ErrorAction SilentlyContinue
    $pythonExe = if ($cmd3) { $cmd3.Source } else { $null }
}

$pyVersion = if ($pythonExe) { & $pythonExe --version 2>&1 } else { "NOT FOUND" }

$script = @'
import os, sys, json, tempfile, traceback

password = "#ngrierBill70"

# Find backup dir
backup_dir = None
for base in [
    os.path.join(os.environ.get("USERPROFILE",""), "Apple", "MobileSync", "Backup"),
    os.path.join(os.environ.get("USERPROFILE",""), "AppData", "Roaming", "Apple Computer", "MobileSync", "Backup"),
]:
    if os.path.isdir(base):
        entries = [os.path.join(base, d) for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        if entries:
            backup_dir = max(entries, key=os.path.getmtime)
            break

print(f"Backup dir: {backup_dir}")

import subprocess
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "iphone_backup_decrypt"], capture_output=True)

from iphone_backup_decrypt import EncryptedBackup

backup = EncryptedBackup(backup_directory=backup_dir, password=password)
print(f"Backup object created: {type(backup)}")
print(f"Backup attrs: {[a for a in dir(backup) if not a.startswith('__')]}")

# First try extract_file to force unlock
print("\n=== Trying extract_file for podcasts (forces unlock) ===")
try:
    tmp = tempfile.mktemp(suffix=".sqlite")
    backup.extract_file(
        relative_name="AppDomainGroup-243LU875E5.groups.com.apple.podcasts/Library/Database/MTLibrary.sqlite",
        output_filename=tmp
    )
    print(f"extract_file succeeded, file size: {os.path.getsize(tmp)}")
except Exception as e:
    print(f"extract_file error: {repr(e)}")
    traceback.print_exc()

# Now try _open_manifest_db
print("\n=== Trying _open_manifest_db() ===")
try:
    backup._open_manifest_db()
    print("_open_manifest_db() succeeded")
    conn = backup._manifest_db_conn
    print(f"Connection: {conn}")
    cur = conn.execute("SELECT fileID, domain, relativePath FROM Files WHERE domain LIKE '%Google%' OR domain LIKE '%Maps%' OR relativePath LIKE '%google%' OR relativePath LIKE '%maps%' OR relativePath LIKE '%Maps%' LIMIT 50")
    rows = cur.fetchall()
    print(f"Google/Maps files found: {len(rows)}")
    for r in rows:
        print(f"  {r[1]} | {r[2]} | {r[0]}")
except Exception as e:
    print(f"_open_manifest_db error: {repr(e)}")
    traceback.print_exc()

print("\nDone!")
'@

$tmpScript = Join-Path $env:TEMP "gmaps_inspect_v5.py"
$script | Set-Content -Path $tmpScript -Encoding UTF8

$output = if ($pythonExe) {
    "Machine: $machine`nPython: $pythonExe ($pyVersion)`n`n" + (& $pythonExe $tmpScript 2>&1 | Out-String)
} else {
    "Machine: $machine`nPython NOT FOUND - run Install-LifeLog.ps1 first`n"
}

$body = @{ output = $output; timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); computer = $machine; source = "gmaps_inspect_v5" } | ConvertTo-Json
Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType "application/json" | Out-Null
