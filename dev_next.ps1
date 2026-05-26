# Diagnose v1.44 exit — capture all output
$installDir = 'C:\ProgramData\LifeLog'
$serviceFile = Join-Path $installDir 'lifelog_service.py'
$logFile = Join-Path $installDir 'startup_debug.log'

# Clean ALL rollback artifacts
$flagFiles = @('update_in_progress', 'update_started', 'lifelog_service.py.bak')
foreach ($f in $flagFiles) {
    $fp = Join-Path $installDir $f
    if (Test-Path $fp) {
        Remove-Item $fp -Force
        Write-Output "Cleaned: $f"
    }
}

# Stop existing service
$procs = Get-Process python*, python3* -ErrorAction SilentlyContinue | Where-Object {
    try { $_.CommandLine -match 'lifelog_service' } catch { $false }
}
if ($procs) {
    Write-Output "Stopping $($procs.Count) process(es)..."
    $procs | Stop-Process -Force
    Start-Sleep -Seconds 3
}

# Download v1.44 fresh
$configPath = Join-Path $installDir 'lifelog_config.json'
$token = ''
if (Test-Path $configPath) {
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    $token = $cfg.github_token
}
$headers = @{ 'Accept' = 'application/vnd.github.v3+json'; 'User-Agent' = 'LifeLog' }
if ($token) { $headers['Authorization'] = "token $token" }

try {
    $resp = Invoke-RestMethod -Uri 'https://api.github.com/repos/Shumania/lifelog/contents/lifelog_service.py' -Headers $headers
    $content = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($resp.content))
    [System.IO.File]::WriteAllText($serviceFile, $content, [System.Text.Encoding]::UTF8)
    
    # Verify version
    if ($content -match 'SERVICE_VERSION\s*=\s*"([^"]+)"') {
        Write-Output "File version: v$($Matches[1])"
    }

    # Verify NO flags exist
    foreach ($f in $flagFiles) {
        $fp = Join-Path $installDir $f
        if (Test-Path $fp) { Write-Output "WARNING: $f still exists!" }
    }

    # Start with output redirected to log file
    Write-Output "Starting with output capture..."
    $p = Start-Process -FilePath 'python' -ArgumentList $serviceFile `
        -WorkingDirectory $installDir `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError (Join-Path $installDir 'startup_error.log') `
        -PassThru -WindowStyle Hidden -NoNewWindow:$false
    
    Start-Sleep -Seconds 15
    
    if ($p.HasExited) {
        Write-Output "EXITED code $($p.ExitCode)"
        Write-Output "=== STDOUT (last 30 lines) ==="
        if (Test-Path $logFile) {
            Get-Content $logFile -Tail 30 | ForEach-Object { Write-Output $_ }
        }
        Write-Output "=== STDERR ==="
        $errLog = Join-Path $installDir 'startup_error.log'
        if (Test-Path $errLog) {
            Get-Content $errLog -Tail 30 | ForEach-Object { Write-Output $_ }
        }
    } else {
        Write-Output "Running PID=$($p.Id)"
        if (Test-Path $logFile) {
            Write-Output "=== First 20 lines ==="
            Get-Content $logFile -Head 20 | ForEach-Object { Write-Output $_ }
        }
    }
} catch {
    Write-Output "ERROR: $($_.Exception.Message)"
}
