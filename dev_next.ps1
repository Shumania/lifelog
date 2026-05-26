# Force update to v1.44 — clean rollback flags first
$installDir = 'C:\ProgramData\LifeLog'
$serviceFile = Join-Path $installDir 'lifelog_service.py'

# STEP 1: Clean up ALL rollback artifacts
$flagFiles = @('update_in_progress', 'update_started', 'lifelog_service.py.bak')
foreach ($f in $flagFiles) {
    $fp = Join-Path $installDir $f
    if (Test-Path $fp) {
        Remove-Item $fp -Force
        Write-Output "Cleaned: $f"
    }
}

# STEP 2: Stop existing service
$procs = Get-Process python*, python3* -ErrorAction SilentlyContinue | Where-Object {
    try { $_.CommandLine -match 'lifelog_service' } catch { $false }
}
if ($procs) {
    Write-Output "Stopping $($procs.Count) process(es)..."
    $procs | Stop-Process -Force
    Start-Sleep -Seconds 3
} else {
    Write-Output "No running service found"
}

# STEP 3: Download v1.44 from GitHub API
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
    $version = if ($content -match 'SERVICE_VERSION\s*=\s*"([^"]+)"') { $Matches[1] } else { 'unknown' }
    [System.IO.File]::WriteAllText($serviceFile, $content, [System.Text.Encoding]::UTF8)
    Write-Output "Installed v$version"

    # Verify no rollback flags exist
    foreach ($f in $flagFiles) {
        $fp = Join-Path $installDir $f
        if (Test-Path $fp) { Write-Output "WARNING: $f still exists!" }
    }

    # STEP 4: Start
    $p = Start-Process -FilePath 'python' -ArgumentList $serviceFile -WorkingDirectory $installDir -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 10
    if ($p.HasExited) {
        Write-Output "WARNING: exited code $($p.ExitCode)"
    } else {
        Write-Output "Running v$version PID=$($p.Id)"
    }
} catch {
    Write-Output "ERROR: $($_.Exception.Message)"
}
