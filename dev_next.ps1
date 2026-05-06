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
import sys, os, tempfile, inspect
sys.path.insert(0, r'C:\ProgramData\LifeLog')
from iphone_backup_decrypt import EncryptedBackup, RelativePath

backup_path = sys.argv[1]
password = '#ngrierBill70'

print(f'Backup: {backup_path}')

# Print extract_file signature
print('\nextract_file signature:', inspect.signature(EncryptedBackup.extract_file))
print('extract_file docstring:', EncryptedBackup.extract_file.__doc__)

# Check for other useful methods
methods = [m for m in dir(EncryptedBackup) if not m.startswith('__')]
print('\nAll methods:', methods)

# Also check RelativePath constants for Maps
maps_paths = [attr for attr in dir(RelativePath) if 'MAP' in attr.upper() or 'GOOGLE' in attr.upper()]
print('\nRelativePath Maps constants:', maps_paths)

# Print ALL RelativePath constants to see the format
print('\nAll RelativePath constants:')
for attr in dir(RelativePath):
    if not attr.startswith('_'):
        print(f'  {attr} = {getattr(RelativePath, attr)}')

print('\nUnlocking backup...')
backup = EncryptedBackup(backup_directory=backup_path, passphrase=password)

# Force unlock with podcasts
tmp = tempfile.mkdtemp()
try:
    backup.extract_file(
        relative_path=RelativePath.PODCASTS,
        output_filename=os.path.join(tmp, 'podcasts.sqlite')
    )
    print('Unlocked OK via RelativePath.PODCASTS')
except Exception as e:
    print(f'Podcasts extract error: {e}')

# Now try Google Maps files - try different path formats
targets = [
    'Library/Application Support/tlogs_offline_storage.binaryproto',
    'Library/Application Support/DirectionsData',
    'Library/Application Support/FrequentTripsData',
    'Library/Application Support/PlacesheetVisits',
]

for rel in targets:
    out = os.path.join(tmp, os.path.basename(rel))
    # Try 1: plain relative path
    try:
        backup.extract_file(relative_path=rel, output_filename=out)
        size = os.path.getsize(out)
        print(f'\nExtracted (plain path) {rel}: {size:,} bytes')
        if rel.endswith('.binaryproto') and size > 0:
            with open(out, 'rb') as f:
                data = f.read(500)
            print('Hex:', data.hex())
            print('Chars:', ''.join(chr(b) if 32<=b<127 else '.' for b in data))
        continue
    except Exception as e1:
        print(f'Plain path failed for {os.path.basename(rel)}: {e1}')

print('\nDone.')
'@

    $pyPath = Join-Path $env:TEMP 'extract_maps.py'
    $pyScript | Set-Content $pyPath -Encoding UTF8

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
