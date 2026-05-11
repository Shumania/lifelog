# v52
# Reset cursor to Nov 10 2024 to re-fetch only missing Dec 2024+ episodes
# (batch size reduced to 50 on server side to fix SQL size limit issue)

$LifeLogDir = "C:\ProgramData\LifeLog"
$cursorFile = "$LifeLogDir\last_podcast_cursor.txt"
$hashFile   = "$LifeLogDir\last_backup_hash.txt"
$extractScript = "$LifeLogDir\lifelog_extract.py"

Write-Host "Resetting podcast cursor to Nov 10 2024 (Apple epoch 752889391)..."
"752889391" | Set-Content -Path $cursorFile -Encoding UTF8

Write-Host "Clearing backup hash to force re-extraction..."
if (Test-Path $hashFile) { Remove-Item $hashFile -Force }

Write-Host "Downloading latest lifelog_extract.py..."
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py" -OutFile $extractScript -UseBasicParsing

Write-Host "Running extraction (only episodes after Nov 10 2024 — expecting ~230)..."
$py = "python"
& $py $extractScript
