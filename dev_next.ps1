$cfg = Get-Content 'C:\ProgramData\LifeLog\lifelog_config.json' | ConvertFrom-Json
Write-Output "GITHUB_PAT=$($cfg.github_token)"
