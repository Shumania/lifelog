# v59 - fix: clear CORRECT hash file (last_backup_hash.txt), reset cursor to Nov 2024
Write-Host "v59: clearing correct hash file and resetting cursor to Nov 2024..."

$lifelogDir = "C:\ProgramData\LifeLog"

# Reset cursor to Nov 10 2024 (Apple epoch 752890591)
$cursorFile = Join-Path $lifelogDir "last_podcast_cursor.txt"
[System.IO.File]::WriteAllText($cursorFile, "752890591", [System.Text.Encoding]::ASCII)
$readback = [System.IO.File]::ReadAllText($cursorFile).Trim()
Write-Host "Cursor set to 752890591, read-back: '$readback'"

# Clear the CORRECT hash file (extractor uses last_backup_hash.txt, NOT lifelog_backup_hash.txt)
$hashFile = Join-Path $lifelogDir "last_backup_hash.txt"
if (Test-Path $hashFile) { Remove-Item $hashFile -Force; Write-Host "Deleted $hashFile" }
else { Write-Host "$hashFile not found (already clear)" }

# Also clear the wrong-named one just in case
$wrongHash = Join-Path $lifelogDir "lifelog_backup_hash.txt"
if (Test-Path $wrongHash) { Remove-Item $wrongHash -Force; Write-Host "Also deleted $wrongHash" }

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
