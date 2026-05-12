# v57 - clear hash+cursor, extractor self-updates to 2.5 (BATCH_SIZE=20), re-extract missing Nov 2024 - May 2026
Write-Host "v57: clearing hash and cursor for fresh extraction of missing episodes..."

$lifelogDir = "C:\ProgramData\LifeLog"

# Set cursor to Nov 10 2024 so we get everything from Nov 10 onwards
$cursorFile = Join-Path $lifelogDir "last_podcast_cursor.txt"
[System.IO.File]::WriteAllText($cursorFile, "752890591", [System.Text.Encoding]::ASCII)
Write-Host "Cursor set to 752890591 (Nov 10 2024)"

# Clear hash to force decryption
$hashFile = Join-Path $lifelogDir "lifelog_backup_hash.txt"
if (Test-Path $hashFile) { Remove-Item $hashFile -Force }
Write-Host "Hash cleared."

# Download latest extractor (will self-check version and run)
$extractorPath = Join-Path $lifelogDir "lifelog_extract.py"
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py" -OutFile $extractorPath -UseBasicParsing
Write-Host "Downloaded lifelog_extract.py v2.5"

# Run extractor
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python not found" }

& $python.Source $extractorPath
Write-Host "Extraction complete."
