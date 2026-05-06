$script = @'
import sys, os, tempfile, shutil, traceback, sqlite3, plistlib, json

print(f"v14 | Python: {sys.executable}")

# Enumerate all candidate backup locations
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
    if exists and not backup_path:
        for d in os.listdir(base):
            full = os.path.join(base, d)
            if os.path.isfile(os.path.join(full, "Manifest.db")):
                print(f"    -> Found backup: {d}")
                backup_path = full
                break

if not backup_path:
    # Last resort: walk entire drive looking for Manifest.db under MobileSync
    print("\nDoing deep search for Manifest.db...")
    for root, dirs, files in os.walk(up, topdown=True):
        # Skip deep paths to keep it fast
        depth = root[len(up):].count(os.sep)
        if depth > 6:
            dirs[:] = []
            continue
        if "MobileSync" in root and "Manifest.db" in files:
            parent = os.path.dirname(root)
            if os.path.basename(parent) == "Backup":
                print(f"    -> Found: {root}")
                backup_path = root
                break
    if not backup_path:
        print("ERROR: No backup found anywhere under USERPROFILE")
        sys.exit(1)

print(f"\nUsing backup: {backup_path}")

try:
    from iphone_backup_decrypt import EncryptedBackup, RelativePath
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "iphone-backup-decrypt"])
    from iphone_backup_decrypt import EncryptedBackup, RelativePath

try:
    import blackboxprotobuf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "blackboxprotobuf"])
    import blackboxprotobuf

PASSPHRASE = "#ngrierBill70"
tmpdir = tempfile.mkdtemp()

TARGETS = [
    ("PlacesheetVisits",  "Library/Application Support/PlacesheetVisits",             "plist"),
    ("LocalSuggestions",  "Library/Application Support/LocalSuggestions",              "plist"),
    ("FrequentTripsData", "Library/Application Support/FrequentTripsData",             "plist"),
    ("DirectionsData",    "Library/Application Support/DirectionsData",                "plist"),
    ("tlogs_offline",     "Library/Application Support/tlogs_offline_storage.binaryproto", "proto"),
]

def inspect_plist(path, label):
    try:
        with open(path, 'rb') as f:
            data = plistlib.load(f)
        def summarize(obj, depth=0):
            indent = "  " * depth
            if isinstance(obj, dict):
                print(f"{indent}dict({len(obj)} keys): {list(obj.keys())[:10]}")
                for k, v in list(obj.items())[:5]:
                    print(f"{indent}  {k}:")
                    summarize(v, depth+2)
            elif isinstance(obj, list):
                print(f"{indent}list({len(obj)} items)")
                for item in obj[:3]:
                    summarize(item, depth+2)
            elif isinstance(obj, bytes):
                print(f"{indent}bytes({len(obj)}): {obj[:32].hex()}")
                if obj[:6] == b'bplist':
                    try:
                        inner = plistlib.loads(obj)
                        print(f"{indent}  -> nested plist:")
                        summarize(inner, depth+2)
                    except:
                        pass
                elif len(obj) > 4:
                    try:
                        msg, typedef = blackboxprotobuf.decode_message(obj)
                        print(f"{indent}  -> nested protobuf: {json.dumps(msg, default=str)[:300]}")
                    except:
                        pass
            else:
                print(f"{indent}{type(obj).__name__}: {repr(obj)[:200]}")
        summarize(data)
    except Exception as e:
        print(f"  plist decode error: {e}")

def inspect_proto(path, label):
    with open(path, 'rb') as f:
        raw = f.read()
    print(f"  Size: {len(raw)} bytes")
    try:
        msg, typedef = blackboxprotobuf.decode_message(raw)
        def pp(obj, depth=0, max_depth=4):
            indent = "  " * depth
            if depth > max_depth:
                print(f"{indent}[truncated]")
                return
            if isinstance(obj, dict):
                for k, v in list(obj.items())[:20]:
                    print(f"{indent}field_{k}:")
                    pp(v, depth+1, max_depth)
            elif isinstance(obj, list):
                print(f"{indent}list({len(obj)} items)")
                for item in obj[:5]:
                    pp(item, depth+1, max_depth)
            elif isinstance(obj, bytes):
                print(f"{indent}bytes({len(obj)}): {obj[:32].hex()}")
                if len(obj) > 4:
                    try:
                        inner, _ = blackboxprotobuf.decode_message(obj)
                        print(f"{indent}  -> nested proto:")
                        pp(inner, depth+2, max_depth)
                    except:
                        pass
            else:
                print(f"{indent}{repr(obj)[:200]}")
        pp(msg)
    except Exception as e:
        print(f"  proto decode error: {e}")
        traceback.print_exc()

try:
    backup = EncryptedBackup(backup_directory=backup_path, passphrase=PASSPHRASE)
    print("Forcing unlock via MTLibrary.sqlite...")
    try:
        backup.extract_file(
            relative_path="Library/Application Support/MTLibrary.sqlite",
            output_filename=os.path.join(tmpdir, "MTLibrary.sqlite")
        )
        print("  Unlock OK")
    except Exception as e:
        print(f"  Unlock attempt: {e}")

    for label, rel_path, fmt in TARGETS:
        print(f"\n{'='*50}")
        print(f"=== {label} ({fmt}) ===")
        out_path = os.path.join(tmpdir, label)
        try:
            backup.extract_file(relative_path=rel_path, output_filename=out_path)
        except Exception as e:
            print(f"  extract error: {e}")
            continue
        if not os.path.exists(out_path):
            print("  [not extracted]")
            continue
        if fmt == "plist":
            inspect_plist(out_path, label)
        elif fmt == "proto":
            inspect_proto(out_path, label)

except Exception as e:
    print(f"FATAL: {e}")
    traceback.print_exc()
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\nDone.")
'@

# Find Python - use where.exe, skip WindowsApps stub
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
