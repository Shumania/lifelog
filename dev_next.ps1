$script = @'
import sys, os, tempfile, shutil, traceback, sqlite3, struct

print(f"v12 | Python: {sys.executable}")

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
    from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike
except ImportError:
    print("Installing iphone_backup_decrypt...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "iphone-backup-decrypt"])
    from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike

PASSPHRASE = "#ngrierBill70"
tmpdir = tempfile.mkdtemp()
print(f"Temp dir: {tmpdir}")

# Candidate Google Maps files to extract and inspect
CANDIDATES = [
    ("FrequentTripsData",       "Library/Application Support/FrequentTripsData"),
    ("DirectionsData",          "Library/Application Support/DirectionsData"),
    ("PlacesheetVisits",        "Library/Application Support/PlacesheetVisits"),
    ("tlogs_offline.binaryproto","Library/Application Support/tlogs_offline_storage.binaryproto"),
    ("LocalSuggestions",        "Library/Application Support/LocalSuggestions"),
    ("OnDeviceAliasData",       "Library/Application Support/OnDeviceAliasData"),
    ("UserParametersData",      "Library/Application Support/UserParametersData"),
    ("circumstantial.state",    "Library/Application Support/circumstantial.state"),
]

def inspect_file(label, path):
    if not os.path.exists(path):
        print(f"  [NOT EXTRACTED]")
        return
    size = os.path.getsize(path)
    print(f"  Size: {size} bytes")
    if size == 0:
        print(f"  [EMPTY]")
        return

    # Try SQLite
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print(f"  SQLite tables: {tables}")
        for t in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM [{t}]")
                cnt = cur.fetchone()[0]
                cur.execute(f"PRAGMA table_info([{t}])")
                cols = [r[1] for r in cur.fetchall()]
                print(f"    {t}: {cnt} rows, cols: {cols}")
                if cnt > 0:
                    cur.execute(f"SELECT * FROM [{t}] LIMIT 3")
                    for row in cur.fetchall():
                        print(f"      {row}")
            except Exception as e:
                print(f"    {t}: error {e}")
        conn.close()
        return
    except Exception:
        pass

    # Try reading as text/JSON
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read(2000)
        print(f"  Text preview: {repr(content[:500])}")
        return
    except:
        pass

    # Binary: show first 64 bytes as hex
    with open(path, 'rb') as f:
        header = f.read(64)
    print(f"  Binary header (hex): {header.hex()}")
    print(f"  Binary header (repr): {repr(header)}")

try:
    backup = EncryptedBackup(backup_directory=backup_path, passphrase=PASSPHRASE)

    # Force unlock by extracting a known file first
    # Use extract_file_raw by fileID for podcasts to unlock
    podcasts_fileID = None
    mdb_path = getattr(backup, '_temp_decrypted_manifest_db_path', None)

    # Try to find manifest DB (may already exist from previous run)
    if mdb_path and os.path.exists(mdb_path):
        print(f"Manifest DB already available: {mdb_path}")
    else:
        # Need to force unlock - try extracting with the correct relative_path
        print("Forcing unlock via podcasts extract...")
        podcasts_out = os.path.join(tmpdir, "MTLibrary.sqlite")
        try:
            backup.extract_file(
                relative_path="Library/Application Support/MTLibrary.sqlite",
                output_filename=podcasts_out
            )
        except Exception as e:
            print(f"  Unlock attempt error (non-fatal): {e}")
        mdb_path = getattr(backup, '_temp_decrypted_manifest_db_path', None)

    print(f"_unlocked={getattr(backup,'_unlocked',None)}")

    # Get the domain for com.google.Maps by querying manifest
    mdb_path = getattr(backup, '_temp_decrypted_manifest_db_path', None)
    if not mdb_path or not os.path.exists(mdb_path):
        print("ERROR: Manifest DB not found")
        sys.exit(1)

    mconn = sqlite3.connect(mdb_path)
    mcur = mconn.cursor()

    print("\n=== Extracting Google Maps candidate files ===")
    for label, rel_path in CANDIDATES:
        print(f"\n--- {label} ---")
        print(f"  Path: {rel_path}")

        # Find fileID from manifest
        mcur.execute("SELECT fileID, domain FROM Files WHERE relativePath=? AND domain='AppDomain-com.google.Maps'", (rel_path,))
        row = mcur.fetchone()
        if not row:
            mcur.execute("SELECT fileID, domain FROM Files WHERE relativePath=?", (rel_path,))
            row = mcur.fetchone()

        if not row:
            print(f"  [NOT IN MANIFEST]")
            continue

        fileID, domain = row
        print(f"  fileID: {fileID}, domain: {domain}")

        # Try to extract via backup
        out_path = os.path.join(tmpdir, label)
        try:
            backup.extract_file(
                relative_path=rel_path,
                output_filename=out_path
            )
            inspect_file(label, out_path)
        except Exception as e:
            print(f"  extract_file error: {e}")
            # Try reading raw backup file (will be encrypted but let's see)
            raw_path = os.path.join(backup_path, fileID[:2], fileID)
            if os.path.exists(raw_path):
                size = os.path.getsize(raw_path)
                print(f"  Raw backup file exists: {size} bytes (encrypted)")
            else:
                print(f"  Raw backup file not found at {raw_path}")

    mconn.close()

    # Also check AppDomainGroup-group.com.google.Maps files
    print("\n=== AppDomainGroup-group.com.google.Maps files ===")
    mconn2 = sqlite3.connect(mdb_path)
    mcur2 = mconn2.cursor()
    mcur2.execute("SELECT fileID, relativePath, flags FROM Files WHERE domain='AppDomainGroup-group.com.google.Maps' ORDER BY relativePath")
    for fileID, relpath, flags in mcur2.fetchall():
        print(f"  {relpath} | flags={flags} | fileID={fileID[:8]}...")
    mconn2.close()

except Exception as e:
    print(f"FATAL ERROR: {e}")
    traceback.print_exc()
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\nDone.")
'@

$script | python - 2>&1
