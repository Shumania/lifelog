# v56 - set cursor to Nov 10 2024, run extraction (no manifest enumeration needed)

$lifelogDir = "C:\ProgramData\LifeLog"
$cursorFile = "$lifelogDir\last_podcast_cursor.txt"
$hashFile   = "$lifelogDir\last_backup_hash.txt"
$extractScript = "$lifelogDir\lifelog_extract.py"

# Set cursor to Apple epoch for Nov 10, 2024 00:16:31 UTC (our current DB max)
# Apple epoch = Unix timestamp - 978307200
# 1731197791 - 978307200 = 752890591
"752890591" | Set-Content -Path $cursorFile -Encoding UTF8
Write-Host "Cursor set to 752890591 (Nov 10, 2024)"

# Clear hash so extraction runs even if backup unchanged
if (Test-Path $hashFile) { Remove-Item $hashFile -Force }
Write-Host "Hash cleared - forcing extraction"

# Download latest lifelog_extract.py
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py" -OutFile $extractScript -UseBasicParsing
Write-Host "Downloaded latest lifelog_extract.py"

# Run extraction
Write-Host "Running extraction..."
python $extractScript
