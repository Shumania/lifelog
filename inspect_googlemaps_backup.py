#!/usr/bin/env python3
"""
Inspect Google Maps data in an encrypted iPhone backup.
Uses the same decryption approach as lifelog_extract.py.
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
    print(msg)
    output_lines.append(msg)

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

def extract_file(backup_dir, domain, relative_path):
    """Extract a file from encrypted backup - exact pattern from lifelog_extract.py"""
    try:
        from iphone_backup_decrypt import EncryptedBackup
        backup = EncryptedBackup(backup_directory=str(backup_dir), passphrase=BACKUP_PASSWORD)
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
    except Exception as e:
        return None

def get_manifest_db(backup_dir):
    """Get a queryable manifest DB by extracting it through the library."""
    try:
        from iphone_backup_decrypt import EncryptedBackup
        backup = EncryptedBackup(backup_directory=str(backup_dir), passphrase=BACKUP_PASSWORD)

        # The library decrypts Manifest.db to a temp location - find it
        # Try known internal attributes
        manifest_path = None
        for attr in ['_manifest_db', '_manifest_db_path', '_temp_manifest', 'manifest_db']:
            val = getattr(backup, attr, None)
            if val and Path(str(val)).exists():
                manifest_path = str(val)
                break

        # Also check temp dir
        if not manifest_path:
            temp_dir = getattr(backup, '_temp_dir', None) or getattr(backup, 'temp_dir', None)
            if temp_dir:
                candidate = Path(str(temp_dir)) / "Manifest.db"
                if candidate.exists():
                    manifest_path = str(candidate)

        if manifest_path:
            out(f"Found decrypted manifest at: {manifest_path}")
            return sqlite3.connect(manifest_path)

        # Fallback: dump all attributes for debugging
        out("Could not find manifest DB. Library attributes:")
        for attr in dir(backup):
            if not attr.startswith('__'):
                try:
                    val = getattr(backup, attr)
                    if not callable(val):
                        out(f"  .{attr} = {repr(val)[:100]}")
                except:
                    pass
        return None
    except Exception as e:
        out(f"Error creating EncryptedBackup: {e}")
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

    # Step 1: Try to get the manifest DB and list all domains
    out("\n=== Step 1: List all domains in backup ===")
    manifest_conn = get_manifest_db(backup_dir)
    if manifest_conn:
        try:
            cur = manifest_conn.cursor()
            cur.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
            domains = [row[0] for row in cur.fetchall()]
            out(f"Total domains: {len(domains)}")
            out("\nAll domains:")
            for d in domains:
                out(f"  {d}")

            # Count files per domain
            out("\n=== File counts per domain (top 30) ===")
            cur.execute("SELECT domain, COUNT(*) as cnt FROM Files GROUP BY domain ORDER BY cnt DESC LIMIT 30")
            for row in cur.fetchall():
                out(f"  {row[1]:5d}  {row[0]}")

            # Look for Google Maps specifically
            out("\n=== Google-related domains ===")
            cur.execute("SELECT DISTINCT domain FROM Files WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR domain LIKE '%gmm%'")
            google_domains = [row[0] for row in cur.fetchall()]
            for d in google_domains:
                out(f"  {d}")
                cur.execute("SELECT relativePath FROM Files WHERE domain=? ORDER BY relativePath", (d,))
                for frow in cur.fetchall():
                    out(f"    {frow[0]}")

            manifest_conn.close()
        except Exception as e:
            out(f"Error querying manifest: {e}")
    else:
        out("Could not get manifest DB.")

    # Step 2: Try direct extraction of known Google Maps paths
    out("\n=== Step 2: Try direct extraction of known Google Maps files ===")
    google_candidates = [
        ("AppDomain-com.google.Maps", "Library/Application Support/GMMTimeline/timeline.sqlite"),
        ("AppDomain-com.google.Maps", "Library/Application Support/GMMTimeline/Timeline.sqlite"),
        ("AppDomain-com.google.Maps", "Library/Application Support/GMMTimeline/PlacesVisited.sqlite"),
        ("AppDomain-com.google.Maps", "Library/Application Support/offline.db"),
        ("AppDomain-com.google.Maps", "Library/Caches/timeline.sqlite"),
        ("AppDomain-com.google.Maps", "Documents/timeline.sqlite"),
        ("AppDomain-com.google.Maps", "Library/Application Support/GMMTimeline/GMMTimeline.sqlite"),
        ("%google%", "Library/Application Support/GMMTimeline/timeline.sqlite"),
        ("%maps%", "Library/Application Support/GMMTimeline/timeline.sqlite"),
    ]

    for domain, rel_path in google_candidates:
        result = extract_file(backup_dir, domain, rel_path)
        if result:
            out(f"\n✅ FOUND: {domain} / {rel_path}")
            out(f"   File size: {result.stat().st_size} bytes")
            try:
                conn = sqlite3.connect(str(result))
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in cur.fetchall()]
                out(f"   Tables: {tables}")
                for table in tables:
                    cur.execute(f"SELECT COUNT(*) FROM [{table}]")
                    cnt = cur.fetchone()[0]
                    cur.execute(f"PRAGMA table_info([{table}])")
                    cols = [r[1] for r in cur.fetchall()]
                    out(f"   {table}: {cnt} rows, cols: {cols}")
                conn.close()
            except Exception as e:
                out(f"   Error reading DB: {e}")
        else:
            out(f"  ✗ {domain} / {rel_path}")

    # Step 3: Try to extract any file from Google Maps to confirm domain name
    out("\n=== Step 3: Try extracting Info.plist from Google Maps ===")
    for domain_try in ["AppDomain-com.google.Maps", "AppDomain-com.google.maps"]:
        result = extract_file(backup_dir, domain_try, "Library/Preferences/com.google.Maps.plist")
        if result:
            out(f"✅ Domain confirmed: {domain_try}")
            try:
                with open(result, 'rb') as f:
                    plist = plistlib.load(f)
                out(f"   Keys: {list(plist.keys())[:20]}")
            except Exception as e:
                out(f"   (Could not parse plist: {e})")
            break
        else:
            out(f"  ✗ {domain_try}")

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
        }).encode()
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
