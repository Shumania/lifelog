# diag-garage-v1: read-only Garage (192.168.4.54) AVTransport/Queue timing diagnostics
$ip = "192.168.4.54"

function Soap($path, $service, $action, $body) {
  $url = "http://${ip}:1400$path"
  $envelope = '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:' + $action + ' xmlns:u="urn:schemas-upnp-org:service:' + $service + ':1">' + $body + '</u:' + $action + '></s:Body></s:Envelope>'
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    $r = Invoke-WebRequest -Uri $url -Method Post -Body $envelope -ContentType 'text/xml; charset=utf-8' -Headers @{ SOAPACTION = ('"urn:schemas-upnp-org:service:' + $service + ':1#' + $action + '"') } -TimeoutSec 12 -UseBasicParsing
    $sw.Stop()
    return @{ Timing = ("TIMING {0}#{1} -> HTTP {2} in {3} ms" -f $service, $action, [int]$r.StatusCode, $sw.ElapsedMilliseconds); Content = [string]$r.Content }
  } catch {
    $sw.Stop()
    return @{ Timing = ("TIMING {0}#{1} -> FAILED in {2} ms : {3}" -f $service, $action, $sw.ElapsedMilliseconds, $_.Exception.Message); Content = $null }
  }
}

Write-Output "=== diag-garage-v1 (read-only) $(Get-Date -Format o) ==="

# 1. Transport state (lightweight AVTransport call - the same interface that timed out)
$t = Soap "/MediaRenderer/AVTransport/Control" "AVTransport" "GetTransportInfo" "<InstanceID>0</InstanceID>"
Write-Output $t.Timing
if ($t.Content -match '<CurrentTransportState>([^<]*)</CurrentTransportState>') { Write-Output ("  TransportState: " + $Matches[1]) }

# 2. Media info: what URI is the transport bound to + queue track count
$m = Soap "/MediaRenderer/AVTransport/Control" "AVTransport" "GetMediaInfo" "<InstanceID>0</InstanceID>"
Write-Output $m.Timing
if ($m.Content -match '<NrTracks>(\d+)</NrTracks>') { Write-Output ("  NrTracks: " + $Matches[1]) }
if ($m.Content -match '<CurrentURI>([^<]*)</CurrentURI>') { Write-Output ("  CurrentURI: " + $Matches[1]) }

# 3. Position info (second lightweight AVTransport call for timing consistency)
$p = Soap "/MediaRenderer/AVTransport/Control" "AVTransport" "GetPositionInfo" "<InstanceID>0</InstanceID>"
Write-Output $p.Timing
if ($p.Content -match '<Track>(\d+)</Track>') { Write-Output ("  TrackNo: " + $Matches[1]) }

# 4. Browse the Sonos queue (ContentDirectory, read-only): did the playlist inserts land?
$q = Soap "/MediaServer/ContentDirectory/Control" "ContentDirectory" "Browse" "<ObjectID>Q:0</ObjectID><BrowseFlag>BrowseDirectChildren</BrowseFlag><Filter>dc:title</Filter><StartingIndex>0</StartingIndex><RequestedCount>15</RequestedCount><SortCriteria></SortCriteria>"
Write-Output $q.Timing
if ($q.Content) {
  if ($q.Content -match '<TotalMatches>(\d+)</TotalMatches>') { Write-Output ("  QueueTotal: " + $Matches[1]) }
  $decoded = [System.Net.WebUtility]::HtmlDecode($q.Content)
  $titles = [regex]::Matches($decoded, '<dc:title>([^<]*)</dc:title>') | ForEach-Object { $_.Groups[1].Value }
  $i = 1
  foreach ($ti in $titles) { Write-Output ("  Q[$i]: $ti"); $i++ }
}

# 5. Repeat GetTransportInfo 3x to measure jitter on the AVTransport interface
foreach ($n in 1..3) {
  $j = Soap "/MediaRenderer/AVTransport/Control" "AVTransport" "GetTransportInfo" "<InstanceID>0</InstanceID>"
  Write-Output $j.Timing
}
Write-Output "=== diag-garage-v1 done ==="
