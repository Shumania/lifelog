#!/usr/bin/env python3
"""
Inspect Google Maps data in an encrypted iPhone backup.
Uses the same decryption approach as lifelog_extract.py.
v7: fix Unicode crash, use library's internal manifest connection directly.
"""

import os
import sys
import json
import sqlite3
import tempfile
import subprocess
import plistlib
import urllib.request
from pathlib import Path
from datetime import datetime

BACKUP_PASSWORD = "#ngrierBill70"
WEBHOOK_URL = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"

BACKUP_PATHS = [
    Path(os.environ.get("USERPROFILE", "")) / "Apple" / "MobileSync" / "Backup",
    Path(os.environ.get("APPDATA", "")) / "Apple Computer" / "MobileSync" / "Backup",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Apple" / "MobileSync" / "Backup",
]

output_lines = []

def out(msg=""):
    # ASCII-only to avoid cp1252 encoding errors on Windows console
    safe = msg.encode("ascii", errors="replace").decode("ascii")
    print(safe)
    output_lines.append(msg)  # keep original for webhook upload

def find_backup_dir():
    candidates = []
    for base in BACKUP_PATHS:
        if base.exists():
            for d in base.iterdir():
                if d.is_dir():
                    manifest = d / "Manifest.db"
                    manifest_plist = d / "Manifest.plist"
                    if manifest.exists() or manifest_plist.exists():
                        mtime = (manifest if manifest.exists() else manifest_plist).stat().st_mtime
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
        out("Installing iphone_backup_decrypt...")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "iphone_backup_decrypt", "--quiet"],
                check=True, capture_output=True
            )
            return True
        except Exception as e:
            out(f"Failed to install: {e}")
            return False

def get_manifest_conn(backup_dir):
    """Get a queryable SQLite connection to the decrypted manifest."""
    try:
        from iphone_backup_decrypt import EncryptedBackup
        backup = EncryptedBackup(backup_directory=str(backup_dir), passphrase=BACKUP_PASSWORD)

        # The library decrypts manifest at init time.
        # _manifest_db is a live sqlite3.Connection
        # _temp_manifest_db is the temp file path
        if hasattr(backup, '_manifest_db') and backup._manifest_db is not None:
            val = backup._manifest_db
            if isinstance(val, sqlite3.Connection):
                out("Got manifest connection from backup._manifest_db (sqlite3.Connection)")
                return val, backup
            elif isinstance(val, (str, Path)) and Path(str(val)).exists():
                p = str(val)
                # Make sure it's not the encrypted original
                backup_db = str(backup_dir / "Manifest.db")
                if p != backup_db:
                    out(f"Got manifest path from backup._manifest_db: {p}")
                    return sqlite3.connect(p), backup
                else:
                    out("_manifest_db pointed to encrypted original -- skipping")

        if hasattr(backup, '_temp_manifest_db') and backup._temp_manifest_db:
            p = str(backup._temp_manifest_db)
            if Path(p).exists() and p != str(backup_dir / "Manifest.db"):
                out(f"Got manifest path from backup._temp_manifest_db: {p}")
                return sqlite3.connect(p), backup

        # Dump all non-callable attributes for debugging
        out("Could not find manifest connection. Library attributes:")
        for attr in sorted(dir(backup)):
            if attr.startswith('__'):
                continue
            try:
                val = getattr(backup, attr)
                if not callable(val):
                    out(f"  .{attr} = {type(val).__name__}: {repr(str(val))[:120]}")
            except Exception:
                pass
        return None, backup

    except Exception as e:
        out(f"Error creating EncryptedBackup: {e}")
        return None, None

def extract_file(backup, domain, relative_path):
    """Extract a file from encrypted backup using the library object."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = tmp.name
        backup.extract_file(
            relative_path=relative_path,
            output_filename=tmp_path,
            domain_like=domain
        )
        if Path(tmp_path).exists() and Path(tmp_path).stat().st_size > 0:
            return Path(tmp_path)
        return None
    except Exception:
        return None

def main():
    backup_dir = find_backup_dir()
    if not backup_dir:
        out("No backup found!")
        return

    out(f"Using backup: {backup_dir}")

    if not ensure_decrypt_lib():
        out("Cannot proceed without iphone_backup_decrypt library")
        return

    # Step 1: Get manifest and list all domains
    out("\n=== Step 1: List all domains in backup ===")
    manifest_conn, backup_obj = get_manifest_conn(backup_dir)

    if manifest_conn:
        try:
            cur = manifest_conn.cursor()
            cur.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
            domains = [row[0] for row in cur.fetchall()]
            out(f"Total domains: {len(domains)}")

            # File counts per domain (top 40)
            out("\n=== File counts per domain (top 40) ===")
            cur.execute("SELECT domain, COUNT(*) as cnt FROM Files GROUP BY domain ORDER BY cnt DESC LIMIT 40")
            for row in cur.fetchall():
                out(f"  {row[1]:5d}  {row[0]}")

            # Google-related domains
            out("\n=== Google-related domains ===")
            cur.execute("SELECT DISTINCT domain FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR domain LIKE '%gmm%' OR domain LIKE '%Maps%'")
            google_domains = [row[0] for row in cur.fetchall()]
            if google_domains:
                for d in google_domains:
                    out(f"\n  Domain: {d}")
                    cur.execute("SELECT relativePath FROM Files WHERE domain=? ORDER BY relativePath", (d,))
                    for frow in cur.fetchall():
                        out(f"    {frow[0]}")
            else:
                out("  (none found)")

                # Show all domains for manual inspection
                out("\n=== All domains (for manual Google Maps search) ===")
                for d in domains:
                    out(f"  {d}")

        except Exception as e:
            out(f"Error querying manifest: {e}")
        finally:
            try:
                manifest_conn.close()
            except Exception:
                pass
    else:
        out("Could not get manifest DB.")

    # Step 2: Try direct extraction of known Google Maps files
    if backup_obj:
        out("\n=== Step 2: Try direct extraction of known Google Maps files ===")
        google_candidates = [
            ("AppDomain-com.google.Maps", "Library/Application Support/GMMTimeline/timeline.sqlite"),
            ("AppDomain-com.google.Maps", "Library/Application Support/GMMTimeline/Timeline.sqlite"),
            ("AppDomain-com.google.Maps", "Library/Application Support/GMMTimeline/PlacesVisited.sqlite"),
            ("AppDomain-com.google.Maps", "Library/Application Support/offline.db"),
            ("AppDomain-com.google.Maps", "Library/Caches/timeline.sqlite"),
            ("AppDomain-com.google.Maps", "Documents/timeline.sqlite"),
            ("%google%", "Library/Application Support/GMMTimeline/timeline.sqlite"),
            ("%google%", "Library/Application Support/GMMTimeline/Timeline.sqlite"),
            ("%maps%", "Library/Application Support/GMMTimeline/timeline.sqlite"),
            ("AppDomain-com.google.Maps", "Library/Preferences/com.google.Maps.plist"),
            ("%google%", "Library/Preferences/com.google.Maps.plist"),
        ]

        for domain, rel_path in google_candidates:
            result = extract_file(backup_obj, domain, rel_path)
            if result:
                out(f"\n[FOUND] {domain} / {rel_path}")
                out(f"   File size: {result.stat().st_size} bytes")
                # Try to open as sqlite or plist
                if rel_path.endswith(".sqlite") or rel_path.endswith(".db"):
                    try:
                        conn = sqlite3.connect(str(result))
                        cur2 = conn.cursor()
                        cur2.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        tables = [r[0] for r in cur2.fetchall()]
                        out(f"   Tables: {tables}")
                        for table in tables:
                            cur2.execute(f"SELECT COUNT(*) FROM [{table}]")
                            cnt = cur2.fetchone()[0]
                            cur2.execute(f"PRAGMA table_info([{table}])")
                            cols = [r[1] for r in cur2.fetchall()]
                            out(f"   {table}: {cnt} rows, cols: {cols}")
                        conn.close()
                    except Exception as e:
                        out(f"   Error reading DB: {e}")
                elif rel_path.endswith(".plist"):
                    try:
                        with open(result, 'rb') as f:
                            plist = plistlib.load(f)
                        out(f"   Plist keys: {list(plist.keys())[:30]}")
                    except Exception as e:
                        out(f"   Could not parse plist: {e}")
            else:
                out(f"  [not found] {domain} / {rel_path}")

    out("\nDone!")

if __name__ == "__main__":
    main()
    # Upload to webhook
    out("\nUploading output to Tasklet...")
    try:
        payload = json.dumps({
            "output": "\n".join(output_lines),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "computer": os.environ.get("COMPUTERNAME", "unknown"),
            "source": "inspect_googlemaps_backup"
        }).encode("utf-8")
        req = urllib.request.Request(
            WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"Upload: {resp.status}")
    except Exception as e:
        print(f"Upload failed: {e}")
