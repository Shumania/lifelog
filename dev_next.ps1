# dev_next: 2026-08-19 verify extractor v2.9 self-update (Shumaframe only)
if ($env:COMPUTERNAME -notlike "*shumaframe*") { exit 0 }
$dir = "C:\ProgramData\LifeLog"
$f = Join-Path $dir "lifelog_extract.py"
Write-Output "=== extractor verification ($env:COMPUTERNAME) ==="
if (Test-Path $f) {
  $fi = Get-Item $f
  Write-Output ("file: {0} bytes | modified {1:u}" -f $fi.Length, $fi.LastWriteTimeUtc)
  Write-Output "--- version-ish lines (first 8) ---"
  Select-String -Path $f -Pattern "version" | Select-Object -First 8 | ForEach-Object { Write-Output ("L{0}: {1}" -f $_.LineNumber, $_.Line.Trim()) }
  Write-Output "--- webhook target lines ---"
  Select-String -Path $f -Pattern "webhooks.tasklet.ai" | Select-Object -First 4 | ForEach-Object { Write-Output ("L{0}: {1}" -f $_.LineNumber, $_.Line.Trim()) }
} else {
  Write-Output "file: NOT FOUND at $f"
}
Write-Output "=== extract-related files in $dir ==="
Get-ChildItem $dir -Filter "*extract*" -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ("{0}  {1}b  {2:u}" -f $_.Name, $_.Length, $_.LastWriteTimeUtc) }
