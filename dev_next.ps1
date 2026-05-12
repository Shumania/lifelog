# v59 - clear cursor+hash, download extractor v2.2 (retry logic + always saves cursor), run extraction
$lifelogDir = "C:\ProgramData\LifeLog"

# Clear cursor and hash so we do a full fresh send with the fixed extractor
$cursorFile = "$lifelogDir\last_podcast_cursor.txt"
$hashFile = "$lifelogDir\lifelog_backup_hash.txt"
if (Test-Path $cursorFile) { Remove-Item $cursorFile -Force; Write-Host "Cursor cleared." }
if (Test-Path $hashFile)   { Remove-Item $hashFile -Force;   Write-Host "Hash cleared." }

# Download latest lifelog_extract.py (v2.2)
$pyDest = "$lifelogDir\lifelog_extract.py"
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py" -OutFile $pyDest -UseBasicParsing
Write-Host "Downloaded lifelog_extract.py v2.2"

# Run extraction
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python not found" }

& $python.Source $pyDest
