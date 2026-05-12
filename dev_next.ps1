# v61 - fix BOM bug: use WriteAllText (no BOM) instead of Set-Content utf8
$lifelogDir = "C:\ProgramData\LifeLog"

# Set cursor to Nov 13, 2024 23:55:34 Apple epoch (753234934)
# so only the ~260 missing Dec 2024 - May 2026 episodes are sent
# IMPORTANT: use [System.IO.File]::WriteAllText to avoid UTF-8 BOM (PowerShell 5 Set-Content adds BOM, breaking Python float())
$cursorFile = "$lifelogDir\last_podcast_cursor.txt"
[System.IO.File]::WriteAllText($cursorFile, "753234934")
Write-Host "Cursor set to 753234934 (Nov 13 2024 - only sending newer episodes, no BOM)"

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
