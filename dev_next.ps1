$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

# Capture everything
$output = @()
$output += "=== DEV_NEXT START on $computer at $timestamp ==="

# Check Python
try {
    $pyVersion = & python --version 2>&1
    $output += "Python: $pyVersion"
    $pyPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    $output += "Python path: $pyPath"
} catch {
    $output += "Python: NOT FOUND - $_"
}

# Check pip packages
try {
    $pipList = & pip list 2>&1 | Select-String -Pattern 'iphone|backup|decrypt|pycrypto'
    if ($pipList) {
        $output += "Relevant pip packages: $pipList"
    } else {
        $output += "No iphone/backup/decrypt packages found in pip list"
    }
} catch {
    $output += "pip check failed: $_"
}

# Find backup
try {
    $backupRoots = @(
        "$env:USERPROFILE\Apple\MobileSync\Backup",
        "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup",
        "$env:LOCALAPPDATA\Apple\MobileSync\Backup"
    )
    $backupDir = $null
    foreach ($root in $backupRoots) {
        if (Test-Path $root) {
            $dirs = Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue
            if ($dirs) {
                $backupDir = ($dirs | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
                $output += "Backup found at: $backupDir"
                break
            }
        }
    }
    if (-not $backupDir) {
        $output += "No backup directory found in any standard location"
    }
} catch {
    $output += "Backup search failed: $_"
}

# Download and run inspection script
try {
    $scriptUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/inspect_googlemaps_backup.py?t=$(Get-Date -UFormat %s)"
    $tmpScript = Join-Path $env:TEMP "inspect_googlemaps_backup.py"
    Invoke-WebRequest -Uri $scriptUrl -OutFile $tmpScript -UseBasicParsing
    $output += "Script downloaded to $tmpScript"
    $scriptOutput = & python $tmpScript 2>&1
    $output += "--- Script output ---"
    $output += $scriptOutput
} catch {
    $output += "Script execution failed: $_"
}

$output += "=== DEV_NEXT END ==="
$fullOutput = $output -join "`n"

# Always post
$body = @{ computer = $computer; timestamp = $timestamp; source = 'LifeLog-DevLoop'; output = $fullOutput } | ConvertTo-Json -Depth 3
Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json' | Out-Null
Write-Host $fullOutput
