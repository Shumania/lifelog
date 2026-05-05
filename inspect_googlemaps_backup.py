#!/usr/bin/env python3
"""
Inspect Google Maps data in an encrypted iPhone backup.
Uses the same iphone_backup_decrypt library as lifelog_extract.py.
"""

import os
import sys
import json
import shutil
import sqlite3
import subprocess
import tempfile
import plistlib
from pathlib import Path
from datetime import datetime, timezone

BACKUP_PASSWORD = "#ngrierBill70"

BACKUP_PATHS = [
    Path(os.environ.get("USERPROFILE", "")) / "Apple" / "MobileSync" / "Backup",
    Path(os.environ.get("APPDATA", "")) / "Apple Computer" / "MobileSync" / "Backup",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Apple" / "MobileSync" / "Backup",
]


def find_backup_dir():
    candidates = []
    for base in BACKUP_PATHS:
        if base.exists():
            for d in base.iterdir():
                if d.is_dir():
                    manifest = d / "Manifest.plist"
                    if manifest.exists():
                        mtime = manifest.stat().st_mtime
                        candidates.append((mtime, d))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def ensure_decrypt_lib():
    try:
        import iphone_backup_decrypt
        return True
    except ImportError:
        print("Installing iphone_backup_decrypt...")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "iphone_backup_decrypt", "--quiet"],
                check=True, capture_output=True
            )
            return True
        except Exception as e:
            print(f"Failed to install: {e}")
            return False


def main():
    backup_dir = find_backup_dir()
    if not backup_dir:
        print("ERROR: No iPhone backup found.")
        sys.exit(1)
    print(f"Using backup: {backup_dir}")

    if not ensure_decrypt_lib():
        print("ERROR: Cannot proceed without iphone_backup_decrypt library.")
        sys.exit(1)

    from iphone_backup_decrypt import EncryptedBackup

    print("Decrypting backup manifest (this may take 30-60 seconds first time)...")
    try:
        backup = EncryptedBackup(backup_directory=str(backup_dir), passphrase=BACKUP_PASSWORD)
    except Exception as e:
        print(f"ERROR: Failed to open backup: {e}")
        sys.exit(1)

    # Access the internal decrypted manifest database
    # The library stores it as _manifest_db after decryption
    manifest_db = None
    for attr in ["_manifest_db", "_db", "manifest_db"]:
        manifest_db = getattr(backup, attr, None)
        if manifest_db:
            break

    if not manifest_db:
        # Try to find it by triggering a dummy extraction which opens manifest
        print("Trying to access manifest via extraction...")
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                tmp_path = tmp.name
            backup.extract_file(
                relative_path="Library/Preferences/com.apple.Maps.plist",
                output_filename=tmp_path,
                domain_like="AppDomain-com.apple.Maps"
            )
        except Exception:
            pass

        for attr in ["_manifest_db", "_db", "manifest_db"]:
            manifest_db = getattr(backup, attr, None)
            if manifest_db:
                break

    if manifest_db:
        print("\n=== Searching manifest for Google Maps files ===")
        try:
            cur = manifest_db.cursor()

            # Search for any Google-related domains
            cur.execute("""
                SELECT domain, relativePath, fileID
                FROM Files
                WHERE domain LIKE '%google%' OR domain LIKE '%Google%'
                ORDER BY domain, relativePath
            """)
            rows = cur.fetchall()
            print(f"Google-related files: {len(rows)}")
            for domain, rel_path, file_id in rows[:50]:
                print(f"  [{domain}] {rel_path}")

            # Also search for Maps
            cur.execute("""
                SELECT domain, relativePath, fileID
                FROM Files
                WHERE domain LIKE '%Maps%' OR domain LIKE '%maps%'
                ORDER BY domain, relativePath
                LIMIT 50
            """)
            maps_rows = cur.fetchall()
            print(f"\nMaps-related files: {len(maps_rows)}")
            for domain, rel_path, file_id in maps_rows[:50]:
                print(f"  [{domain}] {rel_path}")

        except Exception as e:
            print(f"Manifest query error: {e}")
    else:
        print("Could not access manifest DB directly. Trying known paths...")

    # Try extracting known Google Maps database locations
    print("\n=== Trying known Google Maps database locations ===")

    google_maps_candidates = [
        ("AppDomain-com.google.Maps", "Library/Application Support/Offline/offline.db"),
        ("AppDomain-com.google.Maps", "Library/Application Support/GMMTimeline/timeline.sqlite"),
        ("AppDomain-com.google.Maps", "Library/Application Support/timeline.sqlite"),
        ("AppDomain-com.google.Maps", "Library/Caches/com.google.Maps/timeline.sqlite"),
        ("AppDomain-com.google.Maps", "Documents/timeline.sqlite"),
        ("AppDomain-com.google.Maps", "Documents/offline.db"),
        ("AppDomain-com.google.Maps", "Library/Application Support/GMMTimeline/GMMTimeline.sqlite"),
        ("AppDomain-com.google.Maps", "Library/Application Support/GMMTimeline/Timeline.sqlite"),
        ("AppDomain-com.google.Maps", "Library/Application Support/GMMTimeline/PlacesVisited.sqlite"),
    ]

    found_any = False
    for domain, rel_path in google_maps_candidates:
        print(f"Trying {rel_path}...", end=" ")
        try:
            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
                tmp_path = tmp.name

            backup.extract_file(
                relative_path=rel_path,
                output_filename=tmp_path,
                domain_like=domain
            )

            if Path(tmp_path).exists() and Path(tmp_path).stat().st_size > 100:
                size = Path(tmp_path).stat().st_size
                print(f"FOUND! ({size:,} bytes)")
                found_any = True

                # Inspect the database
                try:
                    db = sqlite3.connect(tmp_path)
                    cur = db.cursor()
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                    tables = [r[0] for r in cur.fetchall()]
                    print(f"  Tables: {tables}")

                    for table in tables:
                        cur.execute(f"SELECT COUNT(*) FROM [{table}]")
                        count = cur.fetchone()[0]
                        cur.execute(f"PRAGMA table_info([{table}])")
                        cols = [r[1] for r in cur.fetchall()]
                        marker = "***" if any(kw in table.lower() for kw in ['location', 'timeline', 'place', 'visit', 'trip', 'route', 'history', 'travel']) else "   "
                        print(f"  {marker} {table}: {count} rows | cols: {cols}")

                    db.close()
                except Exception as e:
                    print(f"  Could not inspect DB: {e}")

                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            else:
                print("not found")
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        except Exception as e:
            print(f"error: {e}")

    # Dump all files in com.google.Maps domain if manifest accessible
    if manifest_db:
        print("\n=== All files in com.google.Maps domain ===")
        try:
            cur = manifest_db.cursor()
            cur.execute("""
                SELECT domain, relativePath, fileID
                FROM Files
                WHERE domain = 'AppDomain-com.google.Maps'
                ORDER BY relativePath
            """)
            all_rows = cur.fetchall()
            print(f"Total files: {len(all_rows)}")
            for domain, rel_path, file_id in all_rows:
                print(f"  {rel_path}")
        except Exception as e:
            print(f"Error: {e}")

    if not found_any:
        print("\nNo Google Maps databases found at known locations.")
        print("Google may store Timeline data differently on this iOS version.")

    print("\nDone! Paste this output back to Tasklet.")


if __name__ == "__main__":
    main()
