# Write Python script to a temp file to avoid all PowerShell quoting issues
$pyScript = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.py'

@'
import sys, os, tempfile, shutil, traceback, sqlite3, plistlib, json

print("v21 | Python: " + sys.executable)
print("USERPROFILE: " + os.environ.get("USERPROFILE", "<not set>"))

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

backup_path = None
for base in candidates:
    exists = os.path.isdir(base)
    print("  " + ("[EXISTS]" if exists else "[missing]") + " " + base)
    if exists and not backup_path:
        try:
            for d in os.listdir(base):
                full = os.path.join(base, d)
                if os.path.isfile(os.path.join(full, "Manifest.db")):
                    print("    -> Found backup: " + d)
                    backup_path = full
                    break
        except Exception as e:
            print("    -> listdir error: " + str(e))

if not backup_path:
    print("No backup in candidates, doing deep walk...")
    try:
        for root, dirs, files in os.walk(up, topdown=True):
            depth = root[len(up):].count(os.sep)
            if depth > 6:
                dirs[:] = []
                continue
            if "MobileSync" in root and "Manifest.db" in files:
                parent = os.path.dirname(root)
                if os.path.basename(parent) == "Backup":
                    print("    -> Found: " + root)
                    backup_path = root
                    break
    except Exception as e:
        print("Walk error: " + str(e))

if not backup_path:
    print("ERROR: No backup found anywhere")
    sys.exit(1)

print("\nUsing backup: " + backup_path)

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
    # Force unlock by extracting a known file
    try:
        backup.extract_file(
            relative_path="Library/Application Support/MTLibrary.sqlite",
            output_filename=os.path.join(tmpdir, "MTLibrary.sqlite")
        )
        print("Unlock OK")
    except Exception as e:
        print("Unlock attempt: " + str(e))

    # Extract tlogs_offline
    proto_path = os.path.join(tmpdir, "tlogs_offline")
    try:
        backup.extract_file(
            relative_path="Library/Application Support/tlogs_offline_storage.binaryproto",
            output_filename=proto_path
        )
    except Exception as e:
        print("tlogs extract error: " + str(e))
        sys.exit(1)

    if not os.path.exists(proto_path):
        print("tlogs file not found in backup")
        sys.exit(1)

    with open(proto_path, "rb") as f:
        raw = f.read()
    print("tlogs size: " + str(len(raw)) + " bytes")

    msg, typedef = blackboxprotobuf.decode_message(raw)

    def flatten(obj, path=""):
        results = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                results.extend(flatten(v, path + "." + str(k)))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                results.extend(flatten(item, path + "[" + str(i) + "]"))
        elif isinstance(obj, bytes):
            try:
                inner, _ = blackboxprotobuf.decode_message(obj)
                results.extend(flatten(inner, path + "(bytes->proto)"))
            except:
                pass
            try:
                s = obj.decode("utf-8")
                results.append((path, "str", s[:100]))
            except:
                results.append((path, "bytes", obj[:16].hex()))
        elif isinstance(obj, int):
            results.append((path, "int", obj))
        elif isinstance(obj, float):
            results.append((path, "float", obj))
        else:
            results.append((path, type(obj).__name__, str(obj)[:100]))
        return results

    leaves = flatten(msg)

    latlons = []
    timestamps = []
    for path, typ, val in leaves:
        if typ == "int":
            if 1000000 < abs(val) < 1800000000:
                latlons.append((path, val, val/1e7))
            elif 1000000000 < val < 9999999999:
                timestamps.append((path, val))
            elif 1000000000000 < val < 9999999999999:
                timestamps.append((path, val, "ms"))
        elif typ == "float":
            if -180 <= val <= 180:
                latlons.append((path, val, "float"))

    print("\n=== CANDIDATE LAT/LON VALUES ===")
    for item in latlons[:30]:
        print(item)

    print("\n=== CANDIDATE TIMESTAMPS ===")
    for item in timestamps[:20]:
        print(item)

    print("\n=== STRUCTURE (top level) ===")
    if isinstance(msg, dict):
        for k, v in list(msg.items())[:5]:
            vlen = len(v) if hasattr(v, "__len__") else "n/a"
            print("  field_" + str(k) + ": " + type(v).__name__ + " len=" + str(vlen))
            if isinstance(v, dict):
                for k2, v2 in list(v.items())[:5]:
                    v2len = len(v2) if hasattr(v2, "__len__") else "n/a"
                    print("    field_" + str(k2) + ": " + type(v2).__name__ + " len=" + str(v2len))
                    if isinstance(v2, dict):
                        for k3, v3 in list(v2.items())[:3]:
                            v3str = str(v3)[:80] if not isinstance(v3, list) else "list(" + str(len(v3)) + ")"
                            print("      field_" + str(k3) + ": " + type(v3).__name__ + " val=" + v3str)
                            if isinstance(v3, list) and len(v3) > 0:
                                item0 = v3[0]
                                if isinstance(item0, dict):
                                    for k4, v4 in list(item0.items())[:8]:
                                        v4str = str(v4)[:80] if not isinstance(v4, (list, dict, bytes)) else type(v4).__name__ + "(" + str(len(v4)) + ")"
                                        print("        [0].field_" + str(k4) + ": " + type(v4).__name__ + " val=" + v4str)

except Exception as e:
    print("FATAL: " + str(e))
    traceback.print_exc()
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\nDone.")
'@ | Set-Content -Path $pyScript -Encoding UTF8

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

& $pythonExe $pyScript 2>&1
Remove-Item $pyScript -ErrorAction SilentlyContinue
