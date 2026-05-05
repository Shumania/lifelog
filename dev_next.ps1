# Self-contained: downloads inspect script to temp and runs it
$ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$tmpScript = Join-Path $env:TEMP "inspect_googlemaps_backup.py"
$scriptUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/inspect_googlemaps_backup.py?t=$ts"

Write-Output "Downloading inspect script to $tmpScript ..."
Invoke-WebRequest -Uri $scriptUrl -OutFile $tmpScript -UseBasicParsing
Write-Output "Running inspect script..."
& python $tmpScript 2>&1
