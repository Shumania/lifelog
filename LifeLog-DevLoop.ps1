# LifeLog-DevLoop.ps1
# Remote-control dev loop: polls GitHub every 100s, runs dev_next.ps1 only when version changes.
# Cache-busting via Unix timestamp param. Version parsed from first line comment.

$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$installDir = "C:\ProgramData\LifeLog"
$versionFile = "$installDir\dev_next_last_version.txt"
$intervalSeconds = 100

Write-Host "=== LifeLog Dev Loop ===" -ForegroundColor Cyan
Write-Host "Polling every $intervalSeconds seconds. Only runs when dev_next.ps1 version changes."
Write-Host "Press Ctrl+C to stop."
Write-Host ""

# Load last run version
$lastVersion = ""
if (Test-Path $versionFile) {
    $lastVersion = (Get-Content $versionFile -Raw).Trim()
    Write-Host "Last run version: $lastVersion"
}

while ($true) {
    $ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $runTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$runTime] Checking dev_next.ps1..." -NoNewline

    try {
        # Download with cache-bust
        $tempScript = "$env:TEMP\lifelog_dev_next_$ts.ps1"
        $rawUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/dev_next.ps1?v=$ts"
        Invoke-WebRequest -Uri $rawUrl -OutFile $tempScript -UseBasicParsing

        # Parse version from first line comment (e.g. "# dev_next.ps1 v39")
        $firstLine = (Get-Content $tempScript -TotalCount 1).Trim()
        $currentVersion = $firstLine -replace '^#\s*', ''

        if ($currentVersion -eq $lastVersion) {
            Write-Host " No change ($currentVersion). Skipping." -ForegroundColor DarkGray
            Remove-Item $tempScript -ErrorAction SilentlyContinue
        } else {
            Write-Host " NEW VERSION: $currentVersion (was: $lastVersion)" -ForegroundColor Yellow

            # Run it
            $output = & powershell.exe -ExecutionPolicy Bypass -File $tempScript 2>&1 | Out-String
            Remove-Item $tempScript -ErrorAction SilentlyContinue

            # Save version so we don't re-run
            $currentVersion | Set-Content $versionFile

            $lastVersion = $currentVersion

            Write-Host "  Output: $($output.Length) chars. Uploading..." -NoNewline

            $body = @{
                source    = "LifeLog-DevLoop"
                computer  = $env:COMPUTERNAME
                timestamp = $runTime
                output    = $output
            } | ConvertTo-Json -Depth 3

            Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType "application/json" | Out-Null
            Write-Host " Sent!" -ForegroundColor Green
        }
    }
    catch {
        $errMsg = $_ | Out-String
        Write-Host " ERROR: $errMsg" -ForegroundColor Red
        try {
            $body = @{
                source    = "LifeLog-DevLoop"
                computer  = $env:COMPUTERNAME
                timestamp = $runTime
                output    = "ERROR in dev loop: $errMsg"
            } | ConvertTo-Json -Depth 3
            Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType "application/json" | Out-Null
        } catch {}
    }

    Write-Host "  Sleeping $intervalSeconds seconds..."
    Start-Sleep -Seconds $intervalSeconds
}
