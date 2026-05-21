# Retrieve the Sonos transport debug dump
$debugPath = "C:\ProgramData\LifeLog\sonos_transport_debug.json"
if (Test-Path $debugPath) {
    Get-Content $debugPath -Raw
} else {
    Write-Output "DEBUG FILE NOT FOUND YET - no track event since v1.30 update"
}
