$script = @'
import sys, os, tempfile, shutil, traceback, sqlite3, plistlib, json, struct

print(f"v15 | Python: {sys.executable}")

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

backup_path = None
for base in candidates:
    if os.path.isdir(base):
        for d in os.listdir(base):
            full = os.path.join(base, d)
            if os.path.isfile(os.path.join(full, "Manifest.db")):
                backup_path = full
                break
    if backup_path:
        break

if not backup_path:
    print("ERROR: No backup found")
    sys.exit(1)

print(f"Using backup: {backup_path}")

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

def decode_fixed64_as_double(b):
    """Try to interpret 8 bytes as a double (little-endian)"""
    if len(b) == 8:
        return struct.unpack('<d', b)[0]
    return None

def pp_deep(obj, depth=0, max_items=50):
    """Deep pretty-print with no depth limit, try to decode bytes as proto/plist/double"""
    indent = "  " * depth
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:max_items]:
            print(f"{indent}field_{k}:")
            pp_deep(v, depth+1)
    elif isinstance(obj, list):
        print(f"{indent}[{len(obj)} items]")
        for i, item in enumerate(obj[:max_items]):
            print(f"{indent}  [{i}]:")
            pp_deep(item, depth+2)
    elif isinstance(obj, bytes):
        # Try double
        d = decode_fixed64_as_double(obj)
        if d is not None and -200 < d < 200 and d != 0.0:
            print(f"{indent}bytes({len(obj)}) as double: {d}")
            return
        print(f"{indent}bytes({len(obj)}): {obj[:64].hex()}")
        # Try ASCII
        try:
            s = obj.decode('utf-8')
            print(f"{indent}  -> utf8: {repr(s[:200])}")
            return
        except:
            pass
        # Try nested proto
        if len(obj) > 2:
            try:
                inner, _ = blackboxprotobuf.decode_message(obj)
                print(f"{indent}  -> nested proto:")
                pp_deep(inner, depth+2)
                return
            except:
                pass
        # Try plist
        if obj[:6] == b'bplist':
            try:
                inner = plistlib.loads(obj)
                print(f"{indent}  -> nested plist: {repr(inner)[:300]}")
                return
            except:
                pass
    else:
        print(f"{indent}{repr(obj)[:300]}")

try:
    backup = EncryptedBackup(backup_directory=backup_path, passphrase=PASSPHRASE)
    print("Unlocking via MTLibrary.sqlite...")
    try:
        backup.extract_file(
            relative_path="Library/Application Support/MTLibrary.sqlite",
            output_filename=os.path.join(tmpdir, "MTLibrary.sqlite")
        )
        print("  OK")
    except Exception as e:
        print(f"  {e}")

    tlog_path = os.path.join(tmpdir, "tlogs_offline")
    print("\n=== Extracting tlogs_offline ===")
    try:
        backup.extract_file(
            relative_path="Library/Application Support/tlogs_offline_storage.binaryproto",
            output_filename=tlog_path
        )
    except Exception as e:
        print(f"  extract error: {e}")
        sys.exit(1)

    with open(tlog_path, 'rb') as f:
        raw = f.read()
    print(f"Size: {len(raw)} bytes")

    msg, typedef = blackboxprotobuf.decode_message(raw)
    print("\n--- Full decoded structure ---")
    pp_deep(msg)

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
