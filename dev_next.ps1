$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

try {
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
    if (-not $backupDir) { throw "No backup directory found!" }

    $pyScript = @'
import sys, os, tempfile, subprocess, json
sys.path.insert(0, r'C:\ProgramData\LifeLog')
from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike

backup_path = sys.argv[1]
password = '#ngrierBill70'

print(f'Backup: {backup_path}')
print('Unlocking...')
backup = EncryptedBackup(backup_directory=backup_path, passphrase=password)

# Force unlock by extracting podcasts DB
tmp = tempfile.mkdtemp()
try:
    backup.extract_file(
        relative_path='Library/Preferences/com.google.Maps.plist',
        domain='AppDomain-com.google.Maps',
        output_filename=os.path.join(tmp, 'maps.plist')
    )
    print('Unlock confirmed.')
except Exception as e:
    print(f'Unlock note: {e}')

# Files to extract and inspect
targets = [
    ('AppDomain-com.google.Maps', 'Library/Application Support/tlogs_offline_storage.binaryproto', 'tlogs.binaryproto'),
    ('AppDomain-com.google.Maps', 'Library/Application Support/DirectionsData', 'DirectionsData'),
    ('AppDomain-com.google.Maps', 'Library/Application Support/FrequentTripsData', 'FrequentTripsData'),
    ('AppDomain-com.google.Maps', 'Library/Application Support/PlacesheetVisits', 'PlacesheetVisits'),
]

for domain, rel_path, out_name in targets:
    out_path = os.path.join(tmp, out_name)
    try:
        backup.extract_file(relative_path=rel_path, domain=domain, output_filename=out_path)
        size = os.path.getsize(out_path)
        print(f'\nExtracted {out_name}: {size:,} bytes')
        # For binaryproto: hex dump first 200 bytes
        if out_name.endswith('.binaryproto') and size > 0:
            with open(out_path, 'rb') as f:
                data = f.read(500)
            print('First 500 bytes (hex):')
            print(data.hex())
            print('Printable chars:', ''.join(chr(b) if 32<=b<127 else '.' for b in data))
        elif size > 0 and size < 50000:
            # Try to read as text/plist
            with open(out_path, 'rb') as f:
                raw = f.read(2000)
            print('Preview:', ''.join(chr(b) if 32<=b<127 else '.' for b in raw))
    except Exception as e:
        print(f'Error extracting {out_name}: {e}')

print('\nDone.')
'@

    $pyPath = Join-Path $env:TEMP 'extract_maps.py'
    $pyScript | Set-Content $pyPath -Encoding UTF8

    & python -m pip install iphone-backup-decrypt -q 2>&1 | Out-Null
    $output = & python $pyPath $backupDir 2>&1 | Out-String

    $body = @{
        computer  = $computer
        timestamp = $timestamp
        source    = 'LifeLog-DevLoop'
        output    = $output
    } | ConvertTo-Json -Compress

    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json'
    Write-Host "Sent!"

} catch {
    $errBody = @{
        computer  = $computer
        timestamp = $timestamp
        source    = 'LifeLog-DevLoop'
        output    = "ERROR: $_"
    } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $errBody -ContentType 'application/json'
    Write-Host "Error: $_"
}
