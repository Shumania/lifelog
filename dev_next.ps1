# dev_next.ps1 v36 - WAL diagnostic
$Machine = $env:COMPUTERNAME
Write-Host "[$Machine] dev_next.ps1 v36 - WAL diagnostic"

# Find Python
$PythonPath = $null
$candidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\andre\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python314\python.exe",
    "C:\ProgramData\LifeLog\python\python.exe"
)
foreach ($p in $candidates) {
    if (Test-Path $p) { $PythonPath = $p; break }
}
Write-Host "[$Machine] Python: $PythonPath"

# Find backup dir
$backupRoots = @(
    "C:\Users\andre\Apple\MobileSync\Backup",
    "C:\Users\Shumadmin\Apple\MobileSync\Backup",
    "$env:APPDATA\Apple Computer\MobileSync\Backup",
    "$env:LOCALAPPDATA\Apple Computer\MobileSync\Backup"
)

$backupDir = $null
foreach ($root in $backupRoots) {
    if (Test-Path $root) {
        $dirs = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue
        foreach ($d in $dirs) {
            $backupDir = $d.FullName
            Write-Host "[$Machine] Found backup: $backupDir"
        }
    }
}

if (-not $backupDir) {
    Write-Host "[$Machine] ERROR: No backup directory found!"
} else {
    # Run Python diagnostic
    $diagScript = @"
import sys, os, json

backup_dir = r'$backupDir'
print(f'Backup dir: {backup_dir}')
print(f'Exists: {os.path.exists(backup_dir)}')

# Read manifest to find podcast DB files
manifest_path = os.path.join(backup_dir, 'Manifest.db')
if not os.path.exists(manifest_path):
    print('ERROR: Manifest.db not found!')
    sys.exit(1)

import sqlite3
conn = sqlite3.connect(manifest_path)
cur = conn.cursor()

# Find MTLibrary files
cur.execute("SELECT fileID, relativePath, flags, file FROM Files WHERE relativePath LIKE '%MTLibrary%' AND domain LIKE '%podcast%'")
rows = cur.fetchall()
print(f'MTLibrary files in manifest: {len(rows)}')
for r in rows:
    fid, path, flags, fileblob = r
    # Check actual file on disk
    file_path = os.path.join(backup_dir, fid[:2], fid)
    exists = os.path.exists(file_path)
    size = os.path.getsize(file_path) if exists else 0
    print(f'  fileID={fid[:8]}... path={path} exists={exists} size={size:,} bytes ({size//1024//1024}MB)')

conn.close()
"@
    $diagScript | & $PythonPath -
}

Write-Host "[$Machine] Diagnostic complete."
