#!/usr/bin/env python3
"""
Enumerate all files in Google Maps domains from iPhone backup.
Shows relativePath and fileID for every file in Maps-related domains.
"""
import argparse
import os
import sys
import tempfile

try:
    from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "iphone-backup-decrypt", "-q"])
    from iphone_backup_decrypt import EncryptedBackup, RelativePath, RelativePathsLike

PASSWORD = "#ngrierBill70"

MAPS_DOMAINS = [
    "AppDomain-com.google.Maps",
    "AppDomainGroup-group.com.google.Maps",
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", required=True)
    args = parser.parse_args()

    print(f"Backup path: {args.backup}")
    print("Unlocking backup...")

    backup = EncryptedBackup(backup_directory=args.backup, passphrase=PASSWORD)

    # Unlock by extracting podcasts DB
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            backup.extract_files(
                relative_paths_like="%MTLibrary.sqlite",
                output_folder=tmpdir,
                domain_like="AppDomainGroup-243LU875E5.groups.com.apple.podcasts"
            )
            print("Backup unlocked!")
        except Exception as e:
            print(f"Unlock warning: {e}")

    # Query manifest for all files in Maps domains
    print("\n=== FILES IN GOOGLE MAPS DOMAINS ===")
    try:
        cursor = backup.manifest_db_cursor()
        for domain in MAPS_DOMAINS:
            print(f"\n--- Domain: {domain} ---")
            rows = cursor.execute(
                "SELECT fileID, relativePath FROM Files WHERE domain = ? ORDER BY relativePath",
                (domain,)
            ).fetchall()
            if rows:
                for fileID, relPath in rows:
                    print(f"  {relPath or '(root)'}  |  {fileID}")
                print(f"  Total: {len(rows)} files")
            else:
                print("  (no files)")
    except Exception as e:
        print(f"Manifest query error: {e}")
        # Fallback: query decrypted manifest directly
        import sqlite3, glob
        print("Trying fallback manifest query...")
        for f in glob.glob(os.path.join(tempfile.gettempdir(), "**", "Manifest.db"), recursive=True):
            try:
                conn = sqlite3.connect(f)
                for domain in MAPS_DOMAINS:
                    print(f"\n--- Domain: {domain} (from {f}) ---")
                    rows = conn.execute(
                        "SELECT fileID, relativePath FROM Files WHERE domain = ? ORDER BY relativePath",
                        (domain,)
                    ).fetchall()
                    for fileID, relPath in rows:
                        print(f"  {relPath or '(root)'}  |  {fileID}")
                    print(f"  Total: {len(rows)} files")
                conn.close()
                break
            except:
                pass

    print("\nDone.")

if __name__ == "__main__":
    main()
