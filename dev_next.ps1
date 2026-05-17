# v61 - read log tail
Get-Content "C:\ProgramData\LifeLog\lifelog.log" -Tail 80 -ErrorAction SilentlyContinue
if (-not (Test-Path "C:\ProgramData\LifeLog\lifelog.log")) { "LOG FILE NOT FOUND" }
Get-Content "C:\ProgramData\LifeLog\lifelog_service.py" -TotalCount 5 -ErrorAction SilentlyContinue
