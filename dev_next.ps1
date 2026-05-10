# v51
# Set cursor to Nov 10 2024 (Apple epoch) so only newer episodes are extracted
# Nov 10 2024 Unix epoch ~1731196591 → Apple epoch = 1731196591 - 978307200 = 752889391

$cursorFile = "C:\ProgramData\LifeLog\last_podcast_cursor.txt"
$hashFile   = "C:\ProgramData\LifeLog\last_backup_hash.txt"

Write-Host "Setting podcast cursor to Nov 10 2024 (Apple epoch 752889391)..."
"752889391" | Set-Content $cursorFile -Encoding UTF8

Write-Host "Clearing backup hash to force re-extraction..."
Remove-Item $hashFile -ErrorAction SilentlyContinue

Write-Host "Downloading latest lifelog_extract.py..."
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py" -OutFile "C:\ProgramData\LifeLog\lifelog_extract.py" -UseBasicParsing

Write-Host "Running extraction (only episodes after Nov 10 2024)..."
$python = "python"
& $python "C:\ProgramData\LifeLog\lifelog_extract.py" 2>&1
Write-Host "Done."
