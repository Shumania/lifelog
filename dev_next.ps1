# powercfg-shumaframe-v1: kill AC standby/hibernate timers (runbook fix 2026-08-06)
if ($env:COMPUTERNAME -ne 'SHUMAFRAME') { exit 0 }  # wrong machine: silent no-op (empty output = no webhook post)

Write-Output "COMPUTER: $env:COMPUTERNAME"
Write-Output "SCRIPT: powercfg-shumaframe-v1"
try {
    powercfg /change standby-timeout-ac 0
    Write-Output "standby-timeout-ac 0 -> exit $LASTEXITCODE"
    powercfg /change hibernate-timeout-ac 0
    Write-Output "hibernate-timeout-ac 0 -> exit $LASTEXITCODE"
    powercfg /change standby-timeout-dc 0
    Write-Output "standby-timeout-dc 0 -> exit $LASTEXITCODE"
    powercfg /change hibernate-timeout-dc 0
    Write-Output "hibernate-timeout-dc 0 -> exit $LASTEXITCODE"
} catch {
    Write-Output "POWERCFG CHANGE FAILED: $_"
}
Write-Output "== VERIFY: current scheme sleep values (raw) =="
try {
    powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Out-String | Write-Output
    powercfg /query SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE | Out-String | Write-Output
} catch {
    Write-Output "POWERCFG QUERY FAILED: $_"
}
Write-Output "== DONE powercfg-shumaframe-v1 =="
