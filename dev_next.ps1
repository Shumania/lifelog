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

# Step 1: Print all attributes of EncryptedBackup to understand its API
print("\n=== Step 1: EncryptedBackup attributes ===")
try:
    from iphone_backup_decrypt import EncryptedBackup
    backup = EncryptedBackup(backup_directory=str(backup_dir), passphrase=PASSWORD)
    attrs = [a for a in dir(backup) if not a.startswith('__')]
    print(f"Attributes: {attrs}")
    # Check _manifest_db_path specifically
    if hasattr(backup, '_manifest_db_path'):
        p = backup._manifest_db_path
        print(f"_manifest_db_path = {p}")
        if p and Path(p).exists():
            print(f"  -> File exists, size={Path(p).stat().st_size}")
            # Try connecting
            try:
                conn = sqlite3.connect(p)
                cur = conn.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
                domains = [r[0] for r in cur.fetchall()]
                print(f"  -> {len(domains)} domains found")
                for d in domains:
                    print(f"     {d}")
                conn.close()
            except Exception as e:
                print(f"  -> sqlite3 connect error: {e}")
        else:
            print(f"  -> File does not exist at that path")
except Exception as e:
    import traceback
    print(f"ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)

# Step 2: Try extracting podcasts DB to force unlock
print("\n=== Step 2: Extract podcasts DB ===")
try:
    import inspect
    sig = inspect.signature(backup.extract_file)
    print(f"extract_file signature: {sig}")
    
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp_path = tmp.name
    
    # Try different domain_like values
    for domain_like in ["AppDomainGroup-243LU875E5.groups.com.apple.podcasts", "%podcasts%", None]:
        try:
            print(f"Trying domain_like={domain_like!r}")
            if domain_like is None:
                backup.extract_file(
                    relative_path="Library/Database/MTLibrary.sqlite",
                    output_filename=tmp_path
                )
            else:
                backup.extract_file(
                    relative_path="Library/Database/MTLibrary.sqlite",
                    output_filename=tmp_path,
                    domain_like=domain_like
                )
            size = Path(tmp_path).stat().st_size
            print(f"  -> Success! {size} bytes")
            break
        except Exception as e:
            print(f"  -> Failed: {e}")
except Exception as e:
    import traceback
    print(f"ERROR in extract: {e}")
    traceback.print_exc()

# Step 3: After extraction, try manifest path again
print("\n=== Step 3: Manifest after extraction ===")
try:
    if hasattr(backup, '_manifest_db_path'):
        p = backup._manifest_db_path
        print(f"_manifest_db_path = {p}")
        if p and Path(p).exists():
            conn = sqlite3.connect(p)
            cur = conn.execute("SELECT COUNT(*) FROM Files")
            print(f"Files count: {cur.fetchone()[0]}")
            # Find Google Maps
            cur = conn.execute("""
                SELECT domain, relativePath FROM Files
                WHERE domain LIKE '%oogle%' OR domain LIKE '%aps%'
                   OR relativePath LIKE '%timeline%' OR relativePath LIKE '%Timeline%'
                LIMIT 50
            """)
            rows = cur.fetchall()
            print(f"Google/Maps rows: {len(rows)}")
            for r in rows:
                print(f"  {r[0]} | {r[1]}")
            conn.close()
except Exception as e:
    print(f"ERROR: {e}")

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
