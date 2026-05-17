# v66 - log tail + versions.json check
Write-Output "=== LAST 60 LOG LINES ==="
Get-Content "C:\ProgramData\LifeLog\lifelog.log" -Tail 60 -ErrorAction SilentlyContinue
Write-Output "=== versions.json from GitHub API ==="
try {
    $r = Invoke-RestMethod "https://api.github.com/repos/Shumania/lifelog/contents/versions.json"
    $content = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($r.content))
    Write-Output $content
} catch { Write-Output "ERROR: $_" }
Write-Output "=== SERVICE FILE VERSION LINE ==="
Select-String "SERVICE_VERSION" "C:\ProgramData\LifeLog\lifelog_service.py" -ErrorAction SilentlyContinue | Select-Object -First 3
