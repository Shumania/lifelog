# Force update to v1.44 with SSE debug logging
$installDir = 'C:\ProgramData\LifeLog'
$serviceFile = Join-Path $installDir 'lifelog_service.py'

# Read GitHub token from config
$configPath = Join-Path $installDir 'lifelog_config.json'
$token = ''
if (Test-Path $configPath) {
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    $token = $cfg.github_token
}

$headers = @{ 'Accept' = 'application/vnd.github.v3+json'; 'User-Agent' = 'LifeLog' }
if ($token) { $headers['Authorization'] = "token $token" }

# Download lifelog_service.py from GitHub API
try {
    $resp = Invoke-RestMethod -Uri 'https://api.github.com/repos/Shumania/lifelog/contents/lifelog_service.py' -Headers $headers
    $content = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($resp.content))
    $version = if ($content -match 'SERVICE_VERSION\s*=\s*"([^"]+)"') { $Matches[1] } else { 'unknown' }
    Write-Output "Downloaded service v$version from GitHub API"
    
    # Stop existing service
    $procs = Get-Process python*, python3* -ErrorAction SilentlyContinue | Where-Object {
        try { $_.CommandLine -match 'lifelog_service' } catch { $false }
    }
    if ($procs) {
        Write-Output "Stopping $($procs.Count) existing service process(es)..."
        $procs | Stop-Process -Force
        Start-Sleep -Seconds 3
    }
    
    # Write new file
    [System.IO.File]::WriteAllText($serviceFile, $content, [System.Text.Encoding]::UTF8)
    Write-Output "Wrote v$version to $serviceFile"
    
    # Start new service
    Write-Output "Starting lifelog_service.py v$version..."
    $p = Start-Process -FilePath 'python' -ArgumentList $serviceFile -WorkingDirectory $installDir -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 10
    if ($p.HasExited) {
        Write-Output "WARNING: Service exited with code $($p.ExitCode)"
    } else {
        Write-Output "Service v$version running (PID $($p.Id))"
    }
} catch {
    Write-Output "ERROR: $($_.Exception.Message)"
}
