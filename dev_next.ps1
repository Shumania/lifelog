$WEBHOOK = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format "yyyy-MM-dd HH:MM:ss"

function Send-Output($text) {
    $body = @{ log = $text; exitCode = 0 } | ConvertTo-Json
    Invoke-RestMethod -Uri $WEBHOOK -Method Post -Body $body -ContentType "application/json" | Out-Null
}

try {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $python = $pythonCmd.Source
    } else {
        $python = $null
    }

    if (-not $python) {
        Send-Output "FROM: $computer at $timestamp`n`nPython not found. Please install from https://www.python.org/downloads/ and check 'Add to PATH'."
        exit 0
    }

    $pyVersion = & $python --version 2>&1
    $scriptUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/inspect_googlemaps_backup.py?t=$(Get-Date -UFormat %s)"
    $tmpScript = "$env:TEMP\inspect_googlemaps_backup.py"
    Invoke-WebRequest -Uri $scriptUrl -OutFile $tmpScript -UseBasicParsing

    # Install dependencies quietly
    & $python -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null

    $output = & $python $tmpScript 2>&1 | Out-String
    Send-Output "FROM: $computer at $timestamp`nPython: $pyVersion`n`n$output"
} catch {
    Send-Output "FROM: $computer at $timestamp`n`nERROR: $_"
}
