# Run podcast extraction from iPhone backup
# Find Python - use where.exe, skip WindowsApps stub
$pythonExe = $null
$candidates = @()
try { $candidates = @(where.exe python 2>$null) } catch {}
foreach ($p in $candidates) {
    if ($p -notmatch "WindowsApps") {
        $pythonExe = $p
        break
    }
}
if (-not $pythonExe) { $pythonExe = "python" }

Write-Host "v23 | Python: $pythonExe"
Write-Host "Machine: $env:COMPUTERNAME"

# Download latest lifelog_extract.py
$scriptUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?t=$(Get-Date -UFormat %s)"
$scriptPath = "$env:TEMP\lifelog_extract.py"
Invoke-WebRequest -Uri $scriptUrl -OutFile $scriptPath -UseBasicParsing

Write-Host "Downloaded lifelog_extract.py ($(Get-Item $scriptPath).Length bytes)"

# Run extraction - output goes to stdout so dev loop captures it
& $pythonExe $scriptPath 2>&1
