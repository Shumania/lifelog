#!/usr/bin/env python3
"""
inspect_googlemaps_backup.py
Inspect Google Maps data in encrypted iPhone backup.
Auto-uploads output to Tasklet webhook.
"""

import os
import sys
import sqlite3
import tempfile
import json
import io
from contextlib import redirect_stdout
from pathlib import Path

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

def ensure_decrypt_lib():
    try:
        import iphone_backup_decrypt
        return True
    except ImportError:
        print("Installing iphone-backup-decrypt...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "iphone-backup-decrypt", "-q"])
        return True

def main():
    backup_dir = find_backup_dir()
    if not backup_dir:
        print("ERROR: No backup directory found.")
        return

    print(f"Using backup: {backup_dir}")

    ensure_decrypt_lib()

    from iphone_backup_decrypt import EncryptedBackup

    print("Initializing encrypted backup...")
    backup = EncryptedBackup(backup_directory=backup_dir, passphrase=BACKUP_PASSWORD)

    # Force unlock by extracting a known file (podcasts DB)
    # This triggers manifest decryption internally
    print("Unlocking backup by extracting a known file (this may take 30-60s)...")
    tmp_dir = tempfile.mkdtemp()
    tmp_known = os.path.join(tmp_dir, "podcasts_test.sqlite")
    try:
        backup.extract_file(
            relative_path="Documents/MTLibrary.sqlite",
            output_filename=tmp_known,
            domain_like="%groups.com.apple.podcasts"
        )
        if os.path.exists(tmp_known):
            print(f"  Podcasts DB extracted OK ({os.path.getsize(tmp_known):,} bytes) — backup is unlocked!")
        else:
            print("  Podcasts DB not found, but continuing...")
    except Exception as e:
        print(f"  Extract attempt: {e} — continuing anyway...")

    # Now check internal state
    manifest_path = getattr(backup, '_temp_decrypted_manifest_db_path', None)
    unlocked = getattr(backup, '_unlocked', None)
    print(f"Unlocked: {unlocked}")
    print(f"Manifest temp path: {manifest_path}")

    if not manifest_path or not os.path.exists(manifest_path):
        # Try alternate attribute names used in different library versions
        for attr in ['_manifest_db_path', 'manifest_db', '_db_path']:
            alt = getattr(backup, attr, None)
            if alt and os.path.exists(str(alt)):
                manifest_path = str(alt)
                print(f"Found manifest via backup.{attr}: {manifest_path}")
                break

    if not manifest_path or not os.path.exists(str(manifest_path)):
        print("\nERROR: Cannot locate decrypted manifest DB.")
        print("Dumping backup object attributes for debugging:")
        for attr in dir(backup):
            if not attr.startswith('__'):
                try:
                    val = getattr(backup, attr)
                    if not callable(val):
                        print(f"  backup.{attr} = {val}")
                except Exception:
                    pass
        return

    print(f"\nReading manifest from: {manifest_path}")
    try:
        conn = sqlite3.connect(str(manifest_path))
        cursor = conn.cursor()

        # List all domains
        cursor.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
        all_domains = [row[0] for row in cursor.fetchall()]
        print(f"\nTotal domains in backup: {len(all_domains)}")

        # Find Google-related domains
        google_domains = [d for d in all_domains if d and 'google' in d.lower()]
        print(f"\n=== Google-related domains ({len(google_domains)}) ===")
        for d in google_domains:
            cursor.execute("SELECT COUNT(*) FROM Files WHERE domain=?", (d,))
            count = cursor.fetchone()[0]
            print(f"  {d}  ({count} files)")

        # For each Google domain, list all files
        for domain in google_domains:
            print(f"\n--- Files in {domain} ---")
            cursor.execute("""
                SELECT relativePath, fileID, flags
                FROM Files
                WHERE domain=?
                ORDER BY relativePath
            """, (domain,))
            rows = cursor.fetchall()
            for rel_path, file_id, flags in rows:
                print(f"  [{flags}] {rel_path}  (id={file_id[:8]}...)")

        # Try to extract and inspect any .sqlite or .db files in Google domains
        if google_domains:
            print(f"\n=== Attempting to extract Google SQLite databases ===")
            for domain in google_domains:
                cursor.execute("""
                    SELECT relativePath, fileID
                    FROM Files
                    WHERE domain=? AND (
                        relativePath LIKE '%.sqlite' OR
                        relativePath LIKE '%.db' OR
                        relativePath LIKE '%.sqlite3'
                    )
                """, (domain,))
                db_files = cursor.fetchall()
                for rel_path, file_id in db_files:
                    print(f"\nExtracting: {domain}/{rel_path}")
                    try:
                        out_path = os.path.join(tmp_dir, "gmaps_extracted.db")
                        if os.path.exists(out_path):
                            os.remove(out_path)
                        backup.extract_file(
                            relative_path=rel_path,
                            output_filename=out_path,
                            domain_like=domain
                        )
                        if os.path.exists(out_path):
                            size = os.path.getsize(out_path)
                            print(f"  Extracted! Size: {size:,} bytes")
                            try:
                                db = sqlite3.connect(out_path)
                                dc = db.cursor()
                                dc.execute("SELECT name FROM sqlite_master WHERE type='table'")
                                tables = [r[0] for r in dc.fetchall()]
                                print(f"  Tables: {tables}")
                                for table in tables:
                                    dc.execute(f"SELECT COUNT(*) FROM [{table}]")
                                    cnt = dc.fetchone()[0]
                                    dc.execute(f"PRAGMA table_info([{table}])")
                                    cols = [r[1] for r in dc.fetchall()]
                                    print(f"    {table}: {cnt} rows, columns: {cols}")
                                    if 0 < cnt <= 5:
                                        dc.execute(f"SELECT * FROM [{table}] LIMIT 3")
                                        for row in dc.fetchall():
                                            print(f"      {row}")
                                db.close()
                            except Exception as db_err:
                                print(f"  Could not read as SQLite: {db_err}")
                        else:
                            print(f"  No output file produced.")
                    except Exception as e:
                        print(f"  Extraction failed: {e}")

        # Show all domains for reference
        print(f"\n=== ALL domains in backup ===")
        for d in all_domains:
            print(f"  {d}")

        conn.close()

    except Exception as e:
        print(f"ERROR reading manifest: {e}")
        import traceback
        traceback.print_exc()

    print("\nDone!")

if __name__ == "__main__":
    # Capture all output
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        main()
    output = buffer.getvalue()
    print(output)

    # Upload to Tasklet
    print("Uploading output to Tasklet...", end=" ", flush=True)
    try:
        import urllib.request
        payload = json.dumps({"source": "inspect_googlemaps_backup", "output": output}).encode("utf-8")
        req = urllib.request.Request(WEBHOOK_URL, data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"OK ({resp.status})")
    except Exception as e:
        print(f"FAILED: {e}")
