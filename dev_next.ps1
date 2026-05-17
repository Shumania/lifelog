# v62 - service diagnostics
$svc = "C:\ProgramData\LifeLog\lifelog_service.py"
$ver = if (Test-Path $svc) { (Select-String "SERVICE_VERSION\s*=\s*" $svc | Select-Object -First 1).Line.Trim() } else { "FILE NOT FOUND" }
$running = Get-Process python* -ErrorAction SilentlyContinue | Select-Object -ExpandProperty CommandLine -ErrorAction SilentlyContinue
$startScript = if (Test-Path "C:\ProgramData\LifeLog\Start-LifeLog.ps1") { "EXISTS" } else { "MISSING" }
$files = Get-ChildItem "C:\ProgramData\LifeLog\" -ErrorAction SilentlyContinue | Select-Object Name, LastWriteTime | Out-String
"SERVICE_VERSION_LINE: $ver`nPYTHON_PROCS: $running`nStart-LifeLog.ps1: $startScript`nFILES:`n$files"
