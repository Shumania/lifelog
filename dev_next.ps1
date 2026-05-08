# dev_next.ps1 v34 - diagnostic: check decrypt lib version + date range in DB
Write-Host "[SHUMAFRAME] dev_next.ps1 v34"

$PythonPath = $null
$candidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\ProgramData\LifeLog\python\python.exe"
)
foreach ($p in $candidates) {
    if (Test-Path $p) { $PythonPath = $p; break }
}
if (-not $PythonPath) {
    throw "[SHUMAFRAME] Python not found in any known location."
}
Write-Host "[SHUMAFRAME] Using Python: $PythonPath"

$BackupBase = $null
$backupRoots = @(
    "C:\Users\andre\Apple\MobileSync\Backup",
    "C:\Users\Shumadmin\Apple\MobileSync\Backup",
    "$env:APPDATA\Apple Computer\MobileSync\Backup"
)
foreach ($root in $backupRoots) {
    if (Test-Path $root) {
        $dirs = Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue
        if ($dirs) { $BackupBase = $dirs[0].FullName; break }
    }
}
if (-not $BackupBase) { throw "[SHUMAFRAME] No backup folder found." }
Write-Host "[SHUMAFRAME] Backup: $BackupBase"

$diagScript = @'
import sys
import subprocess

# Check iphone_backup_decrypt version
try:
    import iphone_backup_decrypt
    v = getattr(iphone_backup_decrypt, '__version__', 'unknown')
    print(f"iphone_backup_decrypt version: {v}")
    # Try to get version from pip
    result = subprocess.run([sys.executable, "-m", "pip", "show", "iphone-backup-decrypt"], 
                          capture_output=True, text=True)
    print(result.stdout)
except ImportError as e:
    print(f"iphone_backup_decrypt not importable: {e}")

import os, sys
backup_path = sys.argv[1]
password = "#ngrierBill70"

print(f"\nAttempting full decrypt of podcast DB...")
print(f"Backup: {backup_path}")

try:
    from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike
    backup = EncryptedBackup(backup_directory=backup_path, passphrase=password)
    
    import tempfile, sqlite3
    with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as f:
        tmp = f.name
    
    backup.extract_file(
        relative_path="Library/Application Support/com.apple.podcasts/Documents/MTLibrary.sqlite",
        domain="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
        output_filename=tmp
    )
    
    file_size = os.path.getsize(tmp)
    print(f"Decrypted file size: {file_size:,} bytes ({file_size/1024/1024:.1f} MB)")
    
    conn = sqlite3.connect(tmp)
    cur = conn.cursor()
    
    # Count episodes
    cur.execute("SELECT COUNT(*) FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL")
    count = cur.fetchone()[0]
    print(f"Episodes with ZLASTDATEPLAYED: {count}")
    
    # Date range
    cur.execute("SELECT MIN(ZLASTDATEPLAYED), MAX(ZLASTDATEPLAYED) FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL")
    mn, mx = cur.fetchone()
    if mn and mx:
        import datetime
        apple_epoch = datetime.datetime(2001, 1, 1)
        min_dt = apple_epoch + datetime.timedelta(seconds=mn)
        max_dt = apple_epoch + datetime.timedelta(seconds=mx)
        print(f"Earliest played: {min_dt}")
        print(f"Latest played:   {max_dt}")
    
    # Show top 5 most recent
    cur.execute("""
        SELECT ZTITLE, ZLASTDATEPLAYED 
        FROM ZMTEPISODE 
        WHERE ZLASTDATEPLAYED IS NOT NULL 
        ORDER BY ZLASTDATEPLAYED DESC 
        LIMIT 5
    """)
    rows = cur.fetchall()
    print("\nTop 5 most recent episodes:")
    for r in rows:
        ts = apple_epoch + datetime.timedelta(seconds=r[1])
        print(f"  {ts.strftime('%Y-%m-%d')} - {r[0][:60]}")
    
    conn.close()
    os.unlink(tmp)
    
except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()
'@

$diagFile = "C:\ProgramData\LifeLog\diag_decrypt.py"
$diagScript | Out-File -FilePath $diagFile -Encoding UTF8
Write-Host "[SHUMAFRAME] Running diagnostic..."
& $PythonPath $diagFile $BackupBase
Write-Host "[SHUMAFRAME] Done."
