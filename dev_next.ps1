# dev_next: 2026-08-19 enable backup module on Mind + install lifelog_extract.py (Mind only)
if ($env:COMPUTERNAME -notlike "*mind*") { exit 0 }
$ErrorActionPreference = "Continue"
$dir = "C:\ProgramData\LifeLog"
$cfgPath = Join-Path $dir "lifelog_config.json"
Write-Output ("=== enable backup module ({0}) ===" -f $env:COMPUTERNAME)

# --- Step 1: config change ---
if (-not (Test-Path $cfgPath)) {
  Write-Output "FAIL: config not found at $cfgPath -- aborting (no changes made)"
  exit 0
}
$raw = Get-Content $cfgPath -Raw
Write-Output "--- config BEFORE ---"
Write-Output $raw.Trim()
try {
  $cfg = $raw | ConvertFrom-Json
} catch {
  Write-Output ("FAIL: config JSON parse error: {0} -- aborting (no changes made)" -f $_.Exception.Message)
  exit 0
}
$mods = @($cfg.modules)
if ($mods.Count -eq 0) { Write-Output "note: no modules key found; service default applies -- will write explicit list" ; $mods = @("sonos","dev") }
if ($mods -contains "backup") {
  Write-Output "modules already contain 'backup' -- config unchanged"
} else {
  $mods = $mods + "backup"
  $cfg | Add-Member -NotePropertyName modules -NotePropertyValue $mods -Force
  $json = $cfg | ConvertTo-Json -Depth 10
  Copy-Item $cfgPath ($cfgPath + ".bak_20260819") -Force
  [System.IO.File]::WriteAllText($cfgPath, $json, (New-Object System.Text.UTF8Encoding($false)))
  Write-Output "backup added to modules; backup copy saved as lifelog_config.json.bak_20260819"
}
Write-Output "--- config AFTER ---"
Write-Output ((Get-Content $cfgPath -Raw).Trim())

# --- Step 2: install lifelog_extract.py (fresh from repo; cursor/state files untouched) ---
$extPath = Join-Path $dir "lifelog_extract.py"
$before = "NOT PRESENT"
if (Test-Path $extPath) { $fi = Get-Item $extPath; $before = ("{0}b modified {1:u}" -f $fi.Length, $fi.LastWriteTimeUtc) }
Write-Output ("extract before: {0}" -f $before)
try {
  $url = "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py"
  Invoke-WebRequest -Uri $url -OutFile $extPath -UseBasicParsing -TimeoutSec 60
  $fi = Get-Item $extPath
  Write-Output ("extract after: {0}b modified {1:u}" -f $fi.Length, $fi.LastWriteTimeUtc)
  $verLine = Select-String -Path $extPath -Pattern "EXTRACTOR_VERSION" | Select-Object -First 1
  Write-Output ("version line: {0}" -f $verLine.Line.Trim())
  $whLine = Select-String -Path $extPath -Pattern "^WEBHOOK_URL" | Select-Object -First 1
  Write-Output ("webhook line: {0}" -f $whLine.Line.Trim())
} catch {
  Write-Output ("FAIL: extract download error: {0}" -f $_.Exception.Message)
}

# --- Step 3: state files inventory (should be untouched / may not exist yet) ---
Write-Output "=== state/cursor files in $dir (untouched by this script) ==="
Get-ChildItem $dir -Include "*cursor*","*state*","*extract*" -Recurse -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ("{0}  {1}b  {2:u}" -f $_.Name, $_.Length, $_.LastWriteTimeUtc) }
Write-Output "=== done -- restart service to activate backup thread ==="
