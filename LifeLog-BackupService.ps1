# Scan and Update iPhone Backup Service v2.4 - ntfy update_check support
$LOOP_VERSION   = "2.4"
$SERVICE_NAME   = "Scan and Update iPhone Backup Service"
$INSTALL_PATH   = "C:\ProgramData\LifeLog\LifeLog-BackupService.ps1"
$versionsApiUrl  = "https://api.github.com/repos/Shumania/lifelog/contents/versions.json"
$loopApiUrl      = "https://api.github.com/repos/Shumania/lifelog/contents/LifeLog-BackupService.ps1"
$apiUrl          = "https://api.github.com/repos/Shumania/lifelog/contents/dev_next.ps1"
$webhookUrl      = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$heartbeatUrl    = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=be22b43febe39260b284d21672db539f"
$interval        = 100
$lastSha         = ""
$VERSION_CHECK_EVERY = 6   # check for loop updates every N poll cycles (~10 min)
$HEARTBEAT_EVERY = 3       # send heartbeat every N poll cycles (~5 min)
$loopCycle       = 0
$heartbeatCycle  = 0

# Detect house from Sonos config if present
$house = "unknown"
try {
    $sonosCfg = Get-Content "C:\ProgramData\LifeLog\sonos_config.json" -ErrorAction Stop | ConvertFrom-Json
    $house = $sonosCfg.house
} catch {}

# Determine ntfy topic from house
$ntfyTopics = @{
    "caphill" = "lifelog-cmd-caphill-4x8m"
    "vashon"  = "lifelog-cmd-vashon-9k3p"
}
$ntfyTopic = $ntfyTopics[$house]
if (-not $ntfyTopic) { $ntfyTopic = "lifelog-cmd-caphill-4x8m" }
$ntfyLastSince = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

function Send-Heartbeat {
    try {
        $ts = [System.DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        $hb = @{
            type        = "heartbeat"
            client_id   = "backup_$env:COMPUTERNAME"
            client_type = "backup_service"
            house       = $house
            version     = $LOOP_VERSION
            timestamp   = $ts
            computer    = $env:COMPUTERNAME
        } | ConvertTo-Json -Compress
        Invoke-WebRequest -Uri $heartbeatUrl -Method Post -Body $hb -ContentType "application/json" -UseBasicParsing | Out-Null
        Write-Host "  ♥ Heartbeat sent" -ForegroundColor DarkGray
    } catch {
        Write-Host "  Heartbeat failed: $_" -ForegroundColor DarkGray
    }
}

# --- Self-update function -------------------------------------------------------
function Check-LoopUpdate {
    Write-Host "Checking for service updates..." -NoNewline
    try {
        $vResp    = Invoke-WebRequest -Uri $versionsApiUrl -UseBasicParsing -Headers @{ "User-Agent" = "LifeLog-BackupService" }
        $vJson    = $vResp.Content | ConvertFrom-Json
        $vContent = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String(($vJson.content -replace "`n","")))
        $versions = $vContent | ConvertFrom-Json

        if ($versions.loop_version -ne $LOOP_VERSION) {
            Write-Host " UPDATE: $LOOP_VERSION -> $($versions.loop_version)" -ForegroundColor Yellow
            Write-Host "Downloading new version and restarting..." -ForegroundColor Yellow

            $lResp    = Invoke-WebRequest -Uri $loopApiUrl -UseBasicParsing -Headers @{ "User-Agent" = "LifeLog-BackupService" }
            $lJson    = $lResp.Content | ConvertFrom-Json
            $lContent = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String(($lJson.content -replace "`n","")))

            New-Item -ItemType Directory -Force -Path (Split-Path $INSTALL_PATH) | Out-Null
            [System.IO.File]::WriteAllText($INSTALL_PATH, $lContent, [System.Text.Encoding]::UTF8)
            Write-Host "Saved. Restarting..." -ForegroundColor Green

            Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -File `"$INSTALL_PATH`""
            exit 0
        } else {
            Write-Host " Up to date (v$LOOP_VERSION)." -ForegroundColor Green
        }
    } catch {
        Write-Host " Version check failed (running anyway): $_" -ForegroundColor DarkGray
    }
}

# --- ntfy command polling -------------------------------------------------------
function Check-NtfyCommands {
    try {
        $url = "https://ntfy.sh/$ntfyTopic/json?since=$ntfyLastSince&poll=1"
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
        $script:ntfyLastSince = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        $lines = $resp.Content -split "`n" | Where-Object { $_.Trim() -ne "" }
        foreach ($line in $lines) {
            try {
                $msg = $line | ConvertFrom-Json
                if ($msg.message) {
                    $cmd = $msg.message | ConvertFrom-Json
                    $action = $cmd.action
                    if ($action -eq "update_check") {
                        Write-Host "  [ntfy] update_check received — checking now..." -ForegroundColor Cyan
                        Check-LoopUpdate
                    }
                }
            } catch {}
        }
    } catch {
        # ntfy unreachable — silent, non-blocking
    }
}
# -------------------------------------------------------------------------------

Write-Host "=== $SERVICE_NAME v$LOOP_VERSION ===" -ForegroundColor Cyan
Write-Host "Machine: $env:COMPUTERNAME | House: $house | ntfy: $ntfyTopic" -ForegroundColor Gray

# Check for updates at startup
Check-LoopUpdate

# Send startup heartbeat
Send-Heartbeat

Write-Host "Polling every $interval seconds via GitHub API." -ForegroundColor Gray
Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

while ($true) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $loopCycle++
    $heartbeatCycle++

    # Check ntfy for instant commands (every cycle)
    Check-NtfyCommands

    # Periodic self-update check every ~10 minutes
    if ($loopCycle % $VERSION_CHECK_EVERY -eq 0) {
        Check-LoopUpdate
    }

    # Periodic heartbeat every ~5 minutes
    if ($heartbeatCycle % $HEARTBEAT_EVERY -eq 0) {
        Send-Heartbeat
    }

    Write-Host "[$ts] Checking dev_next.ps1..." -NoNewline

    try {
        $resp = Invoke-WebRequest -Uri $apiUrl -UseBasicParsing -Headers @{ "User-Agent" = "LifeLog-BackupService" }
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
