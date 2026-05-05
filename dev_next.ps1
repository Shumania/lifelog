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

from iphone_backup_decrypt import EncryptedBackup
print("Constructing EncryptedBackup...")
backup = EncryptedBackup(backup_directory=str(backup_dir), passphrase=PASSWORD)

# Check unlock status
print(f"_unlocked = {backup._unlocked}")
print(f"_temp_decrypted_manifest_db_path = {backup._temp_decrypted_manifest_db_path}")
print(f"_temporary_folder = {backup._temporary_folder}")

# Check if temp decrypted manifest exists
tmp_manifest = backup._temp_decrypted_manifest_db_path
if tmp_manifest and Path(tmp_manifest).exists():
    size = Path(tmp_manifest).stat().st_size
    print(f"Decrypted manifest exists! Size = {size} bytes")
    try:
        conn = sqlite3.connect(tmp_manifest)
        cur = conn.execute("SELECT COUNT(*) FROM Files")
        total = cur.fetchone()[0]
        print(f"Total files in manifest: {total}")
        # Find Google/Maps related
        cur = conn.execute("""
            SELECT domain, relativePath FROM Files
            WHERE domain LIKE '%oogle%' OR domain LIKE '%Maps%' OR domain LIKE '%maps%'
               OR relativePath LIKE '%timeline%' OR relativePath LIKE '%Timeline%'
               OR relativePath LIKE '%GoogleMaps%'
            LIMIT 100
        """)
        rows = cur.fetchall()
        print(f"\nGoogle/Maps files ({len(rows)} found):")
        for r in rows:
            print(f"  {r[0]} | {r[1]}")
        conn.close()
    except Exception as e:
        print(f"sqlite3 error on decrypted manifest: {e}")
else:
    print(f"Decrypted manifest NOT found at: {tmp_manifest}")

# Try test_decryption()
print("\n=== test_decryption() ===")
try:
    result = backup.test_decryption()
    print(f"test_decryption result: {result}")
except Exception as e:
    print(f"test_decryption error: {e}")

# Try manifest_db_cursor() public method
print("\n=== manifest_db_cursor() ===")
try:
    cur = backup.manifest_db_cursor()
    print(f"Got cursor: {cur}")
    results = cur.execute("""
        SELECT domain, relativePath FROM Files
        WHERE domain LIKE '%oogle%' OR domain LIKE '%Maps%'
        LIMIT 50
    """).fetchall()
    print(f"Google/Maps rows: {len(results)}")
    for r in results:
        print(f"  {r[0]} | {r[1]}")
except Exception as e:
    print(f"manifest_db_cursor error: {e}")

# Scan temp folder for any decrypted files
print("\n=== Temp folder contents ===")
try:
    tmp_folder = backup._temporary_folder
    if tmp_folder and Path(tmp_folder).exists():
        files = list(Path(tmp_folder).iterdir())
        print(f"{len(files)} files in temp folder: {tmp_folder}")
        for f in files[:20]:
            print(f"  {f.name} ({f.stat().st_size} bytes)")
    else:
        print(f"Temp folder empty or missing: {tmp_folder}")
except Exception as e:
    print(f"Temp folder error: {e}")

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
