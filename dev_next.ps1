$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

$pythonExe = $null
foreach ($cmd in @('python','python3')) {
    $c = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($c) { $pythonExe = $c.Source; break }
}
if (-not $pythonExe) {
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe","$env:LOCALAPPDATA\Programs\Python\Python312\python.exe","C:\ProgramData\LifeLog\python\python.exe")) {
        if (Test-Path $p) { $pythonExe = $p; break }
    }
}

$backupRoot = "$env:USERPROFILE\Apple\MobileSync\Backup"
if (-not (Test-Path $backupRoot)) { $backupRoot = "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup" }
$backupDir = Get-ChildItem $backupRoot -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1

$output = "Python: $pythonExe`nBackup: $($backupDir.FullName)`n"

$pyScript = @"
import sys, os, traceback, sqlite3, tempfile, inspect
sys.path.insert(0, r'C:\ProgramData\LifeLog')

try:
    import iphone_backup_decrypt
    print('Library version:', getattr(iphone_backup_decrypt, '__version__', 'unknown'))
    print('Library file:', iphone_backup_decrypt.__file__)
except ImportError:
    import subprocess
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'iphone-backup-decrypt', '--quiet'])
    import iphone_backup_decrypt
    print('Library installed fresh')

backup_path = sys.argv[1]
password = sys.argv[2]

try:
    backup = iphone_backup_decrypt.EncryptedBackup(backup_directory=backup_path, passphrase=password)
    print('Backup object created OK')
except Exception as e:
    print('Backup creation failed:', e)
    traceback.print_exc()
    sys.exit(1)

print('\n--- Backup object methods/attributes ---')
for name in sorted(dir(backup)):
    print(' ', name)

print('\n--- Attempting podcasts extract ---')
tmp = tempfile.mkdtemp()
podcasts_out = os.path.join(tmp, 'podcasts.sqlite')
try:
    sig = inspect.signature(backup.extract_file)
    print('extract_file signature:', sig)
except:
    pass

try:
    backup.extract_file(
        relative_path='Library/Application Support/com.apple.podcasts/Documents/MTLibrary.sqlite',
        output_filename=podcasts_out
    )
    print('Podcasts extracted OK to:', podcasts_out)
except Exception as e:
    print('extract_file failed:', repr(e))
    traceback.print_exc()

# Try alternate domain-based extraction
try:
    sig2 = inspect.signature(backup.extract_file)
    params = list(sig2.parameters.keys())
    print('extract_file params:', params)
    if 'domain' in params:
        backup.extract_file(
            domain='AppDomainGroup-243LU875E5.groups.com.apple.podcasts',
            relative_path='Library/Application Support/com.apple.podcasts/Documents/MTLibrary.sqlite',
            output_filename=podcasts_out
        )
        print('Domain-based extraction OK')
except Exception as e:
    print('Domain extraction failed:', repr(e))
    traceback.print_exc()

# Look for decrypted manifest in temp dirs
print('\n--- Searching temp dirs for decrypted manifest ---')
for base in [tempfile.gettempdir(), tmp]:
    for root, dirs, files in os.walk(base):
        for f in files:
            if 'manifest' in f.lower() or f.endswith('.db') or f.endswith('.sqlite'):
                full = os.path.join(root, f)
                size = os.path.getsize(full)
                print(f'  Found: {full} ({size} bytes)')
                if size > 1000:
                    try:
                        conn = sqlite3.connect(full)
                        cur = conn.cursor()
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        tables = [r[0] for r in cur.fetchall()]
                        print(f'    Tables: {tables}')
                        conn.close()
                    except Exception as e:
                        print(f'    Not readable: {e}')
"@

$output += ($pyScript | & $pythonExe -u - $backupDir.FullName '#ngrierBill70' 2>&1 | Out-String)

$body = @{ output = $output; timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); computer = $env:COMPUTERNAME; source = 'LifeLog-DevLoop' } | ConvertTo-Json
Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json' | Out-Null
Write-Host "Sent! Output length: $($output.Length)"
