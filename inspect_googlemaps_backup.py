#!/usr/bin/env python3
"""
Inspect Google Maps data in an encrypted iPhone backup.
Uses the same iphone_backup_decrypt library as lifelog_extract.py.
Auto-uploads output to Tasklet via webhook.
"""

import os
import sys
import io
import json
import shutil
import sqlite3
import subprocess
import tempfile
import plistlib
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

BACKUP_PASSWORD = "#ngrierBill70"
WEBHOOK_URL = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

BACKUP_PATHS = [
    Path(os.environ.get("USERPROFILE", "")) / "Apple" / "MobileSync" / "Backup",
    Path(os.environ.get("APPDATA", "")) / "Apple Computer" / "MobileSync" / "Backup",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Apple" / "MobileSync" / "Backup",
]

# Known podcasts DB - we know this exists in the backup
PODCASTS_DOMAIN = "AppDomainGroup-243LU875E5.groups.com.apple.podcasts"
PODCASTS_PATH = "Library/Application Support/com.apple.podcasts/MTLibrary.sqlite"


class Tee:
    """Write to multiple streams simultaneously."""
    def __init__(self, *files):
        self.files = files
    def write(self, text):
        for f in self.files:
            f.write(text)
    def flush(self):
        for f in self.files:
            f.flush()


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


def get_manifest_db(backup):
    """Try every known way to access the manifest DB."""
    # Try all possible attribute names
    for attr in ["_manifest_db", "_db", "manifest_db", "_manifest", "manifest"]:
        val = getattr(backup, attr, None)
        if val is not None:
            return val

    # Try extracting the podcasts DB (known to exist) to force manifest loading
    print("Forcing manifest load via known podcasts DB extraction...")
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = tmp.name
        backup.extract_file(
            relative_path=PODCASTS_PATH,
            output_filename=tmp_path,
            domain_like=PODCASTS_DOMAIN
        )
        print(f"  Extracted podcasts DB ({os.path.getsize(tmp_path):,} bytes)")
        os.unlink(tmp_path)
    except Exception as e:
        print(f"  Podcasts extraction: {e}")

    # Try attributes again after extraction
    for attr in ["_manifest_db", "_db", "manifest_db", "_manifest", "manifest"]:
        val = getattr(backup, attr, None)
        if val is not None:
            return val

    # Debug: print all non-callable attributes to find the right one
    print("\nDebug - backup object attributes:")
    for attr in sorted(dir(backup)):
        if not attr.startswith('__') and not callable(getattr(backup, attr, None)):
            val = getattr(backup, attr, None)
            print(f"  {attr}: {type(val).__name__} = {str(val)[:80]}")

    return None


def search_manifest(manifest_db):
    """Search the manifest for Google Maps and location-related files."""
    cur = manifest_db.cursor()

    # Get all unique domains
    print("\n=== All app domains in backup ===")
    cur.execute("SELECT DISTINCT domain FROM Files WHERE domain LIKE 'AppDomain%' ORDER BY domain")
    domains = [r[0] for r in cur.fetchall()]
    print(f"Total app domains: {len(domains)}")

    # Filter for anything Google or Maps related
    google_domains = [d for d in domains if 'google' in d.lower() or 'maps' in d.lower() or 'location' in d.lower()]
    print(f"\nGoogle/Maps/Location domains: {len(google_domains)}")
    for d in google_domains:
        print(f"  {d}")

    if not google_domains:
        print("  (none found)")
        # Print all domains so we can manually find Google Maps
        print("\nAll AppDomain entries (to manually spot Google):")
        for d in domains:
            print(f"  {d}")
        return

    # For each Google/Maps domain, list files
    for domain in google_domains:
        print(f"\n=== Files in {domain} ===")
        cur.execute("""
            SELECT relativePath, fileID, flags
            FROM Files
            WHERE domain = ?
            ORDER BY relativePath
        """, (domain,))
        rows = cur.fetchall()
        print(f"  {len(rows)} files total")
        for rel_path, file_id, flags in rows[:100]:
            print(f"  [{flags}] {rel_path}")

        # Check for SQLite files specifically
        sqlite_files = [(r, f) for r, f, _ in rows if r.endswith('.sqlite') or r.endswith('.db')]
        if sqlite_files:
            print(f"\n  SQLite/DB files:")
            for rel, fid in sqlite_files:
                print(f"    {rel} (ID: {fid})")


def try_extract_google_db(backup, domain, rel_path):
    """Try to extract and inspect a specific database file."""
    print(f"\n  Trying to extract: {rel_path}")
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = tmp.name
        backup.extract_file(
            relative_path=rel_path,
            output_filename=tmp_path,
            domain_like=domain
        )
        size = os.path.getsize(tmp_path)
        print(f"  Extracted: {size:,} bytes")

        # Try to open as SQLite
        try:
            conn = sqlite3.connect(tmp_path)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [r[0] for r in cur.fetchall()]
            print(f"  Tables: {tables}")
            for table in tables[:10]:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM [{table}]")
                    count = cur.fetchone()[0]
                    cur.execute(f"PRAGMA table_info([{table}])")
                    cols = [r[1] for r in cur.fetchall()]
                    print(f"    {table}: {count} rows, cols: {cols[:8]}")
                    if count > 0 and count < 5:
                        cur.execute(f"SELECT * FROM [{table}] LIMIT 3")
                        for row in cur.fetchall():
                            print(f"      {row}")
                except Exception as e:
                    print(f"    {table}: error - {e}")
            conn.close()
        except Exception as e:
            print(f"  Not a SQLite DB: {e}")
            try:
                with open(tmp_path, 'rb') as f:
                    content = f.read(200)
                print(f"  First bytes: {content[:50]}")
            except:
                pass
        os.unlink(tmp_path)
    except Exception as e:
        print(f"  Extraction failed: {e}")


def upload_output(output_text):
    """POST output to Tasklet webhook."""
    try:
        print("\nUploading output to Tasklet...", end="", flush=True)
        payload = json.dumps({
            "source": "inspect_googlemaps_backup",
            "output": output_text
        }).encode("utf-8")
        req = urllib.request.Request(
            WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f" OK (HTTP {resp.status})")
    except Exception as e:
        print(f" FAILED: {e}")
        print("(Output was printed above — copy and paste it manually if needed)")


def main():
    # Capture all output while also printing it
    captured = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = Tee(original_stdout, captured)

    try:
        backup_dir = find_backup_dir()
        if not backup_dir:
            print("ERROR: No iPhone backup found.")
            sys.stdout = original_stdout
            sys.exit(1)
        print(f"Using backup: {backup_dir}")

        if not ensure_decrypt_lib():
            print("ERROR: Cannot proceed without iphone_backup_decrypt library.")
            sys.stdout = original_stdout
            sys.exit(1)

        from iphone_backup_decrypt import EncryptedBackup

        print("Decrypting backup manifest (this may take 30-60 seconds first time)...")
        try:
            backup = EncryptedBackup(backup_directory=str(backup_dir), passphrase=BACKUP_PASSWORD)
        except Exception as e:
            print(f"ERROR: Failed to open backup: {e}")
            sys.stdout = original_stdout
            sys.exit(1)

        manifest_db = get_manifest_db(backup)

        if manifest_db:
            print("Manifest DB accessible!")
            search_manifest(manifest_db)
        else:
            print("\nERROR: Could not access manifest DB via any method.")
            print("Trying brute-force extraction of likely Google Maps paths...")

            google_domain_guesses = [
                "AppDomain-com.google.Maps",
                "AppDomain-com.google.maps",
                "AppDomainGroup-com.google.Maps",
                "AppDomainGroup-com.google.maps",
            ]
            path_guesses = [
                "Library/Application Support/timeline.sqlite",
                "Library/Application Support/GMMTimeline/timeline.sqlite",
                "Library/Application Support/GMMTimeline/GMMTimeline.sqlite",
                "Library/Application Support/Offline/offline.db",
                "Library/Application Support/com.google.Maps/timeline.sqlite",
                "Documents/GMMTimeline.sqlite",
                "Documents/timeline.sqlite",
            ]
            for domain in google_domain_guesses:
                for path in path_guesses:
                    try_extract_google_db(backup, domain, path)

        print("\nDone!")

    finally:
        sys.stdout = original_stdout
        output_text = captured.getvalue()

    # Upload to Tasklet
    upload_output(output_text)


if __name__ == "__main__":
    main()
