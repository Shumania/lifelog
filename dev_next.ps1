# Diagnostic: check rollback state and test v1.45 import
$base = 'C:\ProgramData\LifeLog'
$svc = Join-Path $base 'lifelog_service.py'

Write-Output "=== SERVICE VERSION ==="
Select-String -Path $svc -Pattern 'SERVICE_VERSION' | Select-Object -First 1

Write-Output "`n=== ROLLBACK FILES ==="
Get-ChildItem $base -Filter 'lifelog_service*' | ForEach-Object { "$($_.Name) ($($_.Length) bytes, $($_.LastWriteTime))" }
Get-ChildItem $base -Filter 'update_*' | ForEach-Object { "$($_.Name) ($($_.Length) bytes, $($_.LastWriteTime))" }

Write-Output "`n=== RECENT LOG LINES ==="
$logDir = Join-Path $base 'logs'
if (Test-Path $logDir) {
    $latest = Get-ChildItem $logDir -Filter '*.log' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) {
        Write-Output "Log: $($latest.Name)"
        Get-Content $latest.FullName -Tail 50
    }
} else {
    Write-Output 'No logs dir'
}

Write-Output "`n=== TEST V1.45 IMPORT ==="
try {
    $v45 = Join-Path $env:TEMP 'lifelog_service_v145_test.py'
    # Download v1.45 from GitHub
    $headers = @{}
    $cfgPath = Join-Path $base 'lifelog_config.json'
    if (Test-Path $cfgPath) {
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
        if ($cfg.github_token) { $headers['Authorization'] = "token $($cfg.github_token)" }
    }
    $apiUrl = 'https://api.github.com/repos/Shumania/lifelog/contents/lifelog_service.py'
    $resp = Invoke-RestMethod -Uri $apiUrl -Headers $headers
    $bytes = [System.Convert]::FromBase64String($resp.content)
    [System.IO.File]::WriteAllBytes($v45, $bytes)
    
    # Try syntax check
    $result = & python -c "import py_compile; py_compile.compile(r'$v45', doraise=True)" 2>&1
    Write-Output "Syntax check: OK"
    Write-Output $result
    
    # Try importing (will fail on Windows-specific stuff but shows the error)
    $result2 = & python -c "exec(open(r'$v45', encoding='utf-8').read().split('\nif __name__')[0])" 2>&1 | Select-Object -First 20
    Write-Output "`nImport test output:"
    Write-Output $result2
} catch {
    Write-Output "Test failed: $_"
}
