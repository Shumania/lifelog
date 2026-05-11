# v57 - set cursor to Nov 10 2024, run extraction (clean retry)

$lifelogDir = "C:\ProgramData\LifeLog"
$cursorFile = "$lifelogDir\last_podcast_cursor.txt"
$hashFile   = "$lifelogDir\last_backup_hash.txt"
$extractScript = "$lifelogDir\lifelog_extract.py"
$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

function Post-Output($msg) {
    $body = @{ computer = $env:COMPUTERNAME; output = $msg } | ConvertTo-Json
    try { Invoke-WebRequest -Uri $webhookUrl -Method POST -Body $body -ContentType "application/json" -UseBasicParsing | Out-Null } catch {}
}

Post-Output "v57 starting on $env:COMPUTERNAME"

# Set cursor to Apple epoch for Nov 10, 2024 00:16:31 UTC
# Apple epoch = Unix timestamp - 978307200 => 1731197791 - 978307200 = 752890591
"752890591" | Set-Content -Path $cursorFile -Encoding UTF8
Write-Host "Cursor set to 752890591 (Nov 10, 2024)"

# Clear hash to force extraction even if backup unchanged
if (Test-Path $hashFile) { Remove-Item $hashFile -Force }
Write-Host "Hash cleared"

# Download latest lifelog_extract.py
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?$(Get-Random)" -OutFile $extractScript -UseBasicParsing
Write-Host "Downloaded lifelog_extract.py"

Post-Output "v57 running lifelog_extract.py on $env:COMPUTERNAME (cursor=752890591)"

# Run extraction
python $extractScript
