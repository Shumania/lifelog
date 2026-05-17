# v62 - show versions.json + log tail
$ver = (irm "https://api.github.com/repos/Shumania/lifelog/contents/versions.json") | ForEach-Object { [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_.content)) }
Write-Output "=== versions.json from GitHub API ==="
Write-Output $ver
Write-Output "=== log tail (last 60 lines) ==="
Get-Content "C:\ProgramData\LifeLog\lifelog.log" -Tail 60 | Write-Output
