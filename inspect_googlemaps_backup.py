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

def main():
    backup_dir = find_backup_dir()
    if not backup_dir:
        print("ERROR: No backup directory found.")
        return

    print(f"Using backup: {backup_dir}")
    print(f"Decrypting backup manifest...")

    try:
        from iphone_backup_decrypt import EncryptedBackup
        backup = EncryptedBackup(backup_directory=backup_dir, passphrase=BACKUP_PASSWORD)
    except Exception as e:
        print(f"ERROR loading backup: {e}")
        return

    # The library decrypts the manifest to a temp path on init
    manifest_path = getattr(backup, '_temp_decrypted_manifest_db_path', None)
    print(f"Manifest temp path: {manifest_path}")
    print(f"Unlocked: {getattr(backup, '_unlocked', 'unknown')}")

    if not manifest_path or not os.path.exists(manifest_path):
        print("ERROR: Manifest DB temp path not found or doesn't exist.")
        print("Trying _manifest_db_path as fallback...")
        manifest_path = getattr(backup, '_manifest_db_path', None)

    if not manifest_path or not os.path.exists(manifest_path):
        print("ERROR: Cannot find any readable manifest DB.")
        return

    print(f"\nOpening manifest DB at: {manifest_path}")

    try:
        conn = sqlite3.connect(manifest_path)
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
                print(f"  [{flags}] {rel_path}  (id={file_id})")

        # Try to extract and inspect any .sqlite or .db files in Google domains
        print(f"\n=== Attempting to extract Google databases ===")
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
                print(f"\nTrying to extract: {domain}/{rel_path}")
                try:
                    tmp = tempfile.mkdtemp()
                    out_path = os.path.join(tmp, "extracted.db")
                    backup.extract_file(relative_name=rel_path, domain=domain, output_filename=out_path)
                    if os.path.exists(out_path):
                        size = os.path.getsize(out_path)
                        print(f"  Extracted! Size: {size} bytes")
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
                                if cnt > 0 and cnt <= 5:
                                    dc.execute(f"SELECT * FROM [{table}] LIMIT 3")
                                    for row in dc.fetchall():
                                        print(f"      {row}")
                            db.close()
                        except Exception as db_err:
                            print(f"  Could not read as SQLite: {db_err}")
                    else:
                        print(f"  Extraction produced no output file.")
                except Exception as e:
                    print(f"  Extraction failed: {e}")

        # Also show all domains for reference
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
    print("Uploading output to Tasklet...", end=" ")
    try:
        import urllib.request
        payload = json.dumps({"source": "inspect_googlemaps_backup", "output": output}).encode("utf-8")
        req = urllib.request.Request(WEBHOOK_URL, data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"OK ({resp.status})")
    except Exception as e:
        print(f"FAILED: {e}")
