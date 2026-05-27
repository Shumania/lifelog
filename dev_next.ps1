$cfg = Get-Content 'C:\ProgramData\LifeLog\lifelog_config.json' | ConvertFrom-Json
$token = $cfg.github_token
Write-Output "PAT:$token"
