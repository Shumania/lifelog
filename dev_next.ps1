# dev_next.ps1 v46 - run full podcast extraction via lifelog_extract.py
$computer = $env:COMPUTERNAME

Write-Host "[$computer] dev_next.ps1 v46 - full podcast extraction"

# Download latest lifelog_extract.py
$extractPy = "$env:TEMP\lifelog_extract_v46.py"
Write-Host "[$computer] Downloading lifelog_extract.py..."
try {
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?r=$(Get-Random)" -OutFile $extractPy -UseBasicParsing
    $lines = (Get-Content $extractPy).Count
    Write-Host "[$computer] Downloaded ($lines lines)"
} catch {
    throw "Failed to download lifelog_extract.py: $_"
}

# Find Python
$pythonCandidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\andre\AppData\Local\Programs\Python\Python313\python.exe"
)
$python = $null
foreach ($p in $pythonCandidates) {
    if (Test-Path $p) { $python = $p; break }
}
if (-not $python) { throw "Python not found" }
Write-Host "[$computer] Python: $python"

# Run extraction
Write-Host "[$computer] Running extraction (this may take a few minutes)..."
& $python $extractPy
Write-Host "[$computer] v46 complete."
