# One-shot: download v1.45 then clear this script
$dest = 'C:\ProgramData\LifeLog\lifelog_service.py'
$marker = 'C:\ProgramData\LifeLog\v145_downloaded.txt'
if (Test-Path $marker) { exit 0 }
$url = 'https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_service.py'
Write-Host "Downloading v1.45 directly..."
try {
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    'done' | Set-Content $marker
    Write-Host "Downloaded v1.45. Service will pick it up on next self-update cycle."
} catch {
    Write-Host "Download failed: $_"
}
