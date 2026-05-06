$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

try {
    # Find backup directory
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

    if (-not $backupDir) {
        throw "No backup directory found!"
    }

    Write-Host "Found backup: $backupDir"

    # Download inspect script
    $scriptPath = Join-Path $env:TEMP "inspect_googlemaps_files.py"
    $url = "https://raw.githubusercontent.com/Shumania/lifelog/main/inspect_googlemaps_files.py?ts=$(Get-Date -Format 'yyyyMMddHHmmss')"
    Invoke-WebRequest -Uri $url -OutFile $scriptPath -UseBasicParsing

    # Install dependency
    & python -m pip install iphone-backup-decrypt -q 2>&1 | Out-Null

    # Run inspection
    $output = & python $scriptPath --backup $backupDir 2>&1 | Out-String

    $body = @{
        computer  = $computer
        timestamp = $timestamp
        source    = 'LifeLog-DevLoop'
        output    = $output
    } | ConvertTo-Json -Compress

    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json'
    Write-Host "Sent!"

} catch {
    $body = @{
        computer  = $computer
        timestamp = $timestamp
        source    = 'LifeLog-DevLoop'
        output    = "ERROR: $_"
    } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json'
    Write-Host "Error sent: $_"
}
