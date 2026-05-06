$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$computer = $env:COMPUTERNAME
$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

$pythonExe = $null
try {
    $wherePython = where.exe python 2>$null
    if ($wherePython) {
        $candidates = $wherePython -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
        foreach ($candidate in $candidates) {
            if ($candidate -notlike '*WindowsApps*') { $pythonExe = $candidate; break }
        }
        if (-not $pythonExe) { $pythonExe = $candidates[0] }
    }
} catch {}
if (-not $pythonExe) {
    foreach ($p in @(
        "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    )) { if (Test-Path $p) { $pythonExe = $p; break } }
}

$script = @'
import os, sys, struct, tempfile, subprocess

print(f"v19 | Python: {sys.executable}")
print(f"USERPROFILE: {os.environ.get('USERPROFILE', 'N/A')}")
print()

# Install deps
for pkg in ['iphone_backup_decrypt', 'blackboxprotobuf']:
    try:
        __import__(pkg.replace('-','_'))
    except ImportError:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', pkg], capture_output=True)

from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike
import blackboxprotobuf

# Find backup
def find_backup():
    userprofile = os.environ.get('USERPROFILE', '')
    candidates = [
        os.path.join(userprofile, 'Apple', 'MobileSync', 'Backup'),
        os.path.join(userprofile, 'AppData', 'Roaming', 'Apple Computer', 'MobileSync', 'Backup'),
    ]
    for base in candidates:
        if os.path.exists(base):
            for item in os.listdir(base):
                full = os.path.join(base, item)
                if os.path.isdir(full) and os.path.exists(os.path.join(full, 'Manifest.db')):
                    return full
    return None

backup_path = find_backup()
if not backup_path:
    print("No backup found")
    sys.exit()

print(f"Using backup: {backup_path}")
backup = EncryptedBackup(backup_directory=backup_path, passphrase='#ngrierBill70')

# First unlock via podcasts
tmp = tempfile.mkdtemp()
podcasts_out = os.path.join(tmp, 'MTLibrary.sqlite')
try:
    backup.extract_file(
        relative_name='MTLibrary.sqlite',
        output_filename=podcasts_out,
        domain_like='AppDomainGroup-%',
    )
    print(f"Podcasts DB extracted: {os.path.getsize(podcasts_out)} bytes")
except Exception as e:
    print(f"Podcasts extract: {e}")

# Extract tlogs_offline
tlogs_out = os.path.join(tmp, 'tlogs_offline')
try:
    backup.extract_file(
        relative_name='tlogs_offline',
        output_filename=tlogs_out,
        domain_like='AppDomainGroup-%',
    )
    size = os.path.getsize(tlogs_out)
    print(f"tlogs_offline: {size} bytes")
except Exception as e:
    print(f"tlogs extract error: {e}")
    sys.exit()

# Read raw bytes
with open(tlogs_out, 'rb') as f:
    raw = f.read()

print(f"\nFile size: {len(raw)} bytes")
print(f"\n=== HEX DUMP (first 256 bytes) ===")
for i in range(0, min(256, len(raw)), 16):
    chunk = raw[i:i+16]
    hex_str = ' '.join(f'{b:02x}' for b in chunk)
    ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
    print(f"  {i:04x}: {hex_str:<48}  {ascii_str}")

print(f"\n=== SEARCH FOR COORDINATE PATTERNS ===")

# Seattle area bounds
# Lat: 47.0 - 48.5, Lon: -123.0 - -121.5
# Bellevue/Mercer Island area too

def search_float32(data, lat_min, lat_max, lon_min, lon_max):
    hits = []
    for i in range(0, len(data) - 3):
        for endian in ['<', '>']:
            v = struct.unpack_from(f'{endian}f', data, i)[0]
            if lat_min <= v <= lat_max:
                hits.append((i, 'lat_f32', endian, v))
            if lon_min <= v <= lon_max:
                hits.append((i, 'lon_f32', endian, v))
    return hits

def search_float64(data, lat_min, lat_max, lon_min, lon_max):
    hits = []
    for i in range(0, len(data) - 7):
        for endian in ['<', '>']:
            v = struct.unpack_from(f'{endian}d', data, i)[0]
            if lat_min <= v <= lat_max:
                hits.append((i, 'lat_f64', endian, v))
            if lon_min <= v <= lon_max:
                hits.append((i, 'lon_f64', endian, v))
    return hits

def search_int32_e7(data, lat_min, lat_max, lon_min, lon_max):
    hits = []
    lat_min_i, lat_max_i = int(lat_min * 1e7), int(lat_max * 1e7)
    lon_min_i, lon_max_i = int(lon_min * 1e7), int(lon_max * 1e7)
    for i in range(0, len(data) - 3):
        for endian in ['<', '>']:
            v = struct.unpack_from(f'{endian}i', data, i)[0]
            if lat_min_i <= v <= lat_max_i:
                hits.append((i, f'lat_e7_{endian}', v, v/1e7))
            if lon_min_i <= v <= lon_max_i:
                hits.append((i, f'lon_e7_{endian}', v, v/1e7))
    return hits

print("Searching for Seattle-area coordinates (lat 47.0-48.5, lon -123.0 to -121.5)...")

f32_hits = search_float32(raw, 47.0, 48.5, -123.0, -121.5)
print(f"  float32 hits: {len(f32_hits)}")
for hit in f32_hits[:10]:
    print(f"    offset={hit[0]:#06x} {hit[1]} {hit[2]}: {hit[3]:.6f}")

f64_hits = search_float64(raw, 47.0, 48.5, -123.0, -121.5)
print(f"  float64 hits: {len(f64_hits)}")
for hit in f64_hits[:10]:
    print(f"    offset={hit[0]:#06x} {hit[1]} {hit[2]}: {hit[3]:.8f}")

i32_hits = search_int32_e7(raw, 47.0, 48.5, -123.0, -121.5)
print(f"  int32 E7 hits: {len(i32_hits)}")
for hit in i32_hits[:10]:
    print(f"    offset={hit[0]:#06x} {hit[1]}: raw={hit[2]} -> {hit[3]:.6f}")

# Also search broader: anywhere in Washington State
print("\nSearching broader: Washington State (lat 45.5-49.0, lon -124.5 to -116.9)...")
f64_hits2 = search_float64(raw, 45.5, 49.0, -124.5, -116.9)
print(f"  float64 hits: {len(f64_hits2)}")
for hit in f64_hits2[:20]:
    print(f"    offset={hit[0]:#06x} {hit[1]} {hit[2]}: {hit[3]:.8f}")

i32_hits2 = search_int32_e7(raw, 45.5, 49.0, -124.5, -116.9)
print(f"  int32 E7 hits: {len(i32_hits2)}")
for hit in i32_hits2[:20]:
    print(f"    offset={hit[0]:#06x} {hit[1]}: raw={hit[2]} -> {hit[3]:.6f}")

# Also try: search for any recent Unix timestamps in the file
print(f"\n=== SEARCH FOR RECENT TIMESTAMPS ===")
# Recent = 2020-2026: Unix 1577836800 to 1800000000
ts_min, ts_max = 1577836800, 1800000000
ts_hits = []
for i in range(0, len(raw) - 3):
    for endian in ['<', '>']:
        v = struct.unpack_from(f'{endian}I', raw, i)[0]  # unsigned 32-bit
        if ts_min <= v <= ts_max:
            import datetime
            dt = datetime.datetime.utcfromtimestamp(v)
            ts_hits.append((i, endian, v, str(dt)))

print(f"Unix timestamp hits (2020-2026): {len(ts_hits)}")
for hit in ts_hits[:20]:
    print(f"  offset={hit[0]:#06x} {hit[1]}: {hit[2]} = {hit[3]}")

# Try millisecond timestamps
ts_min_ms, ts_max_ms = ts_min * 1000, ts_max * 1000
ms_hits = []
for i in range(0, len(raw) - 7):
    for endian in ['<', '>']:
        v = struct.unpack_from(f'{endian}Q', raw, i)[0]  # unsigned 64-bit
        if ts_min_ms <= v <= ts_max_ms:
            import datetime
            dt = datetime.datetime.utcfromtimestamp(v/1000)
            ms_hits.append((i, endian, v, str(dt)))

print(f"Millisecond timestamp hits: {len(ms_hits)}")
for hit in ms_hits[:20]:
    print(f"  offset={hit[0]:#06x} {hit[1]}: {hit[2]} = {hit[3]}")

# Also show full protobuf decode (all fields)
print(f"\n=== FULL PROTOBUF DECODE (truncated) ===")
try:
    msg, typedef = blackboxprotobuf.decode_message(raw)
    def show(d, prefix='', depth=0):
        if depth > 6:
            print(f"{prefix}...")
            return
        if isinstance(d, dict):
            for k, v in list(d.items())[:20]:
                if isinstance(v, (dict, list)):
                    print(f"{prefix}{k}:")
                    show(v, prefix + '  ', depth+1)
                elif isinstance(v, bytes) and len(v) > 4:
                    print(f"{prefix}{k}: bytes[{len(v)}] = {v[:16].hex()}...")
                else:
                    print(f"{prefix}{k}: {repr(v)[:80]}")
        elif isinstance(d, list):
            for i, v in enumerate(d[:5]):
                print(f"{prefix}[{i}]:")
                show(v, prefix + '  ', depth+1)
            if len(d) > 5:
                print(f"{prefix}... ({len(d)-5} more)")
    show(msg)
except Exception as e:
    print(f"Decode error: {e}")

print("\nDone.")
'@

if (-not $pythonExe) {
    $output = "v19 | ERROR: Python not found"
} else {
    $output = $script | & $pythonExe - 2>&1 | Out-String
    $output = "v19 | Python: $pythonExe`n" + $output
}

$body = @{ output = $output; timestamp = $timestamp; computer = $computer; source = 'LifeLog-DevLoop' } | ConvertTo-Json -Depth 3
Invoke-RestMethod -Uri $webhookUrl -Method POST -Body $body -ContentType 'application/json' | Out-Null
Write-Host "[$timestamp] Sent output from $computer"
