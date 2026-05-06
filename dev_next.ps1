$script = @'
import sys, os, tempfile, shutil, traceback

print(f"v10 | Python: {sys.executable}")

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

try:
    backup = EncryptedBackup(backup_directory=backup_path, passphrase=PASSPHRASE)
    print(f"Backup object created. _unlocked={getattr(backup,'_unlocked',None)}")

    # Step 1: Extract podcasts DB to force full manifest load
    podcasts_out = os.path.join(tmpdir, "MTLibrary.sqlite")
    print("Extracting podcasts DB to force manifest load...")
    try:
        backup.extract_file(
            relative_name="Library/Application Support/MTLibrary.sqlite",
            domain_like="AppDomainGroup-243LU875E5.groups.com.apple.podcasts",
            output_filename=podcasts_out
        )
        podcasts_size = os.path.getsize(podcasts_out) if os.path.exists(podcasts_out) else 0
        print(f"Podcasts DB extracted: {podcasts_size} bytes")
    except Exception as e:
        print(f"Podcasts extract error (non-fatal): {e}")

    print(f"After extraction: _unlocked={getattr(backup,'_unlocked',None)}, decrypted={getattr(backup,'decrypted',None)}")

    # Step 2: Query manifest DB for Google Maps files
    import sqlite3
    manifest_db = getattr(backup, '_temp_decrypted_manifest_db_path', None) or getattr(backup, '_manifest_db_path', None)
    print(f"Manifest DB path: {manifest_db}")

    if manifest_db and os.path.exists(manifest_db):
        conn = sqlite3.connect(manifest_db)
        cur = conn.cursor()

        # Search for Google Maps related files
        print("\n--- Google Maps files in manifest ---")
        cur.execute("""
            SELECT fileID, domain, relativePath, flags, file
            FROM Files
            WHERE domain LIKE '%google%maps%' OR domain LIKE '%Maps%'
               OR relativePath LIKE '%maps%timeline%' OR relativePath LIKE '%googlemaps%'
               OR domain LIKE '%com.google.Maps%'
            ORDER BY domain, relativePath
            LIMIT 100
        """)
        rows = cur.fetchall()
        print(f"Found {len(rows)} rows matching Google Maps")
        for row in rows:
            fileID, domain, relpath, flags, file_blob = row
            print(f"  domain={domain}")
            print(f"  relpath={relpath}")
            print(f"  fileID={fileID}, flags={flags}")
            print()

        # Also check all domains for anything Google-related
        print("\n--- All Google-related domains ---")
        cur.execute("""
            SELECT DISTINCT domain FROM Files
            WHERE domain LIKE '%google%' OR domain LIKE '%Google%'
            ORDER BY domain
        """)
        for (domain,) in cur.fetchall():
            print(f"  {domain}")

        # Count files per Google domain
        print("\n--- File counts per Google domain ---")
        cur.execute("""
            SELECT domain, COUNT(*) as cnt FROM Files
            WHERE domain LIKE '%google%' OR domain LIKE '%Google%'
            GROUP BY domain ORDER BY cnt DESC
        """)
        for (domain, cnt) in cur.fetchall():
            print(f"  {cnt:4d}  {domain}")

        conn.close()
    else:
        print("ERROR: Manifest DB not accessible")
        # Try reading raw Manifest.db
        raw_manifest = os.path.join(backup_path, "Manifest.db")
        print(f"Trying raw (unencrypted) manifest at {raw_manifest}")
        if os.path.exists(raw_manifest):
            try:
                conn2 = sqlite3.connect(raw_manifest)
                cur2 = conn2.cursor()
                cur2.execute("SELECT DISTINCT domain FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%Google%' ORDER BY domain")
                for (d,) in cur2.fetchall():
                    print(f"  RAW: {d}")
                conn2.close()
            except Exception as e2:
                print(f"Raw manifest error: {e2}")

except Exception as e:
    print(f"FATAL ERROR: {e}")
    traceback.print_exc()
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("Done.")
'@

$script | python - 2>&1
