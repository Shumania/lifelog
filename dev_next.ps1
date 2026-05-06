$WEBHOOK = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

function Send-Output($text) {
    $body = @{ log = $text; exitCode = 0 } | ConvertTo-Json -Depth 3
    try { Invoke-RestMethod -Uri $WEBHOOK -Method POST -Body $body -ContentType 'application/json' | Out-Null } catch {}
}

$script = @'
import sys, os, subprocess, tempfile, sqlite3
subprocess.run([sys.executable, '-m', 'pip', 'install', 'iphone-backup-decrypt', 'biplist', '-q'], capture_output=True)

import iphone_backup_decrypt
import biplist

computer = os.environ.get('COMPUTERNAME', 'UNKNOWN')
from datetime import datetime
timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
lines = [f"FROM: {computer} at {timestamp}"]

# Find newest backup
backup_base = os.path.join(os.environ.get('USERPROFILE',''), 'Apple', 'MobileSync', 'Backup')
if not os.path.exists(backup_base):
    appdata = os.environ.get('APPDATA','')
    backup_base = os.path.join(os.path.dirname(appdata), 'Apple', 'Apple Application Support', 'MobileSync', 'Backup')
backups = [(os.path.getmtime(os.path.join(backup_base, d)), os.path.join(backup_base, d))
           for d in os.listdir(backup_base) if os.path.isdir(os.path.join(backup_base, d))]
backup_dir = sorted(backups)[-1][1]
lines.append(f"Backup: {backup_dir}")

# Unlock backup (lazy - triggered by first extract)
PASSWORD = '#ngrierBill70'
backup = iphone_backup_decrypt.EncryptedBackup(backup_directory=backup_dir, passphrase=PASSWORD)

tmp = tempfile.mkdtemp()

# Force unlock by extracting a known file (may fail with FileNotFoundError, that's OK)
try:
    backup.extract_file(relative_path='Library/Preferences/com.apple.podcasts.plist',
                        domain_like='AppDomainGroup%podcasts%',
                        output_filename=os.path.join(tmp, 'unlock_test.plist'))
except FileNotFoundError:
    pass  # Expected - but unlock has happened

lines.append(f"Unlocked: {backup._unlocked}")

# Query manifest DB for ALL Google Maps files
lines.append("\n=== ALL Google Maps files in backup ===")
try:
    conn = backup._manifest_db_conn
    cur = conn.cursor()
    cur.execute("SELECT domain, relativePath, flags, file FROM Files WHERE domain LIKE '%google%Maps%' ORDER BY relativePath")
    rows = cur.fetchall()
    lines.append(f"Total Google Maps files: {len(rows)}")
    for domain, rel_path, flags, file_blob in rows:
        lines.append(f"  [{flags}] {domain} / {rel_path}")
except Exception as e:
    lines.append(f"Manifest query error: {e}")
    # Fallback: read Manifest.db directly
    manifest_db = os.path.join(backup_dir, 'Manifest.db')
    if os.path.exists(manifest_db):
        conn2 = sqlite3.connect(manifest_db)
        cur2 = conn2.cursor()
        cur2.execute("SELECT domain, relativePath, flags FROM Files WHERE domain LIKE '%google%' ORDER BY relativePath")
        rows2 = cur2.fetchall()
        lines.append(f"Manifest.db Google files: {len(rows2)}")
        for domain, rel_path, flags in rows2:
            lines.append(f"  [{flags}] {domain} / {rel_path}")
        conn2.close()

# Try to parse the Maps plist with biplist
lines.append("\n=== Parsing com.google.Maps.plist with biplist ===")
try:
    plist_out = os.path.join(tmp, 'maps.plist')
    backup.extract_file(
        relative_path='Library/Preferences/com.google.Maps.plist',
        domain_like='AppDomain-com.google.Maps',
        output_filename=plist_out
    )
    with open(plist_out, 'rb') as f:
        data = f.read()
    plist_data = biplist.readPlistFromString(data)
    lines.append(f"Keys ({len(plist_data)} total):")
    for k in sorted(plist_data.keys()):
        v = plist_data[k]
        v_str = str(v)[:100]
        lines.append(f"  {k}: {v_str}")
except Exception as e:
    lines.append(f"biplist parse error: {e}")

# Also look for SQLite databases in the Google Maps domain
lines.append("\n=== Looking for SQLite databases in Google Maps domain ===")
try:
    conn = backup._manifest_db_conn
    cur = conn.cursor()
    cur.execute("SELECT domain, relativePath FROM Files WHERE domain LIKE '%google%Maps%' AND (relativePath LIKE '%.db' OR relativePath LIKE '%.sqlite' OR relativePath LIKE '%.sqlite3')")
    db_rows = cur.fetchall()
    lines.append(f"Found {len(db_rows)} database files")
    for domain, rel_path in db_rows:
        lines.append(f"  {domain} / {rel_path}")
        # Try to extract and check schema
        try:
            db_out = os.path.join(tmp, os.path.basename(rel_path))
            backup.extract_file(relative_path=rel_path, domain_like=domain, output_filename=db_out)
            conn3 = sqlite3.connect(db_out)
            tables = conn3.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            lines.append(f"    Tables: {[t[0] for t in tables]}")
            for (table,) in tables:
                count = conn3.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
                lines.append(f"      {table}: {count} rows")
            conn3.close()
        except Exception as e2:
            lines.append(f"    Error: {e2}")
except Exception as e:
    lines.append(f"DB search error: {e}")

print("\n".join(lines))
'@

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) { Send-Output "FROM: $computer at $timestamp`nERROR: Python not found"; exit 1 }

$tmpScript = [System.IO.Path]::GetTempFileName() + '.py'
$script | Set-Content $tmpScript -Encoding UTF8
$output = & $py.Source $tmpScript 2>&1 | Out-String
Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue
Send-Output $output
