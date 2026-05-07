# dev_next.ps1 v26 - Direct Python setup + extraction
$WEBHOOK = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$machine = $env:COMPUTERNAME
$log = @()
$log += "=== dev_next.ps1 v26 on $machine ==="
$log += "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# Ensure install dir
$dir = "C:\ProgramData\LifeLog"
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

# Find real Python (skip Windows Store stub)
function Find-Python {
    # Try py launcher first
    try {
        $ver = & py --version 2>&1
        if ("$ver" -match "Python 3\.") { return "py" }
    } catch {}
    
    # Try python but skip Store stub
    try {
        $p = (Get-Command python -ErrorAction Stop).Source
        if ($p -notlike "*WindowsApps*") {
            $ver = & $p --version 2>&1
            if ("$ver" -match "Python 3\.") { return $p }
        }
    } catch {}
    
    # Common real install paths
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

$pythonExe = Find-Python
$log += "Python found: $pythonExe"

if (-not $pythonExe) {
    $log += "No real Python found — trying winget install..."
    $wg = winget install Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-String
    $log += "Winget result: $wg"
    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
    $pythonExe = Find-Python
    $log += "Python after install: $pythonExe"
}

if ($pythonExe) {
    # Install package
    $pip = & $pythonExe -m pip install --quiet --upgrade iphone_backup_decrypt 2>&1 | Out-String
    $log += "pip install result: $pip"
    
    # Download latest extract script
    $scriptPath = "$dir\lifelog_extract.py"
    $ts = Get-Date -Format 'yyyyMMddHHmmss'
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?v=$ts" -OutFile $scriptPath -UseBasicParsing
    $lineCount = (Get-Content $scriptPath).Count
    $log += "Downloaded lifelog_extract.py ($lineCount lines)"
    
    # Run extraction
    $log += "--- Running extraction ---"
    $extract = & $pythonExe $scriptPath 2>&1 | Out-String
    $log += $extract
} else {
    $log += "ERROR: Could not find or install Python. Aborting."
}

$output = $log -join "`n"
$body = @{ computer = $machine; output = $output; timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') } | ConvertTo-Json -Compress
Invoke-WebRequest -Uri $WEBHOOK -Method POST -Body $body -ContentType "application/json" -UseBasicParsing | Out-Null
