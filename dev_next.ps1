# dev_next: fetch GitHub PAT from ntfy, inject into config, force v1.38 update

$configPath = "C:\ProgramData\LifeLog\lifelog_config.json"

# Step 1: Fetch token from ntfy (plain text endpoint - simpler)
try {
    $resp = Invoke-WebRequest -Uri "https://ntfy.sh/lifelog-gh-token-8x2k/raw?poll=1&since=12h" -UseBasicParsing
    $token = $resp.Content.Trim()
    if (-not $token -or $token.Length -lt 20) { throw "No valid token found, got: '$token'" }
    Write-Output "Got token: $($token.Substring(0,10))... (len=$($token.Length))"
} catch {
    Write-Output "ERROR fetching token: $_"
    exit 1
}

# Step 2: Inject into config
try {
    $config = Get-Content $configPath -Raw | ConvertFrom-Json
    $config | Add-Member -NotePropertyName 'github_token' -NotePropertyValue $token -Force
    $config | ConvertTo-Json -Depth 10 | Set-Content $configPath -Encoding UTF8
    Write-Output "Injected github_token into config"
} catch {
    Write-Output "ERROR updating config: $_"
    exit 1
}

# Step 3: Force download v1.38 from raw.githubusercontent.com
try {
    $servicePath = "C:\ProgramData\LifeLog\lifelog_service.py"
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_service.py" -OutFile $servicePath -UseBasicParsing
    Write-Output "Downloaded v1.38 service"
} catch {
    Write-Output "ERROR downloading service: $_"
    exit 1
}

# Step 4: Kill running service so Start-LifeLog restarts it
try {
    Get-Process python* -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
            if ($cmdline -like '*lifelog*') {
                $_ | Stop-Process -Force
                Write-Output "Killed PID $($_.Id)"
            }
        } catch {}
    }
    Write-Output "Service will restart via Start-LifeLog.ps1"
} catch {
    Write-Output "ERROR killing service: $_"
}

Write-Output "DONE - v1.38 with GitHub PAT"
