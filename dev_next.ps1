# dev_next.ps1 v49 - re-extract podcasts (clear hash first)
$version = "dev_next.ps1 v49 - re-extract podcasts (clear hash first)"
Write-Host "[$env:COMPUTERNAME] $version"

$python = $null
$candidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python313\python.exe",
    "python"
)
foreach ($p in $candidates) {
    if (Get-Command $p -ErrorAction SilentlyContinue) { $python = $p; break }
}
if (-not $python) { throw "Python not found" }
Write-Host "[$env:COMPUTERNAME] Python: $python"

# Delete the backup hash so extraction runs fresh
$hashFile = "C:\ProgramData\LifeLog\lifelog_backup_hash.txt"
if (Test-Path $hashFile) {
    Remove-Item $hashFile -Force
    Write-Host "[$env:COMPUTERNAME] Cleared backup hash - will re-extract."
} else {
    Write-Host "[$env:COMPUTERNAME] No hash file found - will extract fresh."
}

# Download latest lifelog_extract.py
$extractUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py"
$extractPath = "C:\ProgramData\LifeLog\lifelog_extract.py"
Invoke-WebRequest -Uri $extractUrl -OutFile $extractPath -UseBasicParsing
Write-Host "[$env:COMPUTERNAME] Downloaded lifelog_extract.py"

# Run extraction
Write-Host "[$env:COMPUTERNAME] Running extraction..."
& $python $extractPath 2>&1 | ForEach-Object { Write-Host $_ }
Write-Host "[$env:COMPUTERNAME] v49 complete."
