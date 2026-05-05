#!/usr/bin/env python3
"""
inspect_googlemaps_backup.py v5
Diagnostic: test decryption, enumerate all domains, find Google Maps files.
Auto-uploads output to Tasklet webhook.
"""

import os, sys, sqlite3, tempfile, json, io, subprocess, struct, hashlib, plistlib
from pathlib import Path
from contextlib import redirect_stdout

WEBHOOK_URL = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
BACKUP_PASSWORD = "#ngrierBill70"

def find_backup_dir():
    candidates = [
        os.path.expandvars(r"%USERPROFILE%\Apple\MobileSync\Backup"),
        os.path.expandvars(r"%APPDATA%\Apple Computer\MobileSync\Backup"),
    ]
    for base in candidates:
        if os.path.isdir(base):
            subdirs = [os.path.join(base, d) for d in os.listdir(base)
                       if os.path.isdir(os.path.join(base, d))]
            if subdirs:
                return max(subdirs, key=lambda p: os.path.getmtime(p))
    return None

def ensure_pip_package(pkg_name, import_name=None):
    import_name = import_name or pkg_name
    try:
        __import__(import_name)
        return True
    except ImportError:
        print(f"  Installing {pkg_name}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg_name, "-q"])
        return True

def decrypt_manifest_raw(backup_dir, password):
    """
    Decrypt Manifest.db using raw plistlib + pycryptodome approach.
    Returns path to decrypted temp file, or None.
    """
    manifest_plist = Path(backup_dir) / "Manifest.plist"
    manifest_db   = Path(backup_dir) / "Manifest.db"

    if not manifest_plist.exists():
        print("  ERROR: Manifest.plist not found")
        return None

    with open(manifest_plist, "rb") as f:
        plist = plistlib.load(f)

    if not plist.get("IsEncrypted"):
        print("  Backup is NOT encrypted — Manifest.db should be readable directly")
        return str(manifest_db)

    print("  Backup IS encrypted. Decrypting manifest keybag...")

    # Check for BackupKeyBag
    keybag_data = plist.get("BackupKeyBag")
    if not keybag_data:
        print("  ERROR: No BackupKeyBag in Manifest.plist")
        return None

    # We'll use iphone_backup_decrypt library for the heavy lifting
    ensure_pip_package("iphone-backup-decrypt", "iphone_backup_decrypt")

    try:
        from iphone_backup_decrypt import EncryptedBackup
        print("  iphone_backup_decrypt imported OK")

        backup = EncryptedBackup(backup_directory=str(backup_dir), passphrase=password)

        # Dump ALL attributes to find manifest path
        attrs = {}
        for attr in dir(backup):
            if not attr.startswith("__"):
                try:
                    val = getattr(backup, attr)
                    if not callable(val):
                        attrs[attr] = str(val)
                except Exception:
                    pass

        print("  Backup object attributes:")
        for k, v in sorted(attrs.items()):
            print(f"    {k} = {v}")

        # Try common attribute names for the manifest path
        manifest_path = None
        for attr in ['_temp_decrypted_manifest_db_path', '_manifest_db_path',
                     '_manifest_path', 'manifest_db', '_db', '_temp_manifest']:
            val = getattr(backup, attr, None)
            if val and Path(str(val)).exists():
                manifest_path = str(val)
                print(f"  Found manifest at backup.{attr} = {manifest_path}")
                break

        # If not found yet, try extracting a file to force unlock
        if not manifest_path:
            print("  Trying to unlock by extracting podcasts DB...")
            tmp = tempfile.mktemp(suffix=".sqlite")
            try:
                backup.extract_file(
                    relative_path="Documents/MTLibrary.sqlite",
                    output_filename=tmp,
                    domain_like="%groups.com.apple.podcasts"
                )
                if Path(tmp).exists() and Path(tmp).stat().st_size > 0:
                    print(f"  Podcasts DB extracted! ({Path(tmp).stat().st_size:,} bytes)")
                else:
                    print("  extract_file returned but file missing/empty")
            except Exception as e:
                print(f"  extract_file error: {e}")

            # Re-check attributes after extraction
            for attr in ['_temp_decrypted_manifest_db_path', '_manifest_db_path',
                         '_manifest_path', 'manifest_db', '_db', '_temp_manifest']:
                val = getattr(backup, attr, None)
                if val and Path(str(val)).exists():
                    manifest_path = str(val)
                    print(f"  Post-extraction: found manifest at backup.{attr}")
                    break

        # Scan temp dirs for any Manifest.db files
        if not manifest_path:
            print("  Scanning temp dirs for Manifest.db...")
            tmpdir = tempfile.gettempdir()
            for root, dirs, files in os.walk(tmpdir):
                for f in files:
                    if "manifest" in f.lower() and f.endswith(".db"):
                        full = os.path.join(root, f)
                        print(f"  Found temp manifest: {full} ({os.path.getsize(full):,} bytes)")
                        manifest_path = full
                        break
                if manifest_path:
                    break

        return manifest_path

    except Exception as e:
        import traceback
        print(f"  EncryptedBackup error: {e}")
        traceback.print_exc()
        return None

def read_manifest(manifest_path):
    """Read all files from a decrypted Manifest.db."""
    conn = sqlite3.connect(manifest_path)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
    domains = [r[0] for r in cursor.fetchall()]

    cursor.execute("SELECT COUNT(*) FROM Files")
    total = cursor.fetchone()[0]
    conn.close()
    return domains, total

def search_manifest_for_google(manifest_path):
    conn = sqlite3.connect(manifest_path)
    cursor = conn.cursor()

    # All google-related entries
    cursor.execute("""
        SELECT domain, relativePath, fileID, flags
        FROM Files
        WHERE domain LIKE '%google%' OR domain LIKE '%goog%'
           OR relativePath LIKE '%google%' OR relativePath LIKE '%maps%'
           OR relativePath LIKE '%timeline%' OR relativePath LIKE '%gmm%'
        ORDER BY domain, relativePath
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows

def main():
    buf = io.StringIO()
    with redirect_stdout(buf):
        print("=== LifeLog Google Maps Inspector v5 ===\n")

        backup_dir = find_backup_dir()
        if not backup_dir:
            print("ERROR: No backup directory found.")
        else:
            print(f"Backup: {backup_dir}")
            manifest_plist = Path(backup_dir) / "Manifest.plist"
            if manifest_plist.exists():
                with open(manifest_plist, "rb") as f:
                    plist = plistlib.load(f)
                print(f"IsEncrypted: {plist.get('IsEncrypted')}")
                print(f"iOS Version: {plist.get('ProductVersion', 'unknown')}")
                print(f"Device: {plist.get('DeviceName', 'unknown')}\n")

            print("--- Decrypting manifest ---")
            manifest_path = decrypt_manifest_raw(backup_dir, BACKUP_PASSWORD)

            if manifest_path:
                print(f"\nManifest DB: {manifest_path}")
                try:
                    domains, total = read_manifest(manifest_path)
                    print(f"Total files in backup: {total:,}")
                    print(f"Total domains: {len(domains)}")

                    # Show all domains
                    print("\n--- All domains ---")
                    for d in domains:
                        print(f"  {d}")

                    # Search for Google Maps
                    print("\n--- Google/Maps related files ---")
                    google_files = search_manifest_for_google(manifest_path)
                    if google_files:
                        for domain, path, fid, flags in google_files:
                            print(f"  [{domain}]")
                            print(f"    {path}  (id={fid[:8]}..., flags={flags})")
                    else:
                        print("  (none found)")
                except Exception as e:
                    import traceback
                    print(f"Error reading manifest: {e}")
                    traceback.print_exc()
            else:
                print("\nCould not decrypt manifest. See errors above.")

        print("\nDone!")

    output = buf.getvalue()
    print(output)

    # Upload to webhook
    import urllib.request
    print("Uploading to Tasklet...", end="", flush=True)
    try:
        body = json.dumps({
            "source": "inspect_googlemaps_backup",
            "output": output
        }).encode()
        req = urllib.request.Request(WEBHOOK_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        print(" OK")
    except Exception as e:
        print(f" FAILED: {e}")

if __name__ == "__main__":
    main()
