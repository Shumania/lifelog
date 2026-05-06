#!/usr/bin/env python3
"""
inspect_googlemaps_backup.py v8
- Forces decryption by extracting PODCASTS DB (proven to work)
- Uses correct API: domain_like= and output_filename= (not domain= / output_folder=)
- Then queries manifest DB for ALL Google Maps domain files
- Auto-uploads output to Tasklet webhook
"""

import os
import sys
import glob
import sqlite3
import tempfile
import shutil
import json
import urllib.request

WEBHOOK_URL = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
PODCASTS_DOMAIN = "AppDomainGroup-243LU875E5.groups.com.apple.podcasts"
PODCASTS_PATH = "Library/Application Support/COMApplePodcastsMedia/Documents/MTLibrary.sqlite"

def find_backup():
    candidates = []
    for base in [
        os.path.join(os.environ.get("USERPROFILE", ""), "Apple", "MobileSync", "Backup"),
        os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Roaming", "Apple Computer", "MobileSync", "Backup"),
        os.path.join(os.environ.get("APPDATA", ""), "Apple Computer", "MobileSync", "Backup"),
    ]:
        if os.path.isdir(base):
            for d in glob.glob(os.path.join(base, "*")):
                manifest = os.path.join(d, "Manifest.db")
                if os.path.isfile(manifest):
                    candidates.append((os.path.getmtime(d), d))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]

def post_output(text, source="inspect_googlemaps_backup"):
    try:
        data = json.dumps({
            "source": source,
            "computer": os.environ.get("COMPUTERNAME", "unknown"),
            "output": text
        }).encode("utf-8")
        req = urllib.request.Request(WEBHOOK_URL, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"Upload: {resp.status}")
    except Exception as e:
        print(f"Upload failed: {e}")

def extract_file(backup, relative_path, domain_like, tmp_dir, name):
    """Extract a file using the correct API (output_filename= + domain_like=)."""
    out_path = os.path.join(tmp_dir, name)
    backup.extract_file(
        relative_path=relative_path,
        output_filename=out_path,
        domain_like=domain_like
    )
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        return out_path
    return None

def main():
    lines = []
    def p(s=""):
        print(s)
        lines.append(str(s))

    p("inspect_googlemaps_backup.py v8")
    p(f"Machine: {os.environ.get('COMPUTERNAME', 'unknown')}")
    p()

    backup_path = find_backup()
    if not backup_path:
        p("ERROR: No backup found.")
        post_output("\n".join(lines))
        return

    p(f"Using backup: {backup_path}")
    p()

    try:
        from iphone_backup_decrypt import EncryptedBackup
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "iphone_backup_decrypt", "-q"])
        from iphone_backup_decrypt import EncryptedBackup

    password = "#ngrierBill70"
    tmp_dir = tempfile.mkdtemp()

    try:
        backup = EncryptedBackup(backup_directory=backup_path, passphrase=password)

        # === Step 1: Force unlock via PODCASTS DB ===
        p("=== Step 1: Force unlock via podcasts DB ===")
        try:
            out = extract_file(backup, PODCASTS_PATH, PODCASTS_DOMAIN, tmp_dir, "podcasts_unlock.sqlite")
            if out:
                p(f"Podcasts DB extracted: {os.path.getsize(out)} bytes — backup is UNLOCKED ✓")
            else:
                p("WARNING: Podcasts DB extraction returned no file")
        except Exception as e:
            p(f"WARNING: Podcasts DB extraction failed: {e}")

        p(f"  _unlocked = {getattr(backup, '_unlocked', '?')}")
        p()

        # === Step 2: Query manifest DB for ALL Google Maps files ===
        p("=== Step 2: All Google Maps files in manifest DB ===")

        manifest_db = None
        try:
            tmp_manifest = backup._temp_decrypted_manifest_db_path
            if tmp_manifest and os.path.isfile(tmp_manifest):
                manifest_db = tmp_manifest
                p(f"Decrypted manifest at: {tmp_manifest}")
        except:
            pass

        if not manifest_db:
            orig = os.path.join(backup_path, "Manifest.db")
            if os.path.isfile(orig):
                try:
                    conn = sqlite3.connect(orig)
                    conn.execute("SELECT count(*) FROM Files")
                    conn.close()
                    manifest_db = orig
                    p(f"Using original manifest: {orig}")
                except:
                    p("Original manifest is encrypted")

        if manifest_db:
            conn = sqlite3.connect(manifest_db)
            rows = conn.execute("""
                SELECT domain, relativePath, fileID, length(file) as fileSize
                FROM Files
                WHERE domain LIKE '%google%' OR domain LIKE '%maps%' OR domain LIKE '%Maps%'
                ORDER BY domain, relativePath
            """).fetchall()
            p(f"Total Google Maps files: {len(rows)}")
            p()
            for domain, path, fileID, size in rows:
                p(f"  [{size or 0:>8}B] {domain} / {path}")

            # Also search for timeline/location keywords across ALL domains
            p()
            p("=== Step 2b: Any timeline/location files across ALL domains ===")
            rows2 = conn.execute("""
                SELECT domain, relativePath, length(file) as fileSize
                FROM Files
                WHERE relativePath LIKE '%timeline%' OR relativePath LIKE '%Timeline%'
                   OR relativePath LIKE '%location%' OR relativePath LIKE '%Location%'
                   OR relativePath LIKE '%GMMTimeline%' OR relativePath LIKE '%significant%'
                ORDER BY domain, relativePath
            """).fetchall()
            p(f"Total timeline/location files: {len(rows2)}")
            for domain, path, size in rows2:
                p(f"  [{size or 0:>8}B] {domain} / {path}")
            conn.close()
        else:
            p("Could not access manifest DB after unlock attempt")

        p()

        # === Step 3: Try to extract sqlite/db files from Google Maps domain ===
        p("=== Step 3: Try to extract sqlite/db files from Google Maps domain ===")
        sqlite_paths = [
            "Library/Application Support/GMMTimeline/timeline.sqlite",
            "Library/Application Support/GMMTimeline/Timeline.sqlite",
            "Library/Application Support/GMMTimeline/GMM_Timeline.sqlite",
            "Library/Application Support/GMMTimeline/PlacesVisited.sqlite",
            "Library/Application Support/offline.db",
            "Library/Application Support/timeline.db",
            "Library/Application Support/GMMCore/timeline.sqlite",
            "Library/Application Support/GMMCore/GMMCore.sqlite",
            "Documents/timeline.sqlite",
            "Library/Caches/timeline.sqlite",
        ]
        for i, sp in enumerate(sqlite_paths):
            try:
                out = extract_file(backup, sp, "AppDomain-com.google.Maps", tmp_dir, f"gmaps_{i}.sqlite")
                if out:
                    p(f"[FOUND] {sp}")
                    fsize = os.path.getsize(out)
                    p(f"  Size: {fsize} bytes")
                    try:
                        c = sqlite3.connect(out)
                        tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                        p(f"  Tables: {[t[0] for t in tables]}")
                        c.close()
                    except Exception as e:
                        p(f"  Not valid sqlite: {e}")
                else:
                    p(f"  [not found] {sp}")
            except Exception as e:
                p(f"  [error] {sp}: {e}")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    p()
    p("Done!")

    full_output = "\n".join(lines)
    p("\nUploading output to Tasklet...")
    post_output(full_output)

if __name__ == "__main__":
    main()
