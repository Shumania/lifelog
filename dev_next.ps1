# dev_next.ps1 v46 - run full lifelog_extract.py extraction
$version = "dev_next.ps1 v46 - run full lifelog_extract.py extraction"
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

$extractScript = "C:\ProgramData\LifeLog\lifelog_extract.py"
if (-not (Test-Path $extractScript)) {
    Write-Host "[$env:COMPUTERNAME] Downloading lifelog_extract.py..."
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py" -OutFile $extractScript -UseBasicParsing
}

Write-Host "[$env:COMPUTERNAME] Running extraction..."
& $python $extractScript
Write-Host "[$env:COMPUTERNAME] v46 complete."
