# v63 - reset cursor to Nov 2024, use fixed extractor v2.4 (no cursor-save-on-fail, 45s chunk delay)

$lifelogDir = "C:\ProgramData\LifeLog"
$pyDest     = "$lifelogDir\lifelog_extract.py"
$cursorFile = "$lifelogDir\last_podcast_cursor.txt"
$hashFile   = "$lifelogDir\last_backup_hash.txt"

# Step 1: Set cursor to Nov 10 2024 so only newer episodes are fetched
# Apple epoch 752890591 = 2024-11-10. This skips the ~1600 episodes already in DB.
[System.IO.File]::WriteAllText($cursorFile, "752890591", [System.Text.Encoding]::UTF8)
$readBack = [System.IO.File]::ReadAllText($cursorFile, [System.Text.Encoding]::UTF8)
Write-Host "Cursor set to 752890591, read-back: '$readBack'"

# Step 2: Clear backup hash to force decryption/extraction
if (Test-Path $hashFile) { Remove-Item $hashFile -Force; Write-Host "Hash cleared." }

# Step 3: Download fixed extractor v2.4
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py" -OutFile $pyDest -UseBasicParsing
Write-Host "Downloaded lifelog_extract.py v2.4"

# Step 4: Run extractor (cursor active = only Nov 2024+ episodes, ~250 total, ~2 chunks)
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python not found" }

Write-Host "=== RUNNING EXTRACTOR (cursor=752890591) ==="
& $python.Source $pyDest
