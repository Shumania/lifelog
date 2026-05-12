# v57 - DIAGNOSTIC: show cursor value, backup date range, top 10 recent episodes
$ErrorActionPreference = "Stop"
try {
    $cursorFile = "C:\ProgramData\LifeLog\last_podcast_cursor.txt"
    $cursorVal = if (Test-Path $cursorFile) { Get-Content $cursorFile -Raw } else { "NOT FOUND" }
    Write-Host "=== CURSOR FILE ===" 
    Write-Host "Value: $($cursorVal.Trim())"
    
    # Convert cursor to date if numeric
    if ($cursorVal -match '^\d+') {
        $appleEpoch = [long]$cursorVal.Trim()
        $unixEpoch = $appleEpoch + 978307200
        $dt = [DateTimeOffset]::FromUnixTimeSeconds($unixEpoch).ToLocalTime()
        Write-Host "Cursor date: $dt"
    }

    # Find the backup
    $backupRoots = @(
        "$env:LOCALAPPDATA\Apple\MobileSync\Backup",
        "$env:USERPROFILE\Apple\MobileSync\Backup",
        "C:\Users\andre\Apple\MobileSync\Backup",
        "C:\Users\Shumadmin\Apple\MobileSync\Backup"
    )
    $backupDir = $null
    foreach ($root in $backupRoots) {
        if (Test-Path $root) {
            $dirs = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue
            foreach ($d in $dirs) { $backupDir = $d.FullName; break }
        }
        if ($backupDir) { break }
    }
    Write-Host "`n=== BACKUP DIR ===" 
    Write-Host $backupDir

    # Run Python diagnostic directly on backup using lifelog_extract logic
    $pyScript = @"
import os, sys, sqlite3, hashlib, struct

backup_dir = r'$backupDir'
password = '#ngrierBill70'

# Find manifest
manifest_db = os.path.join(backup_dir, 'Manifest.db')
if not os.path.exists(manifest_db):
    print('ERROR: Manifest.db not found')
    sys.exit(1)

# Find podcast DB hash
conn = sqlite3.connect(manifest_db)
cur = conn.cursor()
cur.execute("""
    SELECT fileID FROM Files 
    WHERE domain='AppDomainGroup-243LU875E5.groups.com.apple.podcasts'
    AND relativePath='Library/Application Support/com.apple.podcasts/MTLibrary.sqlite'
""")
row = cur.fetchone()
conn.close()
if not row:
    print('ERROR: Podcast DB not found in manifest')
    sys.exit(1)
file_id = row[0]
print(f'Podcast DB file_id: {file_id}')
file_path = os.path.join(backup_dir, file_id[:2], file_id)
print(f'File path exists: {os.path.exists(file_path)}')

# Try to open directly (might work if unencrypted)
try:
    pconn = sqlite3.connect(file_path)
    pcur = pconn.cursor()
    pcur.execute("SELECT COUNT(*), MIN(ZLASTDATEPLAYED), MAX(ZLASTDATEPLAYED) FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL")
    row = pcur.fetchone()
    print(f'Direct open - Count: {row[0]}, Min Apple epoch: {row[1]}, Max Apple epoch: {row[2]}')
    if row[1]:
        min_dt = row[1] + 978307200
        max_dt = row[2] + 978307200
        import datetime
        print(f'Date range: {datetime.datetime.utcfromtimestamp(min_dt)} to {datetime.datetime.utcfromtimestamp(max_dt)}')
    # Show top 10 most recent
    pcur.execute("""
        SELECT e.ZTITLE, p.ZTITLE, e.ZLASTDATEPLAYED 
        FROM ZMTEPISODE e LEFT JOIN ZMTPODCAST p ON e.ZPODCAST=p.Z_PK
        WHERE e.ZLASTDATEPLAYED IS NOT NULL 
        ORDER BY e.ZLASTDATEPLAYED DESC LIMIT 10
    """)
    print('Top 10 most recent episodes:')
    for r in pcur.fetchall():
        import datetime
        dt = datetime.datetime.utcfromtimestamp(r[2] + 978307200)
        print(f'  {dt} | {r[1]} | {r[0]}')
    pconn.close()
except Exception as e:
    print(f'Direct open failed (probably encrypted): {e}')
    print('Need to decrypt - run full lifelog_extract.py instead')
"@

    $pyScript | python - 2>&1 | Write-Host

} catch {
    Write-Host "ERROR: $_"
    throw $_
}
