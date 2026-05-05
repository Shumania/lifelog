# dev_next.ps1 - controlled by Tasklet agent
# v6: run inspect_googlemaps_backup.py (mirrors lifelog_extract.py decryption)

$installDir = "C:\ProgramData\LifeLog"

# Always download fresh copy
$ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/inspect_googlemaps_backup.py?t=$ts" `
    -OutFile "$installDir\inspect_googlemaps_backup.py" -UseBasicParsing

# Run it (output goes to webhook automatically via script)
python "$installDir\inspect_googlemaps_backup.py"
