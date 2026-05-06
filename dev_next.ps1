$webhook = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

try {
    # Find backup
    $backupRoots = @(
        "$env:USERPROFILE\Apple\MobileSync\Backup",
        "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup"
    )
    $backupPath = $null
    foreach ($root in $backupRoots) {
        if (Test-Path $root) {
            $latest = Get-ChildItem $root -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($latest) { $backupPath = $latest.FullName; break }
        }
    }
    if (-not $backupPath) { throw "No backup found" }

    # Download and run Python script
    $scriptUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/inspect_googlemaps_files.py?t=$(Get-Date -Format 'yyyyMMddHHmmss')"
    $scriptPath = "$env:TEMP\inspect_maps_files.py"
    Invoke-WebRequest -Uri $scriptUrl -OutFile $scriptPath -UseBasicParsing

    $output = & python $scriptPath --backup $backupPath 2>&1 | Out-String

    $body = @{ computer=$computer; timestamp=$timestamp; source="LifeLog-DevLoop"; log=$output; exitCode=0 } | ConvertTo-Json
    Invoke-RestMethod -Uri $webhook -Method POST -Body $body -ContentType "application/json"
} catch {
    $body = @{ computer=$computer; timestamp=$timestamp; source="LifeLog-DevLoop"; log="ERROR: $_"; exitCode=1 } | ConvertTo-Json
    Invoke-RestMethod -Uri $webhook -Method POST -Body $body -ContentType "application/json"
}
