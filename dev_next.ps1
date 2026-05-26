# Fix: Add ntfy_ui_topic to config file directly
$configPath = "C:\ProgramData\LifeLog\lifelog_config.json"
$cfg = Get-Content $configPath -Raw | ConvertFrom-Json

# Add ntfy_ui_topic if missing
if (-not $cfg.ntfy_ui_topic) {
    $cfg | Add-Member -NotePropertyName 'ntfy_ui_topic' -NotePropertyValue 'lifelog-ui-caphill-771b06' -Force
    $cfg | ConvertTo-Json -Depth 5 | Set-Content $configPath -Encoding UTF8
    Write-Output "FIXED: Added ntfy_ui_topic='lifelog-ui-caphill-771b06' to config"
    Write-Output "Config now:"
    Get-Content $configPath -Raw
} else {
    Write-Output "ntfy_ui_topic already set: '$($cfg.ntfy_ui_topic)'"
}
