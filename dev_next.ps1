# Self-contained: installs Python if needed, downloads and runs inspect script
$ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

# Find Python
$py = $null
foreach ($cmd in @("py", "python", "python3")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python") { $py = $cmd; break }
    } catch {}
}

if (-not $py) {
    Write-Output "Python not found. Installing via winget..."
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements 2>&1
    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
    foreach ($cmd in @("py", "python", "python3")) {
        try {
            $ver = & $cmd --version 2>&1
            if ($ver -match "Python") { $py = $cmd; break }
        } catch {}
    }
}

if (-not $py) {
    Write-Output "ERROR: Could not find or install Python. Please install from https://python.org"
    exit 1
}

Write-Output "Using Python: $py"
$tmpScript = Join-Path $env:TEMP "inspect_googlemaps_backup.py"
$scriptUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/inspect_googlemaps_backup.py?t=$ts"

Write-Output "Downloading inspect script..."
Invoke-WebRequest -Uri $scriptUrl -OutFile $tmpScript -UseBasicParsing
Write-Output "Running inspect script..."
& $py $tmpScript 2>&1
