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

passphrase = "#ngrierBill70"  # v0.9 API uses passphrase=

# Find backup dir
backup_dir = None
for base in [
    os.path.join(os.environ.get("USERPROFILE",""), "Apple", "MobileSync", "Backup"),
    os.path.join(os.environ.get("APPDATA",""), "Apple Computer", "MobileSync", "Backup"),
]:
    if os.path.isdir(base):
        entries = [os.path.join(base, d) for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        if entries:
            backup_dir = max(entries, key=os.path.getmtime)
            break

print(f"Backup dir: {backup_dir}")
if not backup_dir:
    print("ERROR: No backup directory found!")
    sys.exit(1)

import subprocess
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "iphone_backup_decrypt"], capture_output=True)

from iphone_backup_decrypt import EncryptedBackup
print(f"iphone_backup_decrypt imported OK")

# Step 1: Create backup object and force unlock by extracting podcasts DB
print("\n=== Creating backup + extracting podcasts DB (forces manifest unlock) ===")
backup = EncryptedBackup(backup_directory=backup_dir, passphrase=passphrase)
tmp_podcasts = tempfile.mktemp(suffix="_podcasts.sqlite")
try:
    backup.extract_file(
        relative_path="Library/Database/MTLibrary.sqlite",
        output_filename=tmp_podcasts,
        domain_like="%groups.com.apple.podcasts"
    )
    size = os.path.getsize(tmp_podcasts) if os.path.exists(tmp_podcasts) else 0
    print(f"Podcasts DB extracted OK, size={size}")
except Exception as e:
    print(f"Podcasts extract error: {repr(e)}")
    traceback.print_exc()

# Step 2: Save manifest to temp file and open it to enumerate Google Maps files
print("\n=== Saving manifest DB and searching for Google Maps files ===")
tmp_manifest = tempfile.mktemp(suffix="_manifest.db")
try:
    backup.save_manifest_file(output_filename=tmp_manifest)
    print(f"save_manifest_file OK, size={os.path.getsize(tmp_manifest)}")
    import sqlite3
    conn = sqlite3.connect(tmp_manifest)
    cur = conn.execute("""
        SELECT fileID, domain, relativePath FROM Files
        WHERE domain LIKE '%Google%' OR domain LIKE '%Maps%'
           OR relativePath LIKE '%google%' OR relativePath LIKE '%Maps%'
           OR relativePath LIKE '%timeline%'
        LIMIT 100
    """)
    rows = cur.fetchall()
    print(f"Google/Maps files found: {len(rows)}")
    for r in rows:
        print(f"  {r[1]} | {r[2]} | {r[0]}")
    # Also dump all unique domains for context
    cur2 = conn.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
    domains = [r[0] for r in cur2.fetchall()]
    print(f"\n=== All domains in backup ({len(domains)} total) ===")
    for d in domains:
        print(f"  {d}")
    conn.close()
except Exception as e:
    print(f"Manifest error: {repr(e)}")
    traceback.print_exc()

print("\nDone!")
'@

$tmpScript = Join-Path $env:TEMP "gmaps_inspect_v6.py"
$script | Set-Content -Path $tmpScript -Encoding UTF8

$output = if ($pythonExe) {
    "Machine: $machine`nPython: $pythonExe ($pyVersion)`n`n" + (& $pythonExe $tmpScript 2>&1 | Out-String)
} else {
    "Machine: $machine`nPython NOT FOUND - run Install-LifeLog.ps1 first`n"
}

$body = @{ output = $output; timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); computer = $machine; source = "gmaps_inspect_v6" } | ConvertTo-Json -Depth 3
Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType "application/json" | Out-Null
