$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) {
    $body = @{ computer=$computer; timestamp=$timestamp; exitCode=1; log="Python not found in PATH" } | ConvertTo-Json
    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json'
    return
}

$tmpScript = [System.IO.Path]::GetTempFileName() + ".py"
@'
import sys, os, glob, sqlite3, tempfile
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    from iphone_backup_decrypt import EncryptedBackup
except ImportError:
    print("ERROR: iphone_backup_decrypt not installed")
    sys.exit(1)

# Find most recent backup
base_dirs = []
for env_var in ['USERPROFILE', 'LOCALAPPDATA']:
    base = os.environ.get(env_var, '')
    if base:
        for pattern in [
            os.path.join(base, 'Apple', 'MobileSync', 'Backup', '*'),
            os.path.join(base, 'Apple Computer', 'MobileSync', 'Backup', '*'),
        ]:
            base_dirs.extend(glob.glob(pattern))

backup_path = None
latest_mtime = 0
for d in base_dirs:
    if os.path.isdir(d):
        mtime = os.path.getmtime(d)
        if mtime > latest_mtime:
            latest_mtime = mtime
            backup_path = d

if not backup_path:
    print("ERROR: No backup found")
    sys.exit(1)

print(f"Backup path: {backup_path}")

# Unlock backup by extracting podcasts DB (correct path from lifelog_extract.py)
password = "#ngrierBill70"
backup = EncryptedBackup(backup_directory=backup_path, passphrase=password)

print("Extracting podcasts DB to unlock backup...")
tmpdir = tempfile.mkdtemp()
try:
    backup.extract_file(
        relative_path="Documents/MTLibrary.sqlite",
        domain_like="%groups.com.apple.podcasts",
        output_filename=os.path.join(tmpdir, "podcasts.sqlite")
    )
    print("Unlock successful via Documents/MTLibrary.sqlite!")
except FileNotFoundError:
    print("Primary path not found, trying fallback...")
    try:
        backup.extract_file(
            relative_path="Documents/MTLibrary.sqlite",
            domain_like="AppDomain-com.apple.podcasts",
            output_filename=os.path.join(tmpdir, "podcasts.sqlite")
        )
        print("Unlock successful via AppDomain fallback!")
    except FileNotFoundError:
        print("Both podcast DB paths failed - trying to proceed anyway")
except Exception as e:
    print(f"Unlock error: {e}")

# Dump ALL attributes of backup object to find manifest connection
print("\n=== BACKUP OBJECT ATTRIBUTES ===")
for attr in sorted(dir(backup)):
    if attr.startswith('__'):
        continue
    try:
        val = getattr(backup, attr)
        val_type = type(val).__name__
        if callable(val):
            print(f"  {attr}: [method]")
        else:
            val_str = str(val)
            if len(val_str) > 200:
                val_str = val_str[:200] + '...'
            print(f"  {attr} ({val_type}): {val_str}")
    except Exception as e:
        print(f"  {attr}: ERROR - {e}")

# Try to find sqlite connections on the backup object
print("\n=== LOOKING FOR SQLITE CONNECTIONS ===")
for attr in dir(backup):
    if attr.startswith('__'):
        continue
    try:
        val = getattr(backup, attr)
        if isinstance(val, sqlite3.Connection):
            print(f"Found sqlite3.Connection: {attr}")
            cur = val.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            print(f"  Tables: {tables}")
            if 'Files' in tables:
                cur.execute("SELECT domain, relativePath FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR relativePath LIKE '%google%' OR relativePath LIKE '%maps%' ORDER BY relativePath LIMIT 50")
                rows = cur.fetchall()
                print(f"  Google/Maps files ({len(rows)}):")
                for row in rows:
                    print(f"    {row[0]} | {row[1]}")
    except:
        pass

# Scan temp dirs for any manifest .sqlite or .db files created recently
print("\n=== TEMP DIR SQLITE FILES ===")
for tmp in [tempfile.gettempdir(), tmpdir]:
    for f in glob.glob(os.path.join(tmp, '**', '*.sqlite'), recursive=True) + glob.glob(os.path.join(tmp, '**', '*.db'), recursive=True):
        try:
            size = os.path.getsize(f)
            print(f"  {f} (size={size})")
            try:
                conn = sqlite3.connect(f)
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in cur.fetchall()]
                conn.close()
                print(f"    Tables: {tables}")
                if 'Files' in tables:
                    conn2 = sqlite3.connect(f)
                    cur2 = conn2.cursor()
                    cur2.execute("SELECT domain, relativePath FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR relativePath LIKE '%google%' OR relativePath LIKE '%maps%' ORDER BY relativePath LIMIT 50")
                    rows = cur2.fetchall()
                    conn2.close()
                    print(f"    Google/Maps files ({len(rows)}):")
                    for row in rows:
                        print(f"      {row[0]} | {row[1]}")
            except Exception as e:
                print(f"    Cannot open: {e}")
        except:
            pass

print("\nDone.")
'@ | Set-Content $tmpScript -Encoding UTF8

try {
    $output = & $py.Source $tmpScript 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
} catch {
    $output = "Exception: $_"
    $exitCode = 1
} finally {
    Remove-Item $tmpScript -ErrorAction SilentlyContinue
}

$body = @{ computer=$computer; timestamp=$timestamp; exitCode=$exitCode; log=$output } | ConvertTo-Json -Depth 3
Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json'
