# dev_next.ps1 v31 - install Python if needed, run extraction (no exit 1)
$computer = $env:COMPUTERNAME
$lifelogDir = "C:\ProgramData\LifeLog"
$pythonDir = "$lifelogDir\python"

Write-Output "[$computer] dev_next.ps1 v31"

# Find existing real Python (skip WindowsApps stub)
$python = $null
$candidates = @(
    "C:\Python313\python.exe","C:\Python312\python.exe","C:\Python311\python.exe",
    "C:\Python310\python.exe","C:\Python39\python.exe","C:\Python38\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "$pythonDir\python.exe"
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $python = $c; break }
}
if (-not $python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found -and $found.Source -notlike "*WindowsApps*") { $python = $found.Source }
}

# Install embeddable Python 3.12 if still not found
if (-not $python) {
    Write-Output "[$computer] Python not found. Installing embeddable Python 3.12..."
    $zipUrl = "https://www.python.org/ftp/python/3.12.9/python-3.12.9-embed-amd64.zip"
    $zipPath = "$env:TEMP\python-embed.zip"
    New-Item -ItemType Directory -Force -Path $pythonDir | Out-Null
    Write-Output "[$computer] Downloading Python embeddable zip..."
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
    Write-Output "[$computer] Extracting..."
    Expand-Archive -Path $zipPath -DestinationPath $pythonDir -Force
    # Enable site-packages
    $pth = Get-ChildItem $pythonDir -Filter "python*._pth" | Select-Object -First 1
    if ($pth) {
        $content = Get-Content $pth.FullName -Raw
        $content = $content -replace "#import site", "import site"
        Set-Content $pth.FullName $content
    }
    # Install pip
    Write-Output "[$computer] Installing pip..."
    $getPipPath = "$env:TEMP\get-pip.py"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPipPath
    & "$pythonDir\python.exe" $getPipPath --no-warn-script-location 2>&1
    $python = "$pythonDir\python.exe"
    Write-Output "[$computer] Python installed at $python"
}

Write-Output "[$computer] Using Python: $python"

# Install iphone_backup_decrypt if needed
$testImport = & $python -c "import iphone_backup_decrypt; print('ok')" 2>&1
if ($testImport -notmatch "ok") {
    Write-Output "[$computer] Installing iphone_backup_decrypt..."
    & $python -m pip install --quiet iphone_backup_decrypt 2>&1
}

# Download latest lifelog_extract.py
$ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$script = "$lifelogDir\lifelog_extract.py"
Write-Output "[$computer] Downloading latest lifelog_extract.py..."
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?t=$ts" -OutFile $script
$lines = (Get-Content $script).Count
Write-Output "[$computer] Downloaded OK ($lines lines)."

# Run extraction
Write-Output "[$computer] Running extraction..."
& $python $script 2>&1
Write-Output "[$computer] Done."
