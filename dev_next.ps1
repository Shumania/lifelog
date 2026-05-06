$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

$sysinfo = "=== SYSINFO ===`nComputer: $env:COMPUTERNAME | User: $env:USERNAME | Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# Find real Python
$pythonExe = $null
$candidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "C:\Python313\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe",
    "C:\Python310\python.exe",
    "C:\ProgramData\LifeLog\python\python.exe"
)
foreach ($c in $candidates) {
    if ($c -and (Test-Path $c) -and $c -notlike "*WindowsApps*") {
        $pythonExe = $c
        break
    } else {
        Write-Host "Skipped: $c"
    }
}

if (-not $pythonExe) {
    $msg = "$sysinfo`n`nERROR: No real Python found (WindowsApps stub doesn't count)`nPlease install Python from https://python.org - make sure to check 'Add to PATH'"
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
import sys, os, json

backup_path = sys.argv[1]
print(f"Backup: {backup_path}")

try:
    from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike
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

# Query manifest using context manager correctly
print("\nQuerying manifest via manifest_db_cursor()...")
try:
    with backup.manifest_db_cursor() as cursor:
        print("Got cursor inside 'with' block")
        domains = cursor.execute("SELECT DISTINCT domain FROM Files ORDER BY domain").fetchall()
        print(f"Found {len(domains)} distinct domains")
        print("\n=== ALL DOMAINS ===")
        for (d,) in domains:
            print(f"  {d}")
        
        # Now find Google Maps files
        print("\n=== GOOGLE MAPS FILES ===")
        maps_files = cursor.execute(
            "SELECT domain, relativePath, flags, file FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR relativePath LIKE '%google%' OR relativePath LIKE '%maps%' OR relativePath LIKE '%Maps%'"
        ).fetchall()
        print(f"Found {len(maps_files)} Google/Maps related files")
        for row in maps_files[:50]:
            print(f"  domain={row[0]} | path={row[1]}")
except Exception as e:
    import traceback
    print(f"manifest_db_cursor() failed: {e}")
    traceback.print_exc()

print("\nDone.")
'@

$tmpFile = [System.IO.Path]::GetTempFileName() + ".py"
$script | Out-File -FilePath $tmpFile -Encoding utf8

$output = "$sysinfo`n`nPython: $pythonExe`n"

if (-not $backupDir) {
    $output += "ERROR: No backup directory found"
} else {
    # Install dependency
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
