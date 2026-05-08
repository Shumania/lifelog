# dev_next.ps1 v33 - force re-extraction by deleting hash file
$computer = $env:COMPUTERNAME
$lifelogDir = "C:\ProgramData\LifeLog"

Write-Output "[$computer] dev_next.ps1 v33"

# Find existing real Python (skip WindowsApps stub)
$python = $null
$candidates = @(
    "C:\Python313\python.exe","C:\Python312\python.exe","C:\Python311\python.exe",
    "C:\Python310\python.exe","C:\Python39\python.exe","C:\Python38\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "$lifelogDir\python\python.exe"
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $python = $c; break }
}
if (-not $python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found -and $found.Source -notlike "*WindowsApps*") { $python = $found.Source }
}

if (-not $python) {
    Write-Output "[$computer] Python not found. Please run: winget install Python.Python.3.12"
    Write-Output "[$computer] Or download from https://www.python.org/downloads/"
    return
}

Write-Output "[$computer] Using Python: $python"

# Install iphone_backup_decrypt if needed
$testImport = & $python -c "import iphone_backup_decrypt; print('ok')" 2>&1
if ($testImport -notmatch "ok") {
    Write-Output "[$computer] Installing iphone_backup_decrypt..."
    & $python -m pip install --quiet iphone_backup_decrypt 2>&1
}

# FORCE re-extraction by deleting hash file
$hashFile = "$lifelogDir\last_backup_hash.txt"
if (Test-Path $hashFile) {
    Remove-Item $hashFile -Force
    Write-Output "[$computer] Cleared backup hash - forcing fresh extraction."
} else {
    Write-Output "[$computer] No hash file found - will extract fresh."
}

# Download latest lifelog_extract.py
$ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$script = "$lifelogDir\lifelog_extract.py"
Write-Output "[$computer] Downloading latest lifelog_extract.py..."
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?t=$ts" -OutFile $script -UseBasicParsing
$lines = (Get-Content $script).Count
Write-Output "[$computer] Downloaded OK ($lines lines)."

# Run extraction
Write-Output "[$computer] Running extraction..."
& $python $script 2>&1
Write-Output "[$computer] Done."
