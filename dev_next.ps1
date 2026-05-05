# dev_next.ps1 - controlled by Tasklet agent
# Current task: run inspect_googlemaps_backup.py and return output

$installDir = "C:\ProgramData\LifeLog"

# Download latest inspect script
$ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$inspectUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/inspect_googlemaps_backup.py?t=$ts"
Invoke-WebRequest -Uri $inspectUrl -OutFile "$installDir\inspect_googlemaps_backup.py" -UseBasicParsing

# Run it and capture output
$output = & python "$installDir\inspect_googlemaps_backup.py" 2>&1 | Out-String

Write-Output $output
