$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

$sysinfo = "=== SYSINFO ===`nComputer: $env:COMPUTERNAME | User: $env:USERNAME | Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# Find real Python (skip WindowsApps stub)
$pythonExe = $null
try {
    $allPython = @(where.exe python 2>$null)
    foreach ($p in $allPython) {
        if ($p -and (Test-Path $p) -and $p -notlike "*WindowsApps*") {
            $pythonExe = $p
            break
        }
    }
} catch {}

# Also check common install dirs for any Python version
if (-not $pythonExe) {
    $searchRoots = @("$env:LOCALAPPDATA\Programs\Python", "C:\Python")
    foreach ($root in $searchRoots) {
        if (Test-Path $root) {
            $found = Get-ChildItem $root -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found) { $pythonExe = $found.FullName; break }
        }
    }
}

if (-not $pythonExe) {
    $msg = "$sysinfo`n`nERROR: No real Python found. Please install from https://python.org"
    Invoke-RestMethod -Uri $webhookUrl -Method POST -ContentType 'application/json' -Body (@{source='LifeLog-DevLoop';timestamp=(Get-Date -Format 'yyyy-MM-dd HH:mm:ss');computer=$env:COMPUTERNAME;output=$msg} | ConvertTo-Json)
    exit
}

Write-Host "Python: $pythonExe"

# Find backup
$backupRoots = @(
    "$env:USERPROFILE\Apple\MobileSync\Backup",
    "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup"
)
$backupDir = $null
foreach ($root in $backupRoots) {
    if (Test-Path $root) {
        $backupDir = Get-ChildItem $root -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
        if ($backupDir) { break }
    }
}

$script = @'
import sys, os

backup_path = sys.argv[1]
print(f"Backup: {backup_path}")

try:
    from iphone_backup_decrypt import EncryptedBackup
except ImportError:
    print("ERROR: iphone_backup_decrypt not installed. Run: pip install iphone-backup-decrypt")
    sys.exit(1)

password = "#ngrierBill70"

try:
    backup = EncryptedBackup(backup_directory=backup_path, passphrase=password)
    print("Unlocking keybag...")
    result = backup.test_decryption()
    print(f"test_decryption() result: {result}")
    print(f"_unlocked: {backup._unlocked}")
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
        
        print("\n=== GOOGLE MAPS FILES ===")
        maps_files = cursor.execute(
            "SELECT domain, relativePath FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR domain LIKE '%Maps%' OR relativePath LIKE '%google%' OR relativePath LIKE '%Maps%'"
        ).fetchall()
        print(f"Found {len(maps_files)} Google/Maps related files")
        for row in maps_files[:50]:
            print(f"  domain={row[0]} | path={row[1]}")
except Exception as e:
    import traceback
    print(f"manifest query failed: {e}")
    traceback.print_exc()

print("\nDone.")
'@

$tmpFile = [System.IO.Path]::GetTempFileName() + ".py"
$script | Out-File -FilePath $tmpFile -Encoding utf8

$output = "$sysinfo`n`nPython: $pythonExe`n"

if (-not $backupDir) {
    $output += "ERROR: No backup directory found"
} else {
    & $pythonExe -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null
    $output += & $pythonExe $tmpFile $backupDir 2>&1 | Out-String
}

Remove-Item $tmpFile -ErrorAction SilentlyContinue

Invoke-RestMethod -Uri $webhookUrl -Method POST -ContentType 'application/json' -Body (@{
    source = 'LifeLog-DevLoop'
    timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    computer = $env:COMPUTERNAME
    output = $output
} | ConvertTo-Json -Depth 3)
