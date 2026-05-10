# dev_next.ps1 v49 - re-run extraction to fill missing Dec 2024-May 2026 episodes
$computer = $env:COMPUTERNAME
Write-Output "[$computer] dev_next.ps1 v49 - re-run extraction (fill gap Dec2024-May2026)"

$python = $null
$candidates = @(
    "C:\Users\andre\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\andre\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\Users\Shumadmin\AppData\Local\Programs\Python\Python313\python.exe",
    (Get-Command python -ErrorAction SilentlyContinue)?.Source
)
foreach ($p in $candidates) {
    if ($p -and (Test-Path $p)) { $python = $p; break }
}
if (-not $python) {
    Write-Output "[$computer] ERROR: Python not found"
    throw "Python not found"
}
Write-Output "[$computer] Python: $python"

# Clear backup hash to force re-extraction
$hashFile = "C:\ProgramData\LifeLog\last_backup_hash.txt"
if (Test-Path $hashFile) {
    Remove-Item $hashFile -Force
    Write-Output "[$computer] Cleared backup hash (forcing re-extraction)"
} else {
    Write-Output "[$computer] Hash file not found (will extract fresh)"
}

# Download latest lifelog_extract.py
$extractUrl = "https://api.github.com/repos/Shumania/lifelog/contents/lifelog_extract.py"
$extractDest = "C:\ProgramData\LifeLog\lifelog_extract.py"
try {
    $response = Invoke-WebRequest -Uri $extractUrl -UseBasicParsing -Headers @{"Accept"="application/vnd.github.v3.raw"; "User-Agent"="LifeLog"}
    [System.IO.File]::WriteAllBytes($extractDest, $response.Content)
    Write-Output "[$computer] Downloaded lifelog_extract.py ($($response.Content.Length) bytes)"
} catch {
    Write-Output "[$computer] WARNING: Could not download lifelog_extract.py: $_"
    if (-not (Test-Path $extractDest)) { throw "lifelog_extract.py not found" }
    Write-Output "[$computer] Using existing lifelog_extract.py"
}

Write-Output "[$computer] Starting extraction - filling in missing recent episodes..."
& $python $extractDest
Write-Output "[$computer] v49 complete."
