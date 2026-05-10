# dev_next.ps1 v50 - reset cursor + run extraction with new cursor-based dedup
$computer = $env:COMPUTERNAME
Write-Output "[$computer] dev_next.ps1 v50 - cursor reset + fresh extraction"

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

# Clear hash and cursor to force a full fresh extraction
foreach ($f in @("C:\ProgramData\LifeLog\last_backup_hash.txt", "C:\ProgramData\LifeLog\last_podcast_cursor.txt")) {
    if (Test-Path $f) {
        Remove-Item $f -Force
        Write-Output "[$computer] Cleared: $f"
    }
}

# Download latest lifelog_extract.py via GitHub API (no CDN cache)
$extractDest = "C:\ProgramData\LifeLog\lifelog_extract.py"
try {
    $response = Invoke-WebRequest -Uri "https://api.github.com/repos/Shumania/lifelog/contents/lifelog_extract.py" -UseBasicParsing -Headers @{"Accept"="application/vnd.github.v3.raw"; "User-Agent"="LifeLog"}
    [System.IO.File]::WriteAllBytes($extractDest, $response.Content)
    Write-Output "[$computer] Downloaded lifelog_extract.py ($($response.Content.Length) bytes)"
} catch {
    Write-Output "[$computer] WARNING: Could not download lifelog_extract.py: $_"
    if (-not (Test-Path $extractDest)) { throw "lifelog_extract.py not found" }
    Write-Output "[$computer] Using existing lifelog_extract.py"
}

Write-Output "[$computer] Starting extraction (all episodes — cursor will be set after this run)..."
& $python $extractDest
Write-Output "[$computer] v50 complete."
