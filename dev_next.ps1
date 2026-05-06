$script = @'
import sys, os, tempfile, shutil, traceback, sqlite3, plistlib, json

print(f"v13 | Python: {sys.executable}")

# Find backup
backup_path = None
for base in [
    os.path.join(os.environ.get("USERPROFILE",""), "Apple", "MobileSync", "Backup"),
    os.path.join(os.environ.get("USERPROFILE",""), "AppData", "Roaming", "Apple Computer", "MobileSync", "Backup"),
]:
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

print(f"Backup: {backup_path}")

try:
    from iphone_backup_decrypt import EncryptedBackup, RelativePath
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "iphone-backup-decrypt"])
    from iphone_backup_decrypt import EncryptedBackup, RelativePath

# Install blackboxprotobuf for protobuf decoding
try:
    import blackboxprotobuf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "blackboxprotobuf"])
    import blackboxprotobuf

PASSPHRASE = "#ngrierBill70"
tmpdir = tempfile.mkdtemp()

TARGETS = [
    ("PlacesheetVisits",        "Library/Application Support/PlacesheetVisits",        "plist"),
    ("LocalSuggestions",        "Library/Application Support/LocalSuggestions",         "plist"),
    ("FrequentTripsData",       "Library/Application Support/FrequentTripsData",        "plist"),
    ("DirectionsData",          "Library/Application Support/DirectionsData",           "plist"),
    ("tlogs_offline",           "Library/Application Support/tlogs_offline_storage.binaryproto", "proto"),
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
                # Try nested plist
                if obj[:6] == b'bplist':
                    try:
                        inner = plistlib.loads(obj)
                        print(f"{indent}  -> nested plist:")
                        summarize(inner, depth+2)
                    except:
                        pass
                # Try nested protobuf
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
        # Pretty print with truncation
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
                        try:
                            inner = plistlib.loads(obj)
                            print(f"{indent}  -> nested plist:")
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

    # Force unlock
    print("Forcing unlock...")
    try:
        backup.extract_file(
            relative_path="Library/Application Support/MTLibrary.sqlite",
            output_filename=os.path.join(tmpdir, "MTLibrary.sqlite")
        )
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

$script | python - 2>&1
