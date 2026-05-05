# dev_next.ps1 - controlled by Tasklet agent
# Current task: Inspect Google Maps data in iPhone backup

$output = @()
$output += "=== Dev Loop Ping ==="
$output += "Time: $(Get-Date)"
$output += "Machine: $env:COMPUTERNAME"
$output += ""

# Check for Python
$pythonCmd = $null
foreach ($cmd in @('python', 'python3', 'py')) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match 'Python') {
            $pythonCmd = $cmd
            $output += "Python found: $ver (command: $cmd)"
            break
        }
    } catch {}
}

if (-not $pythonCmd) {
    $output += "Python NOT found. Attempting install via winget..."
    try {
        $result = winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements 2>&1
        $output += $result
        # Re-check
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
        $ver = & python --version 2>&1
        if ($ver -match 'Python') {
            $pythonCmd = 'python'
            $output += "Python installed successfully: $ver"
        } else {
            $output += "Python install may need a restart. Please run: winget install Python.Python.3.12"
        }
    } catch {
        $output += "winget install failed: $_"
        $output += "Please run the LifeLog installer first: irm https://raw.githubusercontent.com/Shumania/lifelog/main/Install-LifeLog.ps1 | iex"
    }
}

if ($pythonCmd) {
    # Download and run inspection script
    $scriptUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/inspect_googlemaps_backup.py?t=$(Get-Date -Format 'yyyyMMddHHmm')"
    $tmpScript = Join-Path $env:TEMP 'inspect_googlemaps_backup.py'
    try {
        Invoke-WebRequest -Uri $scriptUrl -OutFile $tmpScript -UseBasicParsing
        $output += "Script downloaded OK"
        
        # Install dependencies quietly
        & $pythonCmd -m pip install iphone_backup_decrypt --quiet 2>&1 | Out-Null
        
        # Run script
        $scriptOut = & $pythonCmd $tmpScript 2>&1
        $output += ""
        $output += "=== Script Output ==="
        $output += $scriptOut
    } catch {
        $output += "Error running script: $_"
    }
}

$output -join "`n"
