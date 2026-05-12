# v62 - cursor file diagnostic + targeted test of newest 10 episodes

$lifelogDir = "C:\ProgramData\LifeLog"
$pyDest = "$lifelogDir\lifelog_extract.py"
$cursorFile = "$lifelogDir\last_podcast_cursor.txt"
$hashFile = "$lifelogDir\last_backup_hash.txt"
$diagScript = "$lifelogDir\diag_newest.py"

# Step 1: Cursor file diagnostics
Write-Host "=== CURSOR FILE DIAGNOSTIC ==="
if (Test-Path $cursorFile) {
    $cursorContent = Get-Content $cursorFile -Raw
    $cursorBytes = [System.IO.File]::ReadAllBytes($cursorFile)
    Write-Host "Cursor file EXISTS. Content: '$cursorContent' (len=$($cursorContent.Length) bytes=$($cursorBytes.Length))"
    Write-Host "First 3 bytes (hex): $($cursorBytes[0].ToString('X2')) $($cursorBytes[1].ToString('X2')) $($cursorBytes[2].ToString('X2'))"
} else {
    Write-Host "Cursor file DOES NOT EXIST"
}

# Step 2: Write cursor, read it back
[System.IO.File]::WriteAllText($cursorFile, "757000000", [System.Text.Encoding]::UTF8)
$readBack = [System.IO.File]::ReadAllText($cursorFile)
Write-Host "Wrote cursor 757000000, read back: '$readBack'"

# Step 3: Clear hash to force extraction
if (Test-Path $hashFile) { Remove-Item $hashFile -Force; Write-Host "Hash cleared." }

# Step 4: Download latest extractor
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py" -OutFile $pyDest -UseBasicParsing
Write-Host "Downloaded lifelog_extract.py"

# Step 5: Write a diagnostic Python script that ONLY extracts newest 15 episodes and prints them
@"
import sys, os, plistlib, sqlite3, hashlib, shutil, tempfile, json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(r'C:\ProgramData\LifeLog')))
# Load the extractor module to reuse its functions
spec_path = r'C:\ProgramData\LifeLog\lifelog_extract.py'

# Read cursor file
cursor_file = Path(r'C:\ProgramData\LifeLog\last_podcast_cursor.txt')
if cursor_file.exists():
    val = cursor_file.read_text(encoding='utf-8-sig').strip()
    print(f'CURSOR_FILE_CONTENT: [{val}] (len={len(val)})')
    try:
        cursor_float = float(val)
        print(f'CURSOR_FLOAT: {cursor_float}')
    except Exception as e:
        print(f'CURSOR_PARSE_ERROR: {e}')
else:
    print('CURSOR_FILE_MISSING')

# Also check Python version
print(f'PYTHON: {sys.version}')
print(f'PLATFORM: {sys.platform}')
"@ | Out-File -FilePath $diagScript -Encoding UTF8

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python not found" }

Write-Host "=== PYTHON DIAGNOSTIC ==="
& $python.Source $diagScript

# Step 6: Run actual extractor
Write-Host "=== RUNNING EXTRACTOR (cursor=757000000 = Jan 2025) ==="
& $python.Source $pyDest
