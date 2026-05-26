# Check last 50 lines of service log for SSE-related entries
Get-Content "C:\ProgramData\LifeLog\lifelog_service.log" -Tail 50 | Select-String -Pattern 'SSE|ntfy_ui|publish_ui|Sonos loop error|Transport states' | Select-Object -Last 20
Write-Output "---"
Write-Output "Config ntfy_ui_topic:"
(Get-Content 'C:\ProgramData\LifeLog\lifelog_config.json' | ConvertFrom-Json).ntfy_ui_topic
