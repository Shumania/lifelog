# dev_next.ps1 - controlled by Tasklet agent
# Prints all output to stdout; dev loop captures and posts to webhook.
# Do NOT call Invoke-RestMethod here - that causes double-post and overwrites good data.

$sysinfo = "FROM: $env:COMPUTERNAME at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Output $sysinfo

# Quick sanity check - does python work?
try {
    $pyVer = & python --version 2>&1 | Out-String
    Write-Output "Python: $($pyVer.Trim())"
} catch {
    Write-Output "ERROR: 'python' not found in PATH. Install from https://python.org and check 'Add to PATH'"
    exit
}

# Find backup with Manifest.plist
$backupRoots = @(
    "$env:USERPROFILE\Apple\MobileSync\Backup",
    "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup"
)
Write-Output "`n=== BACKUP SEARCH ==="
$backupDir = $null
foreach ($root in $backupRoots) {
    if (Test-Path $root) {
        Write-Output "Searching: $root"
        $candidates = Get-ChildItem $root -Directory | Sort-Object LastWriteTime -Descending
        foreach ($c in $candidates) {
            $hasManifest = Test-Path (Join-Path $c.FullName "Manifest.plist")
            Write-Output "  $($c.Name) | Modified: $($c.LastWriteTime) | HasManifest: $hasManifest"
            if ($hasManifest -and -not $backupDir) {
                $backupDir = $c.FullName
            }
        }
    }
}

if (-not $backupDir) {
    Write-Output "ERROR: No valid backup found with Manifest.plist"
    exit
}

Write-Output "Selected: $backupDir"

$script = @'
import sys, os
backup_path = sys.argv[1]
print(f"Backup: {backup_path}")

try:
    from iphone_backup_decrypt import EncryptedBackup
except ImportError:
    print("ERROR: iphone_backup_decrypt not installed")
    sys.exit(1)

try:
    backup = EncryptedBackup(backup_directory=backup_path, passphrase="#ngrierBill70")
    print("Unlocking keybag...")
    backup.test_decryption()
    print("Unlocked OK")
except Exception as e:
    print(f"ERROR during unlock: {e}")
    sys.exit(1)

print("\nQuerying manifest...")
try:
    with backup.manifest_db_cursor() as cursor:
        domains = cursor.execute("SELECT DISTINCT domain FROM Files ORDER BY domain").fetchall()
        print(f"Found {len(domains)} distinct domains")
        print("\n=== ALL DOMAINS ===")
        for (d,) in domains:
            print(f"  {d}")
        print("\n=== GOOGLE/MAPS FILES ===")
        maps = cursor.execute(
            "SELECT domain, relativePath FROM Files WHERE domain LIKE '%oogle%' OR domain LIKE '%maps%' OR domain LIKE '%Maps%' OR relativePath LIKE '%oogle%' OR relativePath LIKE '%Maps%'"
        ).fetchall()
        print(f"Found {len(maps)} Google/Maps files")
        for r in maps[:100]:
            print(f"  {r[0]} | {r[1]}")
except Exception as e:
    import traceback
    print(f"Manifest query failed: {e}")
    traceback.print_exc()
print("Done.")
'@

$tmpFile = [System.IO.Path]::GetTempFileName() + ".py"
$script | Out-File -FilePath $tmpFile -Encoding utf8

python -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null
$pyOut = & python $tmpFile $backupDir 2>&1 | Out-String
Remove-Item $tmpFile -ErrorAction SilentlyContinue

Write-Output $pyOut
