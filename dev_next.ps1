# dev_next.ps1 v48 - run full lifelog extraction (fresh start)
$version = "dev_next.ps1 v48 - run full lifelog extraction (fresh start)"
Write-Host "[$env:COMPUTERNAME] $version"

$python = $null
$candidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\andre\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\ProgramData\LifeLog\python\python.exe"
)
foreach ($c in $candidates) { if (Test-Path $c) { $python = $c; break } }
if (-not $python) { throw "Python not found" }
Write-Host "[$env:COMPUTERNAME] Python: $python"

# Delete backup hash so extraction always runs fresh this time
$hashFile = "C:\ProgramData\LifeLog\last_backup_hash.txt"
if (Test-Path $hashFile) {
    Remove-Item $hashFile -Force
    Write-Host "[$env:COMPUTERNAME] Cleared backup hash (forcing fresh extraction)"
}

# Download latest lifelog_extract.py
$extractScript = "C:\ProgramData\LifeLog\lifelog_extract.py"
$ts = [int][double]::Parse((Get-Date -UFormat %s))
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?v=$ts" -OutFile $extractScript -UseBasicParsing
Write-Host "[$env:COMPUTERNAME] Downloaded lifelog_extract.py ($((Get-Item $extractScript).Length) bytes)"

# Run extraction
Write-Host "[$env:COMPUTERNAME] Starting extraction - this will take a few minutes..."
& $python $extractScript
Write-Host "[$env:COMPUTERNAME] v48 complete."
