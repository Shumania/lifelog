#!/usr/bin/env python3
"""
LifeLog iPhone Backup Extractor
Triggers a fresh iPhone backup, then extracts Apple Podcasts and Safari history.
Posts results to the LifeLog webhook.

Usage: python lifelog_extract.py
"""

import os
import sys
import json
import time
import shutil
import hashlib
import sqlite3
import subprocess
import tempfile
import platform
import socket
import plistlib
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# -- Configuration (injected by installer) -----------------------------------
WEBHOOK_URL = "https://tasklet.ai/webhook/whe_53am4mf5n5afky5t8xeq"
BACKUP_PASSWORD = "#ngrierBill70"
DEVICE_ID = socket.gethostname()

# -- Backup locations ---------------------------------------------------------
BACKUP_PATHS = [
    Path(os.environ.get("APPDATA", "")) / "Apple Computer" / "MobileSync" / "Backup",  # iTunes
    Path(os.environ.get("USERPROFILE", "")) / "Apple" / "MobileSync" / "Backup",        # Apple Devices (new)
    Path(os.environ.get("LOCALAPPDATA", "")) / "Apple" / "MobileSync" / "Backup",
]

# -- libimobiledevice paths ---------------------------------------------------
IDEVICEBACKUP2_PATHS = [
    Path(r"C:\ProgramData\LifeLog\imd\idevicebackup2.exe"),
    Path(r"C:\Program Files\libimobiledevice\idevicebackup2.exe"),
    shutil.which("idevicebackup2") or "",
]

LOG_FILE = Path(r"C:\ProgramData\LifeLog\lifelog.log")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def find_idevicebackup2():
    for p in IDEVICEBACKUP2_PATHS:
        if p and Path(p).exists():
            return str(p)
    return None


def trigger_backup(idb2_path):
    """Trigger a fresh backup via libimobiledevice."""
    log("Triggering iPhone backup via libimobiledevice...")
    try:
        result = subprocess.run(
            [idb2_path, "backup", "--full", r"C:\ProgramData\LifeLog\backup_tmp"],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode == 0:
            log("Backup completed successfully.")
            return True
        else:
            log(f"Backup exited with code {result.returncode}: {result.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        log("Backup timed out after 10 minutes.")
        return False
    except Exception as e:
        log(f"Backup error: {e}")
        return False


def find_backup_dir():
    """Find the most recent iPhone backup directory."""
    candidates = []
    for base in BACKUP_PATHS:
        if base.exists():
            for d in base.iterdir():
                if d.is_dir() and (len(d.name) == 40 or (len(d.name) >= 20 and all(c in '0123456789ABCDEFabcdef-' for c in d.name))):  # UDID format (old 40-char or new hyphenated)
                    manifest = d / "Manifest.db"
                    manifest_plist = d / "Manifest.plist"
                    if manifest.exists() or manifest_plist.exists():
                        mtime = (manifest if manifest.exists() else manifest_plist).stat().st_mtime
                        candidates.append((mtime, d))

    tmp_backup = Path(r"C:\ProgramData\LifeLog\backup_tmp")
    if tmp_backup.exists():
        for d in tmp_backup.iterdir():
            if d.is_dir() and len(d.name) == 40:
                manifest = d / "Manifest.db"
                if manifest.exists():
                    mtime = manifest.stat().st_mtime
                    candidates.append((mtime, d))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    best = candidates[0][1]
    log(f"Using backup at: {best}")
    return best


def is_backup_encrypted(backup_dir):
    """Check if backup is encrypted via Manifest.plist."""
    manifest_plist = backup_dir / "Manifest.plist"
    if manifest_plist.exists():
        try:
            with open(manifest_plist, "rb") as f:
                manifest = plistlib.load(f)
            return manifest.get("IsEncrypted", False)
        except Exception:
            pass
    return False


def ensure_decrypt_lib():
    """Install iphone_backup_decrypt if not present."""
    try:
        import iphone_backup_decrypt
        return True
    except ImportError:
        log("Installing iphone_backup_decrypt library...")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "iphone_backup_decrypt", "--quiet"],
                check=True, capture_output=True
            )
            log("iphone_backup_decrypt installed.")
            return True
        except Exception as e:
            log(f"Failed to install iphone_backup_decrypt: {e}")
            return False


def get_file_from_encrypted_backup(backup_dir, domain, relative_path):
    """Extract and decrypt a file from an encrypted backup."""
    if not ensure_decrypt_lib():
        return None
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
        log(f"Encrypted extraction error ({domain}/{relative_path}): {e}")
        return None


def get_file_from_backup(backup_dir, domain, relative_path, encrypted=False):
    """Extract a specific file from a backup (encrypted or plain)."""
    if encrypted:
        return get_file_from_encrypted_backup(backup_dir, domain, relative_path)

    # Plain backup: SHA1 hash lookup
    file_hash = hashlib.sha1(f"{domain}-{relative_path}".encode()).hexdigest()
    file_path = backup_dir / file_hash[:2] / file_hash
    if file_path.exists():
        return file_path

    # Fallback: Manifest.db lookup
    manifest_path = backup_dir / "Manifest.db"
    if not manifest_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(manifest_path))
        cur = conn.cursor()
        cur.execute(
            "SELECT fileID FROM Files WHERE domain=? AND relativePath=?",
            (domain, relative_path)
        )
        row = cur.fetchone()
        conn.close()
        if row:
            fid = row[0]
            file_path = backup_dir / fid[:2] / fid
            if file_path.exists():
                return file_path
    except Exception as e:
        log(f"Manifest lookup error: {e}")
    return None


def extract_podcasts(backup_dir, encrypted=False):
    # Note: domain uses wildcard to match any group ID prefix (e.g. 243LU875E5.groups.com.apple.podcasts)
    """Extract Apple Podcasts listening history from backup."""
    episodes = []

    # Primary: AppDomainGroup (wildcard handles group ID like 243LU875E5)
    podcast_db_path = get_file_from_backup(
        backup_dir,
        "%groups.com.apple.podcasts",
        "Documents/MTLibrary.sqlite",
        encrypted=encrypted
    )

    if not podcast_db_path:
        # Fallback: old AppDomain location
        podcast_db_path = get_file_from_backup(
            backup_dir,
            "AppDomain-com.apple.podcasts",
            "Documents/MTLibrary.sqlite",
            encrypted=encrypted
        )

    if not podcast_db_path:
        podcast_db_path = get_file_from_backup(
            backup_dir,
            "AppDomain-com.apple.podcasts",
            "Library/Caches/MTLibrary.sqlite",
            encrypted=encrypted
        )

    if not podcast_db_path:
        log("Apple Podcasts DB not found in backup. Is Podcasts app installed?")
        return episodes

    # Copy to temp to avoid lock issues (skip if already temp from decrypt)
    if encrypted:
        tmp_path = str(podcast_db_path)
        cleanup = True
    else:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = tmp.name
        shutil.copy2(str(podcast_db_path), tmp_path)
        cleanup = True

    try:
        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("""
            SELECT
                e.ZTITLE as title,
                e.ZWEBPAGEURL as episode_url,
                e.ZAUTHOR as author,
                e.ZDURATION as duration_seconds,
                e.ZPLAYHEAD as progress_seconds,
                e.ZLASTDATEPLAYED as last_played_apple_epoch,
                e.ZPLAYCOUNT as play_count,
                e.ZENCLOSUREURL as download_url,
                p.ZTITLE as show_name,
                p.ZFEEDURL as feed_url,
                p.ZAUTHOR as show_author
            FROM ZMTEPISODE e
            LEFT JOIN ZMTPODCAST p ON e.ZPODCAST = p.Z_PK
            WHERE e.ZLASTDATEPLAYED IS NOT NULL
              AND e.ZLASTDATEPLAYED > 0
            ORDER BY e.ZLASTDATEPLAYED DESC
        """)
        rows = cur.fetchall()
        conn.close()

        apple_epoch_offset = 978307200
        for row in rows:
            try:
                unix_ts = float(row["last_played_apple_epoch"]) + apple_epoch_offset
                dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
                timestamp = dt.isoformat()
            except Exception:
                timestamp = None

            if not timestamp:
                continue

            duration = row["duration_seconds"] or 0
            progress = row["progress_seconds"] or 0
            percent = round(progress / duration, 3) if duration > 0 else 0.0

            episodes.append({
                "title": row["title"] or "Unknown Episode",
                "show_name": row["show_name"] or "Unknown Show",
                "feed_url": row["feed_url"] or "",
                "episode_url": row["episode_url"] or row["download_url"] or "",
                "author": row["author"] or row["show_author"] or "",
                "timestamp": timestamp,
                "duration_seconds": int(duration),
                "progress_seconds": int(progress),
                "percent_complete": percent,
            })

    except Exception as e:
        log(f"Podcast extraction error: {e}")
    finally:
        if cleanup:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    log(f"Extracted {len(episodes)} podcast episodes.")
    return episodes


def extract_safari(backup_dir, encrypted=False):
    """Extract Safari browsing history from backup."""
    visits = []

    safari_db_path = get_file_from_backup(
        backup_dir,
        "AppDomain-com.apple.mobilesafari",
        "Library/Safari/History.db",
        encrypted=encrypted
    )

    if not safari_db_path:
        log("Safari History.db not found in backup.")
        return visits

    if encrypted:
        tmp_path = str(safari_db_path)
        cleanup = True
    else:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = tmp.name
        shutil.copy2(str(safari_db_path), tmp_path)
        cleanup = True

    try:
        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("""
            SELECT
                hi.url,
                hi.domain_expansion as domain,
                hi.visit_count,
                hv.title,
                hv.visit_time as visit_apple_epoch,
                hv.load_successful
            FROM history_visits hv
            JOIN history_items hi ON hv.history_item = hi.id
            WHERE hv.load_successful = 1
              AND hv.visit_time IS NOT NULL
            ORDER BY hv.visit_time DESC
        """)
        rows = cur.fetchall()
        conn.close()

        apple_epoch_offset = 978307200

        for row in rows:
            try:
                unix_ts = float(row["visit_apple_epoch"]) + apple_epoch_offset
                dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
                timestamp = dt.isoformat()
            except Exception:
                continue

            url = row["url"] or ""
            if not url:
                continue

            domain = row["domain"] or ""
            if not domain and url.startswith("http"):
                try:
                    from urllib.parse import urlparse
                    domain = urlparse(url).netloc.lstrip("www.")
                except Exception:
                    pass

            visits.append({
                "title": row["title"] or url,
                "url": url,
                "domain": domain,
                "timestamp": timestamp,
                "visit_count": row["visit_count"] or 1,
                "time_on_page_seconds": 0,
            })

    except Exception as e:
        log(f"Safari extraction error: {e}")
    finally:
        if cleanup:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    log(f"Extracted {len(visits)} Safari visits.")
    return visits


CHUNK_SIZE = 50  # episodes per POST (keeps payloads small and reliable)


def post_chunk(chunk, chunk_index, total_chunks):
    """POST a single chunk of episodes to the webhook."""
    payload = {
        "source_device_id": DEVICE_ID,
        "schema_version": 1,
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
        "podcasts": chunk,
        "browsing": [],
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = resp.status
            return status < 300, status
    except urllib.error.HTTPError as e:
        return False, e.code
    except Exception as e:
        return False, str(e)


def post_to_webhook(podcasts, browsing):
    """POST all data to LifeLog webhook in chunks."""
    if not podcasts:
        log("No podcasts to post.")
        return True

    chunks = [podcasts[i:i + CHUNK_SIZE] for i in range(0, len(podcasts), CHUNK_SIZE)]
    total = len(chunks)
    log(f"Posting {len(podcasts)} episodes in {total} chunks of up to {CHUNK_SIZE}...")

    success_count = 0
    for idx, chunk in enumerate(chunks):
        ok, status = post_chunk(chunk, idx, total)
        if ok:
            success_count += 1
            log(f"  Chunk {idx + 1}/{total}: OK (HTTP {status})")
        else:
            log(f"  Chunk {idx + 1}/{total}: FAILED (status {status})")
        if idx < total - 1:
            time.sleep(2)  # small delay between chunks

    log(f"Posted {success_count}/{total} chunks successfully.")
    return success_count == total


def main():
    log("=" * 60)
    log(f"LifeLog extraction started on {DEVICE_ID}")

    # Step 1: Try to trigger backup
    idb2 = find_idevicebackup2()
    if idb2:
        log(f"Found libimobiledevice at: {idb2}")
        trigger_backup(idb2)
    else:
        log("libimobiledevice not found -- using existing backup.")

    # Step 2: Find backup directory
    backup_dir = find_backup_dir()
    if not backup_dir:
        log("ERROR: No iPhone backup found. Connect iPhone and ensure backup runs.")
        sys.exit(1)

    # Step 3: Check encryption
    # If a password is configured, always treat as encrypted (Apple Devices app
    # backups may not set IsEncrypted in Manifest.plist reliably on first backup).
    encrypted = bool(BACKUP_PASSWORD) or is_backup_encrypted(backup_dir)
    if encrypted:
        log(f"Backup is encrypted -- will decrypt using stored password.")
        if not ensure_decrypt_lib():
            log("ERROR: Cannot decrypt backup without iphone_backup_decrypt library.")
            sys.exit(1)
    else:
        log("Backup is unencrypted -- reading directly.")

    # Step 4: Extract data
    podcasts = extract_podcasts(backup_dir, encrypted=encrypted)
    browsing = extract_safari(backup_dir, encrypted=encrypted)

    if not podcasts and not browsing:
        log("No data extracted. Exiting.")
        sys.exit(0)

    # Step 5: Post to webhook in chunks
    log(f"Posting {len(podcasts)} podcast episodes...")
    success = post_to_webhook(podcasts, browsing)

    if success:
        log("Data posted successfully.")
    else:
        log("Failed to post data. Check log and retry.")
        sys.exit(1)


if __name__ == "__main__":
    main()
