# dev_next.ps1 - agent-controlled, auto-updated
# Step 1: Ensure Python is installed

$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

function Send-Output($text) {
    $body = @{ output = $text; timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); computer = $env:COMPUTERNAME; source = 'LifeLog-DevLoop' } | ConvertTo-Json
    try { Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json' | Out-Null } catch {}
}

$log = "=== Dev Loop - Python Install Check ===`nTime: $(Get-Date)`nMachine: $env:COMPUTERNAME`n`n"

# Disable the Microsoft Store Python alias
try {
    $aliasPath = "$env:LOCALAPPDATA\Microsoft\WindowsApps"
    $stubs = @("python.exe", "python3.exe")
    foreach ($stub in $stubs) {
        $full = Join-Path $aliasPath $stub
        if (Test-Path $full) {
            # Check if it's a stub (tiny file <10KB)
            $size = (Get-Item $full).Length
            if ($size -lt 10240) {
                Rename-Item $full "$full.disabled" -Force -ErrorAction SilentlyContinue
                $log += "Disabled Store alias: $full`n"
            }
        }
    }
} catch {
    $log += "Could not disable Store aliases: $_`n"
}

# Check if Python is actually installed
$pythonExe = $null
$candidates = @(
    "C:\Python312\python.exe",
    "C:\Python311\python.exe",
    "C:\Python310\python.exe",
    "C:\Program Files\Python312\python.exe",
    "C:\Program Files\Python311\python.exe",
    "C:\ProgramData\LifeLog\python\python.exe",
    (& { try { (Get-Command python -ErrorAction Stop).Source } catch { $null } })
)
foreach ($c in $candidates) {
    if ($c -and (Test-Path $c -ErrorAction SilentlyContinue)) {
        # Verify it actually works
        $ver = & $c --version 2>&1
        if ($ver -match 'Python 3') {
            $pythonExe = $c
            $log += "Python found at: $c ($ver)`n"
            break
        }
    }
}

if (-not $pythonExe) {
    $log += "Python not found. Attempting winget install...`n"
    try {
        $result = & winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements 2>&1
        $log += "winget result: $result`n"
        # Refresh PATH
        $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH','User')
        Start-Sleep -Seconds 5
        # Try again
        $pythonExe = & { try { (Get-Command python -ErrorAction Stop).Source } catch { $null } }
        if ($pythonExe) {
            $log += "Python now available at: $pythonExe`n"
        } else {
            $log += "Python still not found after winget. Trying direct path...`n"
            $newPaths = @(
                "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                "C:\Program Files\Python312\python.exe",
                "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python312\python.exe"
            )
            foreach ($p in $newPaths) {
                if (Test-Path $p) { $pythonExe = $p; $log += "Found at: $p`n"; break }
            }
        }
    } catch {
        $log += "winget failed: $_`n"
    }
}

if (-not $pythonExe) {
    $log += "PYTHON NOT AVAILABLE - cannot run inspection script.`nPlease run: winget install Python.Python.3.12`nOr run Install-LifeLog.ps1 first.`n"
    Send-Output $log
    exit 1
}

# Step 2: Download and run inspect script
$log += "`nRunning Google Maps inspection...`n"
$tmpScript = "$env:TEMP\inspect_googlemaps_backup.py"
$ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
try {
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/inspect_googlemaps_backup.py?t=$ts" -OutFile $tmpScript -UseBasicParsing
    $log += "Script downloaded OK`n"
} catch {
    $log += "Download failed: $_`n"
    Send-Output $log
    exit 1
}

# Install dependencies
try {
    & $pythonExe -m pip install iphone-backup-decrypt --quiet 2>&1 | Out-Null
} catch {}

$scriptOutput = & $pythonExe $tmpScript 2>&1 | Out-String
$log += "`n=== Script Output ===`n$scriptOutput"

Send-Output $log
