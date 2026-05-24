# Force update to v1.38 — dev_next escape hatch
$ErrorActionPreference = 'Continue'
$installDir = 'C:\ProgramData\LifeLog'
$serviceFile = Join-Path $installDir 'lifelog_service.py'
$versionsUrl = 'https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_service.py'

Write-Output "=== Force Update to v1.38 ==="
Write-Output "Current service file: $serviceFile"

# Check current version
if (Test-Path $serviceFile) {
    $currentVer = (Select-String -Path $serviceFile -Pattern 'SERVICE_VERSION\s*=\s*"([^"]+)"' | ForEach-Object { $_.Matches[0].Groups[1].Value })
    Write-Output "Current version: $currentVer"
} else {
    Write-Output "Service file not found!"
}

# Download latest from GitHub (raw URL, bypassing API rate limits)
try {
    Write-Output "Downloading lifelog_service.py from GitHub..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $wc = New-Object System.Net.WebClient
    $wc.Headers.Add('Cache-Control', 'no-cache')
    $tmpFile = Join-Path $installDir 'lifelog_service.py.tmp'
    $wc.DownloadFile($versionsUrl, $tmpFile)
    
    # Verify download
    $newVer = (Select-String -Path $tmpFile -Pattern 'SERVICE_VERSION\s*=\s*"([^"]+)"' | ForEach-Object { $_.Matches[0].Groups[1].Value })
    $size = (Get-Item $tmpFile).Length
    Write-Output "Downloaded version: $newVer (size: $size bytes)"
    
    if ($size -lt 10000) {
        Write-Output "ERROR: Downloaded file too small ($size bytes) - aborting"
        Remove-Item $tmpFile -Force
        exit 1
    }
    
    if ($newVer -ne '1.38') {
        Write-Output "WARNING: Expected v1.38 but got v$newVer"
    }
    
    # Replace service file
    if (Test-Path $serviceFile) {
        Copy-Item $serviceFile "$serviceFile.bak" -Force
        Write-Output "Backed up current to .bak"
    }
    Move-Item $tmpFile $serviceFile -Force
    Write-Output "Replaced service file with v$newVer"
    
    # Kill running service so Start-LifeLog.ps1 restarts it
    $procs = Get-Process python* -ErrorAction SilentlyContinue | Where-Object {
        try { $_.CommandLine -like '*lifelog_service*' } catch { $false }
    }
    if ($procs) {
        Write-Output "Killing running service processes..."
        $procs | Stop-Process -Force
        Write-Output "Service stopped. It should restart via Start-LifeLog.ps1"
    } else {
        Write-Output "No running service process found to kill"
        # Try broader match
        Get-Process python* -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Output "  Found python process: PID=$($_.Id) Name=$($_.ProcessName)"
        }
    }
    
    Write-Output "=== Done. Service should restart with v$newVer ==="
} catch {
    Write-Output "ERROR: $($_.Exception.Message)"
    exit 1
}
