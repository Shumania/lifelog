$script = @'
import sys, os, tempfile, shutil, traceback, sqlite3, plistlib, json

print(f"v17 | Python: {sys.executable}")
print(f"USERPROFILE: {os.environ.get('USERPROFILE','<not set>')}")

up = os.environ.get("USERPROFILE", "")
candidates = [
    os.path.join(up, "Apple", "MobileSync", "Backup"),
    os.path.join(up, "AppData", "Roaming", "Apple Computer", "MobileSync", "Backup"),
    os.path.join(up, "AppData", "Roaming", "Apple", "MobileSync", "Backup"),
    os.path.join(up, "AppData", "Local", "Apple Computer", "MobileSync", "Backup"),
    os.path.join(up, "AppData", "Local", "Apple", "MobileSync", "Backup"),
    r"C:\ProgramData\Apple Computer\MobileSync\Backup",
    r"C:\ProgramData\Apple\MobileSync\Backup",
]

print("\n--- Searching for backups ---")
backup_path = None
for base in candidates:
    exists = os.path.isdir(base)
    print(f"  {'[EXISTS]' if exists else '[missing]'} {base}")
    if exists:
        try:
            entries = os.listdir(base)
            print(f"    Contents ({len(entries)} items): {entries[:10]}")
            for d in entries:
                full = os.path.join(base, d)
                has_manifest = os.path.isfile(os.path.join(full, "Manifest.db"))
                is_dir = os.path.isdir(full)
                print(f"      {d}: isdir={is_dir}, has_Manifest.db={has_manifest}")
                if has_manifest and not backup_path:
                    print(f"    -> Using backup: {d}")
                    backup_path = full
        except Exception as e:
            print(f"    -> listdir error: {e}")

if not backup_path:
    print("\nNo backup found in candidates. Trying deep walk...")
    for root, dirs, files in os.walk(up, topdown=True):
        depth = root[len(up):].count(os.sep)
        if depth > 7:
            dirs[:] = []
            continue
        if "Manifest.db" in files:
            print(f"  Walk found: {root}")
            if not backup_path:
                backup_path = root

if not backup_path:
    print("ERROR: No backup found anywhere")
    sys.exit(1)

print(f"\nUsing backup: {backup_path}")

try:
    from iphone_backup_decrypt import EncryptedBackup, RelativePath
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "iphone-backup-decrypt", "--quiet"])
    from iphone_backup_decrypt import EncryptedBackup, RelativePath

try:
    import blackboxprotobuf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "blackboxprotobuf", "--quiet"])
    import blackboxprotobuf

PASSPHRASE = "#ngrierBill70"
tmpdir = tempfile.mkdtemp()

try:
    backup = EncryptedBackup(backup_directory=backup_path, passphrase=PASSPHRASE)
    try:
        backup.extract_file(
            relative_path="Library/Application Support/MTLibrary.sqlite",
            output_filename=os.path.join(tmpdir, "MTLibrary.sqlite")
        )
        print("Unlock OK")
    except Exception as e:
        print(f"Unlock attempt: {e}")

    proto_path = os.path.join(tmpdir, "tlogs_offline")
    try:
        backup.extract_file(
            relative_path="Library/Application Support/tlogs_offline_storage.binaryproto",
            output_filename=proto_path
        )
    except Exception as e:
        print(f"tlogs extract error: {e}")
        sys.exit(1)

    if not os.path.exists(proto_path):
        print("tlogs file not found in backup")
        sys.exit(1)

    with open(proto_path, 'rb') as f:
        raw = f.read()
    print(f"tlogs size: {len(raw)} bytes")

    msg, typedef = blackboxprotobuf.decode_message(raw)

    def flatten(obj, path=""):
        results = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                results.extend(flatten(v, f"{path}.{k}"))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                results.extend(flatten(item, f"{path}[{i}]"))
        elif isinstance(obj, bytes):
            try:
                inner, _ = blackboxprotobuf.decode_message(obj)
                results.extend(flatten(inner, f"{path}(bytes->proto)"))
            except:
                pass
            try:
                s = obj.decode('utf-8')
                results.append((path, 'str', s[:100]))
            except:
                results.append((path, 'bytes', obj[:16].hex()))
        elif isinstance(obj, int):
            results.append((path, 'int', obj))
        elif isinstance(obj, float):
            results.append((path, 'float', obj))
        else:
            results.append((path, type(obj).__name__, str(obj)[:100]))
        return results

    leaves = flatten(msg)

    latlons = []
    timestamps = []
    for path, typ, val in leaves:
        if typ == 'int':
            if 1000000 < abs(val) < 1800000000:
                latlons.append((path, val, val/1e7))
            elif 1000000000 < val < 9999999999:
                timestamps.append((path, val))
            elif 1000000000000 < val < 9999999999999:
                timestamps.append((path, val, 'ms'))
        elif typ == 'float':
            if -180 <= val <= 180:
                latlons.append((path, val, 'float'))

    print("\n=== CANDIDATE LAT/LON VALUES ===")
    for item in latlons[:30]:
        print(item)

    print("\n=== CANDIDATE TIMESTAMPS ===")
    for item in timestamps[:20]:
        print(item)

    print("\n=== STRUCTURE (top level) ===")
    if isinstance(msg, dict):
        for k, v in list(msg.items())[:5]:
            print(f"  field_{k}: {type(v).__name__} len={len(v) if hasattr(v,'__len__') else 'n/a'}")
            if isinstance(v, dict):
                for k2, v2 in list(v.items())[:5]:
                    print(f"    field_{k2}: {type(v2).__name__} len={len(v2) if hasattr(v2,'__len__') else 'n/a'}")
                    if isinstance(v2, dict):
                        for k3, v3 in list(v2.items())[:3]:
                            print(f"      field_{k3}: {type(v3).__name__} val={str(v3)[:80] if not isinstance(v3,list) else f'list({len(v3)})'}")
                            if isinstance(v3, list) and len(v3) > 0:
                                item0 = v3[0]
                                if isinstance(item0, dict):
                                    for k4, v4 in list(item0.items())[:8]:
                                        print(f"        [0].field_{k4}: {type(v4).__name__} val={str(v4)[:80] if not isinstance(v4,(list,dict,bytes)) else str(type(v4).__name__)+'('+str(len(v4))+')'}")

except Exception as e:
    print(f"FATAL: {e}")
    traceback.print_exc()
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\nDone.")
'@

$pythonExe = $null
$candidates = @()
try { $candidates = @(where.exe python 2>$null) } catch {}
foreach ($p in $candidates) {
    if ($p -notmatch "WindowsApps") {
        $pythonExe = $p
        break
    }
}
if (-not $pythonExe) { $pythonExe = "python" }

$script | & $pythonExe - 2>&1
