$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$sysinfo = "=== SYSINFO ===`nComputer: $env:COMPUTERNAME | User: $env:USERNAME | Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# Just use 'python' from PATH directly - works whether it's a stub or real install
$pythonExe = "python"

# Quick sanity check - does it work?
try {
    $pyVer = & python --version 2>&1 | Out-String
    $sysinfo += "`nPython: $($pyVer.Trim())"
} catch {
    # Report failure and exit
    $msg = "$sysinfo`n`nERROR: 'python' not found in PATH. Install from https://python.org and check 'Add to PATH'"
    Invoke-RestMethod -Uri $webhookUrl -Method POST -ContentType 'application/json' -Body (@{source='LifeLog-DevLoop';timestamp=(Get-Date -Format 'yyyy-MM-dd HH:mm:ss');computer=$env:COMPUTERNAME;output=$msg} | ConvertTo-Json)
    exit
}

# Find backup with Manifest.plist
$backupRoots = @(
    "$env:USERPROFILE\Apple\MobileSync\Backup",
    "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup"
)
$backupInfo = "`n=== BACKUP SEARCH ==="
$backupDir = $null
foreach ($root in $backupRoots) {
    if (Test-Path $root) {
        $backupInfo += "`nSearching: $root"
        $candidates = Get-ChildItem $root -Directory | Sort-Object LastWriteTime -Descending
        foreach ($c in $candidates) {
            $hasManifest = Test-Path (Join-Path $c.FullName "Manifest.plist")
            $backupInfo += "`n  $($c.Name) | Modified: $($c.LastWriteTime) | HasManifest: $hasManifest"
            if ($hasManifest -and -not $backupDir) {
                $backupDir = $c.FullName
            }
        }
    }
}

if (-not $backupDir) {
    $msg = "$sysinfo$backupInfo`n`nERROR: No valid backup found with Manifest.plist"
    Invoke-RestMethod -Uri $webhookUrl -Method POST -ContentType 'application/json' -Body (@{source='LifeLog-DevLoop';timestamp=(Get-Date -Format 'yyyy-MM-dd HH:mm:ss');computer=$env:COMPUTERNAME;output=$msg} | ConvertTo-Json)
    exit
}

$backupInfo += "`nSelected: $backupDir"

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

$output = "$sysinfo$backupInfo`n`n$pyOut"
Invoke-RestMethod -Uri $webhookUrl -Method POST -ContentType 'application/json' -Body (@{
    source='LifeLog-DevLoop'; timestamp=(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); computer=$env:COMPUTERNAME; output=$output
} | ConvertTo-Json -Depth 3)
