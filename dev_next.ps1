# Dump lifelog config ntfy_ui_topic
$configPath = "C:\ProgramData\LifeLog\lifelog_config.json"
if (Test-Path $configPath) {
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    Write-Output "ntfy_ui_topic in config: '$($cfg.ntfy_ui_topic)'"
    Write-Output "house in config: '$($cfg.house)'"
    Write-Output "Full config:"
    Get-Content $configPath -Raw
} else {
    Write-Output "Config not found at $configPath"
}
