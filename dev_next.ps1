$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

$sysinfo = "=== SYSINFO ===`nComputer: $env:COMPUTERNAME | User: $env:USERNAME | Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# Find Python - prefer non-WindowsApps, but fall back to it if that's all we have
$pythonExe = $null
try {
    $allPython = @(where.exe python 2>$null)
    # First pass: prefer non-WindowsApps
    foreach ($p in $allPython) {
        if ($p -and (Test-Path $p) -and $p -notlike "*WindowsApps*") {
            $pythonExe = $p; break
        }
    }
    # Second pass: fall back to WindowsApps (Store install - works fine)
    if (-not $pythonExe) {
        foreach ($p in $allPython) {
            if ($p -and $p -like "*WindowsApps*") {
                $pythonExe = "python"; break  # Use bare 'python' - Store redirector handles it
            }
        }
    }
} catch {}

# Also try bare 'python' as last resort
if (-not $pythonExe) {
    try {
        $ver = & python --version 2>&1
        if ($ver -match 'Python') { $pythonExe = "python" }
    } catch {}
}

if (-not $pythonExe) {
    $msg = "$sysinfo`n`nERROR: No Python found. Please install from https://python.org"
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
except Exception as e:
    import traceback
    print(f"ERROR during unlock: {e}")
    traceback.print_exc()
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
    $output += "Backup dir: $backupDir`n"
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
