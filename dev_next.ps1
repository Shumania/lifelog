# v58 - reset cursor to Nov 2024, re-extract missing Dec 2024 - May 2026 episodes
Write-Host "v58: resetting cursor to Nov 10 2024 and clearing hash for re-extraction..."

$lifelogDir = "C:\ProgramData\LifeLog"

# Reset cursor to Nov 10 2024 (Apple epoch 752890591)
# Our DB has 2,009 episodes up to Nov 13 2024 - need Dec 2024 onwards
$cursorFile = Join-Path $lifelogDir "last_podcast_cursor.txt"
[System.IO.File]::WriteAllText($cursorFile, "752890591", [System.Text.Encoding]::ASCII)
$readback = [System.IO.File]::ReadAllText($cursorFile).Trim()
Write-Host "Cursor set to 752890591 (Nov 10 2024), read-back: '$readback'"

# Clear hash to force decryption/extraction
$hashFile = Join-Path $lifelogDir "lifelog_backup_hash.txt"
if (Test-Path $hashFile) { Remove-Item $hashFile -Force }
Write-Host "Hash cleared."

# Download latest extractor
$extractorPath = Join-Path $lifelogDir "lifelog_extract.py"
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py" -OutFile $extractorPath -UseBasicParsing
Write-Host "Downloaded lifelog_extract.py"

# Run extractor
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python not found" }

& $python.Source $extractorPath
Write-Host "Extraction complete."
