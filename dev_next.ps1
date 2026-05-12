# v58 - clear hash only (keep cursor at Nov 13 2024), extract remaining episodes

$lifelogDir = "C:\ProgramData\LifeLog"
$hashFile   = "$lifelogDir\last_backup_hash.txt"
$extractScript = "$lifelogDir\lifelog_extract.py"
$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

function Post-Output($msg) {
    $body = @{ computer = $env:COMPUTERNAME; output = $msg } | ConvertTo-Json
    try { Invoke-WebRequest -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json" -UseBasicParsing | Out-Null } catch {}
}

Post-Output "v58 starting on $env:COMPUTERNAME"

# Read current cursor for reporting (do NOT change it)
$cursorFile = "$lifelogDir\last_podcast_cursor.txt"
$cursor = if (Test-Path $cursorFile) { Get-Content $cursorFile -Raw } else { "none" }
Write-Host "Current cursor: $cursor (not modified)"

# Clear hash to force extraction even if backup unchanged
if (Test-Path $hashFile) { Remove-Item $hashFile -Force }
Write-Host "Hash cleared - will extract episodes after cursor"

# Download latest lifelog_extract.py
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?$(Get-Random)" -OutFile $extractScript -UseBasicParsing
Write-Host "Downloaded lifelog_extract.py"

Post-Output "v58 running on $env:COMPUTERNAME (cursor=$cursor, expecting ~32 remaining episodes)"

# Run extraction
python $extractScript
