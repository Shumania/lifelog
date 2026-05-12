# v60 - set cursor to Nov 13 2024, clear CORRECT hash file, run extraction (4-attempt retry, 15s between chunks)
$lifelogDir = "C:\ProgramData\LifeLog"

# Set cursor to Nov 13, 2024 23:55:34 Apple epoch (753234934)
# so only the ~260 missing Dec 2024 - May 2026 episodes are sent
$cursorFile = "$lifelogDir\last_podcast_cursor.txt"
"753234934" | Set-Content -Path $cursorFile -Encoding utf8 -NoNewline
Write-Host "Cursor set to 753234934 (Nov 13 2024 - only sending newer episodes)"

# Clear the backup hash to force extraction to run (CORRECT filename: last_backup_hash.txt)
$hashFile = "$lifelogDir\last_backup_hash.txt"
if (Test-Path $hashFile) { Remove-Item $hashFile -Force; Write-Host "Hash cleared." }

# Download latest lifelog_extract.py (v2.2 - better retry logic)
$pyDest = "$lifelogDir\lifelog_extract.py"
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py" -OutFile $pyDest -UseBasicParsing
Write-Host "Downloaded lifelog_extract.py v2.2"

# Run extraction
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python not found" }

& $python.Source $pyDest
