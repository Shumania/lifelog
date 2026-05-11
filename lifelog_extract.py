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

# -- Version ------------------------------------------------------------------
EXTRACTOR_VERSION = "2.1"
VERSIONS_API_URL  = "https://api.github.com/repos/Shumania/lifelog/contents/versions.json"
EXTRACTOR_API_URL = "https://api.github.com/repos/Shumania/lifelog/contents/lifelog_extract.py"
EXTRACTOR_INSTALL_PATH = Path(r"C:\ProgramData\LifeLog\lifelog_extract.py")

# -- Configuration (injected by installer) -----------------------------------
WEBHOOK_URL = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=2a1433f1fa487e647ee1d12c7f26a497"
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
STATE_FILE = Path(r"C:\ProgramData\LifeLog\last_backup_hash.txt")
CURSOR_FILE = Path(r"C:\ProgramData\LifeLog\last_podcast_cursor.txt")


def get_backup_hash(backup_dir):
    """Return a hash representing the current state of the backup (based on Manifest.db mtime + size)."""
    try:
        manifest = backup_dir / "Manifest.db"
        if not manifest.exists():
            manifest = backup_dir / "Manifest.plist"
        if manifest.exists():
            stat = manifest.stat()
            raw = f"{backup_dir}|{stat.st_mtime}|{stat.st_size}"
            return hashlib.md5(raw.encode()).hexdigest()
    except Exception:
        pass
    return None


def load_last_hash():
    """Load the last successfully posted backup hash."""
    try:
        if STATE_FILE.exists():
            return STATE_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None


def save_last_hash(h):
    """Save the current backup hash after a successful post."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(h, encoding="utf-8")
    except Exception:
        pass


def load_cursor():
    """Load the last-sent podcast cursor (max ZLASTDATEPLAYED Apple epoch)."""
    try:
        if CURSOR_FILE.exists():
            val = CURSOR_FILE.read_text(encoding="utf-8").strip()
            if val:
                return float(val)
    except Exception:
        pass
    return None


def save_cursor(value):
    """Save the cursor after a successful post."""
    try:
        CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
        CURSOR_FILE.write_text(str(value), encoding="utf-8")
    except Exception:
        pass


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
    """Extract and decrypt a file from an encrypted backup.
    
    If the file is a .sqlite, also extracts the -wal and -shm sidecar files
    into the same temp directory so SQLite WAL mode works correctly.
    Returns path to the main extracted file.
    """
    if not ensure_decrypt_lib():
        return None
    try:
        from iphone_backup_decrypt import EncryptedBackup
        backup = EncryptedBackup(backup_directory=str(backup_dir), passphrase=BACKUP_PASSWORD)

        # Use a temp directory so WAL sidecar can live alongside the main file
        tmp_dir = Path(tempfile.mkdtemp(prefix="lifelog_"))
        filename = Path(relative_path).name
        tmp_path = tmp_dir / filename

        backup.extract_file(
            relative_path=relative_path,
            output_filename=str(tmp_path),
            domain_like=domain
        )

        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None

        main_size = tmp_path.stat().st_size
        log(f"Extracted {filename}: {main_size // (1024*1024)}MB")

        # Also try to extract WAL and SHM sidecars (SQLite WAL mode)
        if relative_path.endswith(".sqlite"):
            for suffix in ["-wal", "-shm"]:
                sidecar_rel = relative_path + suffix
                sidecar_path = tmp_dir / (filename + suffix)
                try:
                    backup.extract_file(
                        relative_path=sidecar_rel,
                        output_filename=str(sidecar_path),
                        domain_like=domain
                    )
                    if sidecar_path.exists() and sidecar_path.stat().st_size > 0:
                        log(f"Also extracted {filename + suffix}: {sidecar_path.stat().st_size // (1024*1024)}MB")
                    else:
                        sidecar_path.unlink(missing_ok=True)
                except Exception:
                    pass  # WAL not present is fine

        return tmp_path
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


def extract_podcasts(backup_dir, encrypted=False, cursor=None):
    # Note: domain uses wildcard to match any group ID prefix (e.g. 243LU875E5.groups.com.apple.podcasts)
    """Extract Apple Podcasts listening history from backup.

    cursor: optional float — Apple epoch value of the last episode we already sent.
            Only episodes with ZLASTDATEPLAYED > cursor are returned.
            Returns (episodes, max_apple_epoch) tuple.
    """
    episodes = []
    max_apple_epoch = cursor  # will be updated as we find newer episodes

    # Primary: AppDomainGroup, Library/Application Support path (confirmed working)
    podcast_db_path = get_file_from_backup(
        backup_dir,
        "%groups.com.apple.podcasts",
        "Library/Application Support/com.apple.podcasts/MTLibrary.sqlite",
        encrypted=encrypted
    )

    if not podcast_db_path:
        # Fallback: Documents path (older iOS versions)
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
    tmp_dir_to_clean = None
    if encrypted:
        tmp_path = str(podcast_db_path)
        # podcast_db_path is inside a temp dir created by get_file_from_encrypted_backup
        # clean up the whole dir (includes -wal and -shm sidecars)
        tmp_dir_to_clean = podcast_db_path.parent
        cleanup = True
    else:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = tmp.name
        shutil.copy2(str(podcast_db_path), tmp_path)
        cleanup = True

    try:
        # Diagnostic: check WAL sidecar presence
        wal_path = tmp_path + "-wal"
        shm_path = tmp_path + "-shm"
        if os.path.exists(wal_path):
            log(f"WAL sidecar found: {os.path.getsize(wal_path) // (1024*1024)}MB")
        else:
            log("WAL sidecar NOT found alongside podcast DB")
        log(f"SHM sidecar exists: {os.path.exists(shm_path)}")

        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Diagnostic: total rows and date range before main query
        try:
            cur.execute("SELECT COUNT(*) as cnt, MAX(ZLASTDATEPLAYED) as mx, MIN(ZLASTDATEPLAYED) as mn FROM ZMTEPISODE WHERE ZLASTDATEPLAYED IS NOT NULL AND ZLASTDATEPLAYED > 0")
            diag = cur.fetchone()
            if diag and diag["cnt"]:
                apple_epoch_offset = 978307200
                mx_unix = float(diag["mx"]) + apple_epoch_offset
                mn_unix = float(diag["mn"]) + apple_epoch_offset
                log(f"DB range: {diag['cnt']} played episodes; {datetime.fromtimestamp(mn_unix, tz=timezone.utc).date()} to {datetime.fromtimestamp(mx_unix, tz=timezone.utc).date()}")
        except Exception as e:
            log(f"Diagnostic query failed: {e}")

        cursor_clause = f"AND e.ZLASTDATEPLAYED > {cursor}" if cursor else ""
        if cursor:
            log(f"Cursor active: only fetching episodes newer than Apple epoch {cursor:.0f}")
        cur.execute(f"""
            SELECT
                e.ZTITLE as title,
                e.ZWEBPAGEURL as episode_url,
                e.ZAUTHOR as author,
                e.ZDURATION as duration_seconds,
                e.ZPLAYHEAD as progress_seconds,
                e.ZHASBEENPLAYED as has_been_played,
                e.ZMARKASPLAYED as mark_as_played,
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
              {cursor_clause}
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

            # Track max epoch for cursor update
            raw_epoch = float(row["last_played_apple_epoch"])
            if max_apple_epoch is None or raw_epoch > max_apple_epoch:
                max_apple_epoch = raw_epoch

            duration = row["duration_seconds"] or 0
            progress = row["progress_seconds"] or 0
            # If playhead is 0 but episode was marked as played/completed, treat as 100%
            if progress == 0 and duration > 0:
                if row["has_been_played"] or row["mark_as_played"]:
                    progress = duration
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
                if tmp_dir_to_clean and tmp_dir_to_clean.exists():
                    shutil.rmtree(tmp_dir_to_clean, ignore_errors=True)
                else:
                    os.unlink(tmp_path)
            except Exception:
                pass

    log(f"Extracted {len(episodes)} podcast episodes (cursor={cursor}, max_epoch={max_apple_epoch}).")
    return episodes, max_apple_epoch


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


BATCH_SIZE = 200  # rows per SQL INSERT batch (server-side)


def post_to_webhook(podcasts, browsing):
    """POST data to LifeLog webhook in chunks of BATCH_SIZE."""
    if not podcasts:
        log("No podcasts to post.")
        return True

    total = len(podcasts)
    total_chunks = (total + BATCH_SIZE - 1) // BATCH_SIZE
    log(f"Posting {total} podcast episodes in {total_chunks} chunk(s) of up to {BATCH_SIZE}...")

    all_success = True
    for i in range(0, total, BATCH_SIZE):
        chunk = podcasts[i:i + BATCH_SIZE]
        chunk_num = i // BATCH_SIZE + 1
        log(f"Posting chunk {chunk_num}/{total_chunks} ({len(chunk)} episodes)...")

        payload = {
            "source_device_id": DEVICE_ID,
            "schema_version": 2,
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
            with urllib.request.urlopen(req, timeout=120) as resp:
                status = resp.status
                if status < 300:
                    log(f"Chunk {chunk_num}/{total_chunks} OK (HTTP {status}).")
                else:
                    log(f"Chunk {chunk_num}/{total_chunks} failed (HTTP {status}).")
                    all_success = False
        except urllib.error.HTTPError as e:
            log(f"Chunk {chunk_num}/{total_chunks} failed (HTTP {e.code}).")
            all_success = False
        except Exception as e:
            log(f"Chunk {chunk_num}/{total_chunks} failed: {e}")
            all_success = False

    if all_success:
        log("All chunks posted successfully.")
    else:
        log("Some chunks failed. Check log and retry.")
    return all_success


def save_to_file(podcasts, browsing, output_path):
    """Save all extracted data to a JSON file for manual upload."""
    payload = {
        "source_device_id": DEVICE_ID,
        "schema_version": 1,
        "chunk_index": 0,
        "total_chunks": 1,
        "podcasts": podcasts,
        "browsing": browsing,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"Saved {len(podcasts)} episodes to {output_path}")
    log("Upload this file to Tasklet chat to import your podcast history.")


DEBUG_WEBHOOK_URL = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"


def extract_podcasts_raw(backup_dir, encrypted=False, limit=50):
    """Extract raw podcast rows for debugging — no filtering, all fields."""
    tmp_path = None
    cleanup = False

    if encrypted:
        tmp_path = get_file_from_backup(
            backup_dir,
            "%groups.com.apple.podcasts",
            "Documents/MTLibrary.sqlite",
            encrypted=True
        )
        if not tmp_path:
            log("DEBUG: Could not extract podcasts DB from backup.")
            return []
        cleanup = True
    else:
        # unencrypted path not implemented for debug
        log("DEBUG: Unencrypted backup not supported in debug mode.")
        return []

    rows_out = []
    try:
        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(f"""
            SELECT
                e.ZTITLE as title,
                p.ZTITLE as show_name,
                e.ZLASTDATEPLAYED as last_played_raw,
                e.ZPLAYHEAD as playhead,
                e.ZDURATION as duration,
                e.ZHASBEENPLAYED as has_been_played,
                e.ZMARKASPLAYED as mark_as_played,
                e.ZPLAYCOUNT as play_count
            FROM ZMTEPISODE e
            LEFT JOIN ZMTPODCAST p ON e.ZPODCAST = p.Z_PK
            ORDER BY e.ZLASTDATEPLAYED DESC
            LIMIT {limit}
        """)
        apple_epoch_offset = 978307200
        for row in cur.fetchall():
            raw_ts = row["last_played_raw"]
            if raw_ts and float(raw_ts) > 0:
                unix_ts = float(raw_ts) + apple_epoch_offset
                dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
                played_at = dt.strftime("%Y-%m-%d %H:%M UTC")
            else:
                played_at = "never"
            rows_out.append({
                "title": row["title"] or "(no title)",
                "show": row["show_name"] or "(no show)",
                "played_at": played_at,
                "playhead_sec": row["playhead"],
                "duration_sec": row["duration"],
                "has_been_played": row["has_been_played"],
                "mark_as_played": row["mark_as_played"],
                "play_count": row["play_count"],
            })
        conn.close()
    except Exception as e:
        log(f"DEBUG extract error: {e}")
    finally:
        if cleanup and tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    return rows_out


def post_debug_report(rows):
    """Post raw debug rows to the PC output webhook as plain text."""
    lines = [f"=== PODCAST DEBUG REPORT from {DEVICE_ID} — top {len(rows)} most recent ===\n"]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i:3}. [{r['played_at']}] {r['show']} — {r['title']}\n"
            f"     playhead={r['playhead_sec']}s  duration={r['duration_sec']}s  "
            f"has_been_played={r['has_been_played']}  mark_as_played={r['mark_as_played']}  "
            f"play_count={r['play_count']}"
        )
    report = "\n".join(lines)
    log(report)

    payload = json.dumps({
        "computer": DEVICE_ID,
        "output": report,
    }).encode("utf-8")

    req = urllib.request.Request(
        DEBUG_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            log(f"Debug report posted (HTTP {status}).")
    except Exception as e:
        log(f"Failed to post debug report: {e}")


def check_extractor_version():
    """Check versions.json on GitHub; if extractor_version differs, self-update and re-exec."""
    import base64
    try:
        req = urllib.request.Request(VERSIONS_API_URL, headers={"User-Agent": "LifeLog-Extractor"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        content = base64.b64decode(data["content"].replace("\n", "")).decode("utf-8")
        versions = json.loads(content)
        latest = versions.get("extractor_version", EXTRACTOR_VERSION)

        if latest != EXTRACTOR_VERSION:
            log(f"Extractor update available: {EXTRACTOR_VERSION} -> {latest}. Downloading...")
            req2 = urllib.request.Request(EXTRACTOR_API_URL, headers={"User-Agent": "LifeLog-Extractor"})
            with urllib.request.urlopen(req2, timeout=30) as resp2:
                data2 = json.loads(resp2.read())
            new_content = base64.b64decode(data2["content"].replace("\n", "")).decode("utf-8")

            EXTRACTOR_INSTALL_PATH.parent.mkdir(parents=True, exist_ok=True)
            EXTRACTOR_INSTALL_PATH.write_text(new_content, encoding="utf-8")
            log(f"Saved updated extractor to {EXTRACTOR_INSTALL_PATH}. Re-running...")

            import subprocess
            result = subprocess.run([sys.executable, str(EXTRACTOR_INSTALL_PATH)] + sys.argv[1:])
            sys.exit(result.returncode)
        else:
            log(f"Extractor version OK (v{EXTRACTOR_VERSION}).")
    except Exception as e:
        log(f"Version check failed (running anyway): {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="LifeLog iPhone Backup Extractor")
    parser.add_argument("--output", metavar="FILE", help="Save extracted data to a JSON file instead of posting to webhook")
    parser.add_argument("--debug", action="store_true", help="Post raw top-50 podcast rows to debug webhook instead of ingesting")
    args = parser.parse_args()

    log("=" * 60)
    log(f"LifeLog extraction started on {DEVICE_ID} (extractor v{EXTRACTOR_VERSION})")
    check_extractor_version()

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
    encrypted = bool(BACKUP_PASSWORD) or is_backup_encrypted(backup_dir)
    if encrypted:
        log(f"Backup is encrypted -- will decrypt using stored password.")
        if not ensure_decrypt_lib():
            log("ERROR: Cannot decrypt backup without iphone_backup_decrypt library.")
            sys.exit(1)
    else:
        log("Backup is unencrypted -- reading directly.")

    # Debug mode: dump raw rows and exit
    if args.debug:
        log("DEBUG MODE: extracting raw top-50 episodes...")
        raw_rows = extract_podcasts_raw(backup_dir, encrypted=encrypted, limit=50)
        if not raw_rows:
            log("DEBUG: No rows returned.")
        else:
            post_debug_report(raw_rows)
        sys.exit(0)

    # Step 4: Check if backup has changed since last successful post
    current_hash = get_backup_hash(backup_dir)
    last_hash = load_last_hash()
    if not args.output and current_hash and current_hash == last_hash:
        log("Backup unchanged since last sync. Nothing to do.")
        sys.exit(0)

    # Step 5: Load cursor and extract data
    cursor = load_cursor()
    if cursor:
        log(f"Podcast cursor loaded: Apple epoch {cursor:.0f} — only fetching newer episodes.")
    else:
        log("No podcast cursor found — fetching all episodes (first run).")

    podcasts, max_epoch = extract_podcasts(backup_dir, encrypted=encrypted, cursor=cursor)
    browsing = extract_safari(backup_dir, encrypted=encrypted)

    if not podcasts and not browsing:
        log("No new data to send (cursor up to date or nothing extracted).")
        # Still update hash so we don't re-decrypt on every run
        if current_hash:
            save_last_hash(current_hash)
        sys.exit(0)

    # Step 6: Save to file or post to webhook
    if args.output:
        save_to_file(podcasts, browsing, args.output)
    else:
        log(f"Posting {len(podcasts)} podcast episodes...")
        success = post_to_webhook(podcasts, browsing)
        if success:
            log("Data posted successfully.")
            if current_hash:
                save_last_hash(current_hash)
                log("Backup hash saved.")
            if max_epoch is not None:
                save_cursor(max_epoch)
                log(f"Podcast cursor saved: Apple epoch {max_epoch:.0f} (next run will skip these).")
        else:
            log("Failed to post data. Check log and retry.")
            sys.exit(1)


if __name__ == "__main__":
    main()
