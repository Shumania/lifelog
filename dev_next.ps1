$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computerName = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

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

# Step 1: Unlock by extracting podcasts DB
print("\n=== Step 1: Unlock backup ===")
try:
    from iphone_backup_decrypt import EncryptedBackup
    backup = EncryptedBackup(backup_directory=str(backup_dir), passphrase=PASSWORD)
    
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp_path = tmp.name
    
    backup.extract_file(
        relative_path="Library/Database/MTLibrary.sqlite",
        output_filename=tmp_path,
        domain_like="AppDomainGroup-243LU875E5.groups.com.apple.podcasts"
    )
    
    if Path(tmp_path).exists() and Path(tmp_path).stat().st_size > 0:
        print(f"Unlock SUCCESS - extracted podcasts DB ({Path(tmp_path).stat().st_size} bytes)")
    else:
        print("Unlock FAILED - empty output")
    
    # Step 2: Inspect backup object attributes
    print("\n=== Step 2: Backup object attributes ===")
    attrs = [a for a in dir(backup) if not a.startswith('__')]
    print("Attributes:", attrs)
    
    # Try common manifest access patterns
    for attr in ['_manifest_db', '_manifest_db_conn', '_db', 'manifest_db', '_conn']:
        if hasattr(backup, attr):
            val = getattr(backup, attr)
            print(f"  {attr} = {val}")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback; traceback.print_exc()

# Step 3: Try querying Manifest.db directly after unlock
print("\n=== Step 3: Query Manifest.db for Google Maps files ===")
manifest_path = backup_dir / "Manifest.db"
try:
    conn = sqlite3.connect(str(manifest_path))
    # Try to find Google Maps related files
    cur = conn.execute("""
        SELECT domain, relativePath, flags, fileID
        FROM Files
        WHERE domain LIKE '%google%' OR domain LIKE '%maps%'
           OR relativePath LIKE '%google%' OR relativePath LIKE '%maps%'
           OR relativePath LIKE '%timeline%' OR relativePath LIKE '%Maps%'
        LIMIT 50
    """)
    rows = cur.fetchall()
    if rows:
        print(f"Found {len(rows)} Google/Maps entries:")
        for row in rows:
            print(f"  domain={row[0]} path={row[1]} fileID={row[3][:8]}...")
    else:
        print("No Google Maps entries in manifest")
    
    # Also show all unique domains
    cur2 = conn.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
    domains = [r[0] for r in cur2.fetchall()]
    print(f"\nAll {len(domains)} domains in manifest:")
    for d in domains:
        print(f"  {d}")
    conn.close()
except Exception as e:
    print(f"Direct manifest error: {e}")

print("\nDone!")
'@

try {
    $tmpPy = [System.IO.Path]::GetTempFileName() + ".py"
    $pyScript | Out-File -FilePath $tmpPy -Encoding utf8
    
    $pythonExe = Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
    if (-not $pythonExe) { $pythonExe = Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source }
    if (-not $pythonExe) {
        $candidates = @(
            "C:\\ProgramData\\LifeLog\\python\\python.exe",
            "C:\\Python312\\python.exe",
            "C:\\Python311\\python.exe",
            "$env:LOCALAPPDATA\\Programs\\Python\\Python312\\python.exe",
            "$env:LOCALAPPDATA\\Programs\\Python\\Python311\\python.exe"
        )
        foreach ($c in $candidates) { if (Test-Path $c) { $pythonExe = $c; break } }
    }
    
    if (-not $pythonExe) {
        $output = "ERROR: Python not found. Run Install-LifeLog.ps1 first."
    } else {
        # Ensure iphone_backup_decrypt is installed
        & $pythonExe -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null
        $output = & $pythonExe $tmpPy 2>&1 | Out-String
    }
    Remove-Item $tmpPy -ErrorAction SilentlyContinue
} catch {
    $output = "Script error: $_"
}

$body = "FROM: $computerName at $timestamp`n`n=== Script Output ===`n$output"

try {
    Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType "text/plain" | Out-Null
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Output sent to webhook"
} catch {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Webhook failed: $_"
}
