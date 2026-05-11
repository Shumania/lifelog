# LifeLog Dev Loop v2.1 - self-updating, polls GitHub API for dev_next.ps1 changes
$LOOP_VERSION  = "2.1"
$INSTALL_PATH  = "C:\ProgramData\LifeLog\LifeLog-DevLoop.ps1"
$versionsApiUrl = "https://api.github.com/repos/Shumania/lifelog/contents/versions.json"
$loopApiUrl    = "https://api.github.com/repos/Shumania/lifelog/contents/LifeLog-DevLoop.ps1"
$apiUrl        = "https://api.github.com/repos/Shumania/lifelog/contents/dev_next.ps1"
$webhookUrl    = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$interval      = 100
$lastSha       = ""

Write-Host "=== LifeLog Dev Loop v$LOOP_VERSION ===" -ForegroundColor Cyan
Write-Host "Machine: $env:COMPUTERNAME" -ForegroundColor Gray

# --- Self-update check -------------------------------------------------------
Write-Host "Checking for loop updates..." -NoNewline
try {
    $vResp    = Invoke-WebRequest -Uri $versionsApiUrl -UseBasicParsing -Headers @{ "User-Agent" = "LifeLog-DevLoop" }
    $vJson    = $vResp.Content | ConvertFrom-Json
    $vContent = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String(($vJson.content -replace "`n","")))
    $versions = $vContent | ConvertFrom-Json

    if ($versions.loop_version -ne $LOOP_VERSION) {
        Write-Host " UPDATE: $LOOP_VERSION -> $($versions.loop_version)" -ForegroundColor Yellow
        Write-Host "Downloading new loop and restarting..." -ForegroundColor Yellow

        $lResp    = Invoke-WebRequest -Uri $loopApiUrl -UseBasicParsing -Headers @{ "User-Agent" = "LifeLog-DevLoop" }
        $lJson    = $lResp.Content | ConvertFrom-Json
        $lContent = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String(($lJson.content -replace "`n","")))

        New-Item -ItemType Directory -Force -Path (Split-Path $INSTALL_PATH) | Out-Null
        [System.IO.File]::WriteAllText($INSTALL_PATH, $lContent, [System.Text.Encoding]::UTF8)
        Write-Host "Saved to $INSTALL_PATH. Spawning updated loop..." -ForegroundColor Green

        Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -File `"$INSTALL_PATH`""
        exit 0
    } else {
        Write-Host " Up to date (v$LOOP_VERSION)." -ForegroundColor Green
    }
} catch {
    Write-Host " Version check failed (running anyway): $_" -ForegroundColor DarkGray
}
# -----------------------------------------------------------------------------

Write-Host "Polling every $interval seconds via GitHub API." -ForegroundColor Gray
Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

while ($true) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] Checking dev_next.ps1..." -NoNewline

    try {
        $resp = Invoke-WebRequest -Uri $apiUrl -UseBasicParsing -Headers @{ "User-Agent" = "LifeLog-DevLoop" }
        $json = $resp.Content | ConvertFrom-Json
        $sha  = $json.sha.Substring(0, 12)
        $scriptB64 = $json.content -replace "`n",""
        $scriptContent = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($scriptB64))

        # Extract version comment from first line
        $firstLine = ($scriptContent -split "`n")[0].Trim()

        if ($sha -ne $lastSha) {
            Write-Host " NEW SHA: $sha" -ForegroundColor Green
            Write-Host "  Version: $firstLine" -ForegroundColor Yellow

            $tmpFile = "$env:TEMP\dev_next_run.ps1"
            [System.IO.File]::WriteAllText($tmpFile, $scriptContent, [System.Text.Encoding]::UTF8)

            $output = powershell -ExecutionPolicy Bypass -File $tmpFile 2>&1 | Out-String
            $lastSha = $sha

            Write-Host "  Output: $($output.Length) chars. Uploading..." -NoNewline

            $body = @{
                computer = $env:COMPUTERNAME
                sha      = $sha
                version  = $firstLine
                output   = $output
            } | ConvertTo-Json -Compress

            Invoke-WebRequest -Uri $webhookUrl -Method Post -Body $body -ContentType "application/json" -UseBasicParsing | Out-Null
            Write-Host " Sent!" -ForegroundColor Green
        } else {
            Write-Host " No change ($sha). Skipping." -ForegroundColor DarkGray
        }
    } catch {
        Write-Host " ERROR: $_" -ForegroundColor Red
    }

    Write-Host "  Sleeping $interval seconds..."
    Start-Sleep -Seconds $interval
}
