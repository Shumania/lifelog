# dev_next.ps1 v35 - run extraction with WAL fix
$Machine = $env:COMPUTERNAME
Write-Host "[$Machine] dev_next.ps1 v35"

# Find Python
$PythonPath = $null
$candidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\andre\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python314\python.exe",
    "C:\ProgramData\LifeLog\python\python.exe"
)
foreach ($p in $candidates) {
    if (Test-Path $p) { $PythonPath = $p; break }
}
if (-not $PythonPath) {
    throw "[$Machine] Python not found in any known location."
}
Write-Host "[$Machine] Using Python: $PythonPath"

# Download latest lifelog_extract.py (with WAL fix)
$scriptPath = "C:\ProgramData\LifeLog\lifelog_extract.py"
Write-Host "[$Machine] Downloading latest lifelog_extract.py..."
try {
    $ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?v=$ts" -OutFile $scriptPath -UseBasicParsing
    Write-Host "[$Machine] Download OK"
} catch {
    Write-Host "[$Machine] Download failed: $_"
}

# Force re-extraction by deleting hash
$hashFile = "C:\ProgramData\LifeLog\last_backup_hash.txt"
if (Test-Path $hashFile) {
    Remove-Item $hashFile -Force
    Write-Host "[$Machine] Hash file deleted - forcing fresh extraction"
}

# Run extraction
Write-Host "[$Machine] Running lifelog_extract.py..."
& $PythonPath $scriptPath
Write-Host "[$Machine] Done."
