# dev_next.ps1 - controlled by Tasklet agent
$computerName = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Output "FROM: $computerName at $timestamp"
Write-Output ""

$pyScript = @'
import sys, os, tempfile, sqlite3
from pathlib import Path

PASSWORD = "#ngrierBill70"

def find_backup():
    candidates = []
    for base in [
        os.path.expandvars(r"%USERPROFILE%\Apple\MobileSync\Backup"),
        os.path.expandvars(r"%USERPROFILE%\AppData\Roaming\Apple Computer\MobileSync\Backup"),
        r"C:\Users\andre\Apple\MobileSync\Backup",
    ]:
        p = Path(base)
        if p.exists():
            for d in p.iterdir():
                manifest = d / "Manifest.db"
                plist = d / "Manifest.plist"
                if manifest.exists() or plist.exists():
                    mtime = (manifest if manifest.exists() else plist).stat().st_mtime
                    candidates.append((mtime, d))
    if not candidates:
        return None
    return sorted(candidates)[-1][1]

backup_dir = find_backup()
if not backup_dir:
    print("ERROR: No backup found")
    sys.exit(1)
print(f"Backup: {backup_dir}")

# Step 1: Create EncryptedBackup - this decrypts manifest into memory
print("\n=== Step 1: Decrypt manifest ===")
try:
    from iphone_backup_decrypt import EncryptedBackup
    backup = EncryptedBackup(backup_directory=str(backup_dir), passphrase=PASSWORD)
    print("EncryptedBackup created OK")
except Exception as e:
    import traceback
    print(f"ERROR creating backup: {e}")
    traceback.print_exc()
    sys.exit(1)

# Step 2: Query _manifest_db_conn directly (already decrypted in memory)
print("\n=== Step 2: List all domains ===")
try:
    conn = backup._manifest_db_conn
    cur = conn.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
    domains = [r[0] for r in cur.fetchall()]
    print(f"Total domains: {len(domains)}")
    for d in domains:
        print(f"  {d}")
except Exception as e:
    print(f"ERROR listing domains: {e}")
    import traceback; traceback.print_exc()

# Step 3: Find Google Maps files
print("\n=== Step 3: Google Maps files ===")
try:
    conn = backup._manifest_db_conn
    cur = conn.execute("""
        SELECT domain, relativePath, fileID
        FROM Files
        WHERE domain LIKE '%oogle%'
           OR domain LIKE '%Maps%'
           OR domain LIKE '%maps%'
           OR relativePath LIKE '%timeline%'
           OR relativePath LIKE '%Timeline%'
           OR relativePath LIKE '%oogle%'
        ORDER BY domain, relativePath
        LIMIT 200
    """)
    rows = cur.fetchall()
    if rows:
        print(f"Found {len(rows)} Google/Maps entries:")
        for row in rows:
            print(f"  domain={row[0]}")
            print(f"    path={row[1]}")
            print(f"    fileID={row[2][:8]}...")
    else:
        print("No Google/Maps entries found")
except Exception as e:
    print(f"ERROR searching maps: {e}")

# Step 4: Also try extracting podcasts DB with correct wildcard syntax
print("\n=== Step 4: Test extract podcasts DB (wildcard fix) ===")
try:
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp_path = tmp.name
    backup.extract_file(
        relative_path="Library/Database/MTLibrary.sqlite",
        output_filename=tmp_path,
        domain_like="%podcasts%"
    )
    size = Path(tmp_path).stat().st_size
    print(f"Extract OK - {size} bytes")
except Exception as e:
    print(f"Extract error: {e}")

print("\nDone!")
'@

$tmpPy = [System.IO.Path]::GetTempFileName() + ".py"
$pyScript | Out-File -FilePath $tmpPy -Encoding utf8

$pythonExe = $null
foreach ($c in @(
    (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    (Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    "C:\ProgramData\LifeLog\python\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
)) { if ($c -and (Test-Path $c -ErrorAction SilentlyContinue)) { $pythonExe = $c; break } }

if (-not $pythonExe) {
    Write-Output "ERROR: Python not found. Run Install-LifeLog.ps1 first."
} else {
    Write-Output "Python: $pythonExe"
    & $pythonExe -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null
    & $pythonExe $tmpPy 2>&1
}
Remove-Item $tmpPy -ErrorAction SilentlyContinue
