$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$sysinfo = "=== SYSINFO ===`nComputer: $env:COMPUTERNAME | User: $env:USERNAME | Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# Find Python
$pythonExe = $null
try {
    $allPython = @(where.exe python 2>$null)
    foreach ($p in $allPython) {
        if ($p -and (Test-Path $p) -and $p -notlike "*WindowsApps*") { $pythonExe = $p; break }
    }
} catch {}
if (-not $pythonExe) {
    foreach ($root in @("$env:LOCALAPPDATA\Programs\Python", "$env:LOCALAPPDATA\Python", "C:\Python")) {
        if (Test-Path $root) {
            $found = Get-ChildItem $root -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notlike '*WindowsApps*' } | Select-Object -First 1
            if ($found) { $pythonExe = $found.FullName; break }
        }
    }
}

if (-not $pythonExe) {
    $msg = "$sysinfo`n`nERROR: No real Python found."
    Invoke-RestMethod -Uri $webhookUrl -Method POST -ContentType 'application/json' -Body (@{source='LifeLog-DevLoop';timestamp=(Get-Date -Format 'yyyy-MM-dd HH:mm:ss');computer=$env:COMPUTERNAME;output=$msg} | ConvertTo-Json)
    exit
}

# Find backup with Manifest.plist
$backupRoots = @(
    "$env:USERPROFILE\Apple\MobileSync\Backup",
    "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup"
)
$backupInfo = "\n=== BACKUP SEARCH ===\n"
$backupDir = $null
foreach ($root in $backupRoots) {
    if (Test-Path $root) {
        $backupInfo += "Searching: $root\n"
        $candidates = Get-ChildItem $root -Directory | Sort-Object LastWriteTime -Descending
        foreach ($c in $candidates) {
            $hasManifest = Test-Path (Join-Path $c.FullName "Manifest.plist")
            $backupInfo += "  $($c.Name) | Modified: $($c.LastWriteTime) | HasManifest: $hasManifest\n"
            if ($hasManifest -and -not $backupDir) {
                $backupDir = $c.FullName
            }
        }
    }
}

if (-not $backupDir) {
    $msg = "$sysinfo`nPython: $pythonExe$backupInfo\nERROR: No valid backup found with Manifest.plist"
    Invoke-RestMethod -Uri $webhookUrl -Method POST -ContentType 'application/json' -Body (@{source='LifeLog-DevLoop';timestamp=(Get-Date -Format 'yyyy-MM-dd HH:mm:ss');computer=$env:COMPUTERNAME;output=$msg} | ConvertTo-Json)
    exit
}

$backupInfo += "Selected: $backupDir\n"

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
        for r in maps[:50]:
            print(f"  {r[0]} | {r[1]}")
except Exception as e:
    import traceback
    print(f"Manifest query failed: {e}")
    traceback.print_exc()
print("Done.")
'@

$tmpFile = [System.IO.Path]::GetTempFileName() + ".py"
$script | Out-File -FilePath $tmpFile -Encoding utf8

& $pythonExe -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null
$pyOut = & $pythonExe $tmpFile $backupDir 2>&1 | Out-String
Remove-Item $tmpFile -ErrorAction SilentlyContinue

$output = "$sysinfo`nPython: $pythonExe$backupInfo`n$pyOut"
Invoke-RestMethod -Uri $webhookUrl -Method POST -ContentType 'application/json' -Body (@{
    source='LifeLog-DevLoop'; timestamp=(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); computer=$env:COMPUTERNAME; output=$output
} | ConvertTo-Json -Depth 3)
