$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

# Find Python
$pythonExe = $null
try {
    $wherePython = where.exe python 2>$null
    if ($wherePython) {
        $candidates = $wherePython -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
        foreach ($candidate in $candidates) {
            if ($candidate -notlike '*WindowsApps*') {
                $pythonExe = $candidate
                break
            }
        }
        if (-not $pythonExe) { $pythonExe = $candidates[0] }
    }
} catch {}
if (-not $pythonExe) {
    foreach ($p in @(
        "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python310\python.exe"
    )) {
        if (Test-Path $p) { $pythonExe = $p; break }
    }
}

$script = @'
import os, sys

print(f"v18 | Python: {sys.executable}")
print(f"USERPROFILE: {os.environ.get('USERPROFILE', 'N/A')}")
print()

# Check the Apple Devices backup path directly
apple_backup = os.path.join(os.environ.get('USERPROFILE',''), 'Apple', 'MobileSync', 'Backup')
udid = '00008130-001929983450001C'
backup_path = os.path.join(apple_backup, udid)

print(f"--- Apple Devices Backup: {backup_path} ---")
if os.path.exists(backup_path):
    items = os.listdir(backup_path)
    print(f"  Total items: {len(items)}")
    # Show first 20 items
    for item in sorted(items)[:20]:
        full = os.path.join(backup_path, item)
        size = os.path.getsize(full) if os.path.isfile(full) else '-'
        print(f"  {item}  (size={size})")
    if len(items) > 20:
        print(f"  ... and {len(items)-20} more")
    
    # Check for key files
    for key_file in ['Manifest.db', 'Manifest.mbdb', 'Manifest.plist', 'Info.plist', 'Status.plist']:
        fp = os.path.join(backup_path, key_file)
        exists = os.path.exists(fp)
        size = os.path.getsize(fp) if exists else 0
        print(f"  [{('FOUND' if exists else 'missing')}] {key_file}  size={size}")
else:
    print("  Path does not exist!")

print()
print("--- iMazing backup ---")
imazing_path = os.path.join(os.environ.get('APPDATA',''), 'iMazing', 'Backups', udid)
print(f"Path: {imazing_path}")
if os.path.exists(imazing_path):
    items = os.listdir(imazing_path)
    print(f"  Total items: {len(items)}")
    for item in sorted(items)[:10]:
        full = os.path.join(imazing_path, item)
        size = os.path.getsize(full) if os.path.isfile(full) else '-'
        print(f"  {item}  (size={size})")
else:
    print("  Does not exist")

print()
print("Done.")
'@

if (-not $pythonExe) {
    $output = "v18 | ERROR: Python not found"
} else {
    $output = "v18 | Python: $pythonExe`n" + ($script | & $pythonExe - 2>&1 | Out-String)
    # Remove the duplicate version line from python output
    $outputLines = $output -split "`n"
    $output = ($outputLines | Where-Object { $_ -notmatch '^v18 \| Python:.*pythoncore' } | Select-Object -First 1) + "`n" + ($outputLines | Select-Object -Skip 1 | Out-String)
    $output = "v18 | Python: $pythonExe`n" + ($script | & $pythonExe - 2>&1 | Out-String)
}

$body = @{ output = $output; timestamp = $timestamp; computer = $computer; source = 'LifeLog-DevLoop' } | ConvertTo-Json
Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json' | Out-Null
Write-Host "[$timestamp] Sent output from $computer"
