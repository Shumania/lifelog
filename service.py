#!/usr/bin/env python3
"""
LifeLog Unified Service v1.0
Replaces LifeLog-BackupService.ps1 + sonos_service.py

Modules (set in lifelog_config.json):
  - sonos:  real-time Sonos listening history + remote control
  - backup: periodic iPhone backup extraction (calls lifelog_extract.py)
  - dev:    GitHub dev_next.ps1 remote-control loop

Config: C:\\ProgramData\\LifeLog\\lifelog_config.json
{
  "house": "caphill",
  "modules": ["sonos", "backup", "dev"],
  "github_token": ""    <- optional, raises API rate limit 60->5000/hr
}
Falls back to sonos_config.json if lifelog_config.json not found.
"""

import sys
import io
# Force UTF-8 on Windows to avoid charmap codec errors with emoji in logs
# [ROLLBACK-UNSAFE] This wrapper runs before any new version loads. A crash here
# (e.g. encoding error) kills the process before self-update can even start.
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
import json
import time
import hashlib
import base64
import os
import threading
import subprocess
import traceback
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

if sys.version_info < (3, 8):
    print("ERROR: Python 3.8+ required")
    sys.exit(1)

# [ROLLBACK-UNSAFE] _ensure + requests import: runs at module load before any update.
# If pip or import fails here, service can't reach GitHub to self-update.
def _ensure(pkg, import_as=None):
    try:
        __import__(import_as or pkg)
    except ImportError:
        print(f"Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

_ensure("requests")
import requests

# --- CONSTANTS --------------------------------------------------------------
# [ROLLBACK-UNSAFE] SERVICE_VERSION and all constants below are baked into the running
# process. The old version's SERVICE_VERSION is compared against versions.json to decide
# whether to self-update. Wrong GITHUB_API_BASE or WEBHOOK here = update can't download/report.
# IMPORTANT: versions.json key MUST be "service_version" (not "service" or "version").
# Mismatch = silent update failure. See v1.83 postmortem.
SERVICE_VERSION = "2.19"
_mutex_handle   = None   # set in main(); released in self_update_check() before handoff
INSTALL_DIR     = Path(r"C:\ProgramData\LifeLog")
WEBHOOK         = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=be22b43febe39260b284d21672db539f"
DEV_WEBHOOK     = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
GITHUB_OWNER    = "Shumania"
GITHUB_REPO     = "lifelog"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents"

NTFY_TOPICS = {
    "caphill": "lifelog-cmd-caphill-4x8m",
    "vashon":  "lifelog-cmd-vashon-9k3p",
}

# ntfy topics for real-time UI push (browser SSE)
NTFY_UI_TOPICS = {
    "caphill": "lifelog-ui-caphill-b1f1ef",
    "vashon":  "lifelog-ui-vashon-b84d1d",
}

# WiFi SSID -> house mapping (overrides config file setting)
WIFI_HOUSE_MAP = {
    "shumickernet": "caphill",
    "coconetz":     "vashon",
}

# [ROLLBACK-UNSAFE] Called at module level during startup.
def detect_house_from_wifi():
    """Detect current house by checking connected WiFi SSID. Returns house string or None."""
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.lower().startswith("ssid") and "bssid" not in line.lower():
                ssid = line.split(":", 1)[-1].strip()
                house = WIFI_HOUSE_MAP.get(ssid)
                if house:
                    return house, ssid
                # case-insensitive fallback
                for k, v in WIFI_HOUSE_MAP.items():
                    if k.lower() == ssid.lower():
                        return v, ssid
    except Exception:
        pass
    return None, None

POLL_INTERVAL              = 15    # Sonos poll (s)
CMD_POLL_EVERY             = 20    # GitHub cmd fallback every N Sonos cycles (~5 min)
HEARTBEAT_FALLBACK_SECS    = 3600  # keepalive heartbeat every 60 min (only if no real POST)
HEARTBEAT_QUIET_SLEEP      = 1800  # 30 min retry during quiet hours
ACTIVITY_WINDOW            = 7200  # "active" if Sonos track in last 2h
VERSION_CHECK_INTERVAL     = 3600  # 60 min
BACKUP_INTERVAL            = 3600  # run extract every 60 min
DEV_POLL_INTERVAL          = 100   # dev_next.ps1 poll (s)
OFFLINE_THRESHOLD          = 3
OFFLINE_RECHECK_SECS       = 300
BATCH_SIZE                 = 20    # flush buffer when this many tracks queued
BATCH_TRAILING_SECS        = 1800  # flush 30 min after last track was added
BUFFER_MAX_AGE_SECS        = 30    # (LEGACY — buffer_monitor_thread removed in v1.83; kept for reference)
STATE_PUSH_DEBOUNCE_S      = 5     # debounce window for state.json push
STATE_RING_MAX             = 30    # max items in state file ring buffer

# --- CONFIG -----------------------------------------------------------------
# [ROLLBACK-UNSAFE] load_config() runs at module level. If it crashes (bad JSON,
# missing file, encoding), the service never starts and can't self-update.
def load_config():
    for p in [INSTALL_DIR / "lifelog_config.json", INSTALL_DIR / "sonos_config.json"]:
        if p.exists():
            try:
                cfg = json.loads(p.read_text(encoding="utf-8-sig"))
                h = cfg.get("house", "").strip().lower()
                if h not in ("caphill", "vashon"):
                    print(f"WARNING: house must be caphill or vashon (got {h!r}), defaulting to caphill")
                    h = "caphill"
                cfg["house"] = h
                # WiFi override: more reliable than manually set config value
                wifi_house, wifi_ssid = detect_house_from_wifi()
                if wifi_house and wifi_house != h:
                    print(f"WiFi '{wifi_ssid}' -> overriding house: {h!r} -> {wifi_house!r}")
                    cfg["house"] = wifi_house
                    h = wifi_house
                elif wifi_house:
                    print(f"WiFi '{wifi_ssid}' confirms house: {wifi_house!r}")
                else:
                    print(f"WiFi not detected -- using config house: {h!r}")
                if "modules" not in cfg:
                    cfg["modules"] = ["sonos", "backup", "dev"]
                cfg["ntfy_topic"] = NTFY_TOPICS.get(cfg["house"], NTFY_TOPICS["caphill"])
                cfg["ntfy_ui_topic"] = NTFY_UI_TOPICS.get(cfg["house"], NTFY_UI_TOPICS["caphill"])
                # sonos_commander: this machine executes unaddressed Sonos commands
                # Set False on non-primary machines sharing the same house network
                if "sonos_commander" not in cfg:
                    cfg["sonos_commander"] = True
                return cfg
            except Exception as e:
                print(f"Config parse error ({p}): {e}")
    print("WARNING: No config found. Using defaults.")
    return {"house": "caphill", "modules": ["sonos", "backup", "dev"],
            "ntfy_topic": NTFY_TOPICS["caphill"],
            "ntfy_ui_topic": NTFY_UI_TOPICS["caphill"]}

config          = load_config()
house           = config["house"]
modules         = config["modules"]
ntfy_topic      = config["ntfy_topic"]
ntfy_ui_topic   = config.get("ntfy_ui_topic", "") or NTFY_UI_TOPICS.get(house, "")
print(f"[init] ntfy_ui_topic resolved to: {ntfy_ui_topic!r} (house={house!r})")
gh_token        = config.get("github_token", "")
computer        = os.environ.get("COMPUTERNAME", house)
sonos_commander = config.get("sonos_commander", True)
client_id       = f"lifelog_{computer.lower()}"   # canonical ID used in heartbeats

# --- ACTIVE HOURS -----------------------------------------------------------
def seattle_hour():
    """Return current hour in Seattle time (America/Los_Angeles)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).hour
    except (ImportError, Exception):
        try:
            import pytz
            return datetime.now(pytz.timezone("America/Los_Angeles")).hour
        except ImportError:
            # Fallback: approximate UTC-7 (PDT) / UTC-8 (PST)
            utc_hour = datetime.now(timezone.utc).hour
            utc_month = datetime.now(timezone.utc).month
            offset = -7 if 3 <= utc_month <= 11 else -8
            return (utc_hour + offset) % 24

def is_active_hours():
    """Returns True if Seattle time is 7 AM-10 PM."""
    return 7 <= seattle_hour() < 22

# --- GLOBAL SONOS STATE -----------------------------------------------------
current_devices_by_name  = {}
room_state               = {}
_last_ui_track           = {}   # coordinator -> track_key; for ntfy UI dedup
_last_sse_rooms_playing  = []   # for change detection on status_update SSE
_last_sse_mute_states    = {}   # for change detection on mute toggle
_sse_status_counter      = 0    # emit status_update every N poll cycles
_current_play_modes      = {}   # room -> play_mode (NORMAL, REPEAT_ALL, REPEAT_ONE, SHUFFLE, etc.)
_current_mute_states     = {}   # room -> bool (True=muted)
speaker_failures         = {}
speaker_offline_since    = {}
_offline_ips             = {}   # ip -> epoch timestamp; skip timed-out speakers
last_cmd_sha             = None
executed_cmd_hashes      = {}   # hash -> timestamp (TTL-based dedup)
CMD_DEDUP_TTL_SECONDS    = 60   # hashes expire after 60s -- covers ntfy replay window
last_sonos_activity_ts   = 0.0  # updated when a track is buffered
last_track_added_ts      = 0.0  # updated when a track is added to buffer
last_post_ts             = 0.0  # updated whenever any POST succeeds
pending_buffer           = []   # tracks waiting to be flushed
pending_buffer_lock      = threading.Lock()
PENDING_PATH             = INSTALL_DIR / "pending_history.json"
STATE_RING_PATH          = INSTALL_DIR / "state_ring_buffer.json"

# --- GITHUB STATE PUSH (real-time state.json for cross-device UX) -----------
# DESIGN NOTE: Pushes a small state-{house}.json to GitHub after each track change.
# Browser loads this on cold start for instant cross-device now-playing and recent tracks.
# Debounced: rapid skip/skip/skip collapses to one push. Non-fatal: music always plays.
_state_ring_buffer       = []     # recent tracks ring buffer (in-memory + disk-persisted)
_state_push_timer        = None   # threading.Timer for debounced push
_state_push_sha          = None   # last known SHA of state-{house}.json (avoid extra GET)
_state_push_count        = 0      # total pushes since startup (diagnostic)
_state_push_lock         = threading.Lock()

def _load_state_ring_buffer():
    """Load ring buffer from disk (crash recovery)."""
    global _state_ring_buffer
    try:
        if STATE_RING_PATH.exists():
            _state_ring_buffer = json.loads(STATE_RING_PATH.read_text(encoding="utf-8"))
            log(f"[state] Loaded {len(_state_ring_buffer)} items from ring buffer")
    except Exception as e:
        log(f"[state] Failed to load ring buffer: {e}")
        _state_ring_buffer = []

def _persist_state_ring_buffer():
    """Save ring buffer to disk for crash safety."""
    try:
        STATE_RING_PATH.write_text(json.dumps(_state_ring_buffer, ensure_ascii=True), encoding="utf-8")
    except Exception as e:
        log(f"[state] Failed to persist ring buffer: {e}")

def _retire_to_state_ring(track_info, rooms_list, started_at=None):
    """Add a completed track to the state ring buffer."""
    entry = {
        "title": track_info.get("title", ""),
        "artist": track_info.get("artist", ""),
        "album": track_info.get("album", ""),
        "rooms": ", ".join(rooms_list) if isinstance(rooms_list, list) else str(rooms_list),
        "service": track_info.get("service", ""),
        "uri": track_info.get("uri", ""),
        "timestamp": (started_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _state_ring_buffer.insert(0, entry)
    while len(_state_ring_buffer) > STATE_RING_MAX:
        _state_ring_buffer.pop()
    _persist_state_ring_buffer()

def _build_state_payload():
    """Build the state-{house}.json payload from current live state."""
    # Now playing: derive from room_state (same data source as SSE)
    np = None
    rp = get_rooms_playing()
    for coord_name, rs in room_state.items():
        if rs and rs.get("track_info"):
            ti = rs["track_info"]
            rooms = ti.get("rooms", [coord_name])
            np = {
                "title": ti.get("title", ""),
                "artist": ti.get("artist", ""),
                "album": ti.get("album", ""),
                "rooms": rooms,
                "service": ti.get("service", ""),
                "uri": ti.get("uri", ""),
                "play_modes": dict(_current_play_modes),
                "timestamp": now_iso(),
            }
            break  # first active coordinator

    # rooms_paused = speakers in PAUSED_PLAYBACK that aren't actively playing
    rp_paused = sorted(
        name for name, st in _last_transport_states.items()
        if st == "PAUSED_PLAYBACK" and name not in rp
    )

    return {
        "house": house,
        "last_updated": now_iso(),
        "now_playing": np,
        "rooms_playing": rp,
        "rooms_paused": rp_paused,
        "rooms_all": sorted(current_devices_by_name.keys()),
        "recent_tracks": list(_state_ring_buffer),
    }

def _do_state_push():
    """Push state-{house}.json to GitHub. Two API calls: GET SHA + PUT content."""
    global _state_push_sha, _state_push_count
    if not gh_token:
        return  # skip without PAT (60 req/hr too tight for this)
    try:
        payload = _build_state_payload()
        content_json = json.dumps(payload, ensure_ascii=True, separators=(',', ':'))
        content_b64 = base64.b64encode(content_json.encode("utf-8")).decode("ascii")
        filename = f"state-{house}.json"
        url = f"{GITHUB_API_BASE}/{filename}"
        headers = gh_headers()

        # GET current SHA (needed for update; use cached SHA if available)
        sha = _state_push_sha
        if not sha:
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 200:
                    sha = r.json().get("sha")
                elif r.status_code == 404:
                    sha = None  # file doesn't exist yet, will create
                else:
                    log(f"[state] GET SHA failed: HTTP {r.status_code}")
                    return
            except Exception as e:
                log(f"[state] GET SHA error: {e}")
                return

        # PUT updated content
        body = {"message": "state", "content": content_b64}
        if sha:
            body["sha"] = sha
        try:
            r = requests.put(url, headers=headers, json=body, timeout=15)
            if r.status_code in (200, 201):
                _state_push_sha = r.json().get("content", {}).get("sha")
                _state_push_count += 1
                np_title = payload.get("now_playing", {}).get("title", "none") if payload.get("now_playing") else "none"
                log(f"[state] Pushed state-{house}.json (#{_state_push_count}, np={np_title}, ring={len(_state_ring_buffer)})")
            elif r.status_code == 409:
                # SHA conflict -- clear cached SHA so next push re-fetches
                _state_push_sha = None
                log(f"[state] SHA conflict on push -- will retry next change")
            else:
                log(f"[state] PUT failed: HTTP {r.status_code}")
                _state_push_sha = None  # force re-fetch
        except Exception as e:
            log(f"[state] PUT error: {e}")
            _state_push_sha = None
    except Exception as e:
        log(f"[state] Push error: {e}")

def schedule_state_push():
    """Debounced state push. Resets timer on each call; fires after STATE_PUSH_DEBOUNCE_S."""
    global _state_push_timer
    with _state_push_lock:
        if _state_push_timer:
            _state_push_timer.cancel()
        _state_push_timer = threading.Timer(STATE_PUSH_DEBOUNCE_S, _do_state_push)
        _state_push_timer.daemon = True
        _state_push_timer.start()

# --- DIAGNOSTIC STATE -------------------------------------------------------
_service_start_ts        = 0.0
_last_command_at         = 0.0
_last_command_action     = ""
_last_command_source     = ""
_commands_received_count = 0
_track_changes           = []   # ring buffer of last 10 track changes [{room, at, track, commanded}]
_ntfy_connected          = False
_ntfy_reconnects         = 0
_last_transport_states   = {}   # room -> state string (updated by get_rooms_playing)
_prev_diag_fingerprint   = ""   # for change detection

# --- UTILITIES --------------------------------------------------------------
# [ROLLBACK-UNSAFE] log(), gh_headers(), gh_get(), gh_decode() are all called by
# self_update_check(). A non-ASCII char in log() crashed v1.44. Keep these ASCII-clean.
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

LOG_FILE        = INSTALL_DIR / "lifelog_service.log"
_log_write_count = 0
_log_lock        = threading.Lock()

# --- IN-MEMORY LOG RING BUFFER -----------------------------------------------
# DESIGN NOTE: Captures recent log lines so they can ride along on webhook POSTs.
# Last 50 lines included in every heartbeat; full 200-line buffer available via
# 'get_logs' ntfy command. Zero file I/O overhead (appends to deque only).
# ensure_ascii=True used when serializing to avoid cp1252 encoding issues on Windows.
_LOG_RING_MAX    = 500
_log_ring        = deque(maxlen=_LOG_RING_MAX)
_log_ring_lock   = threading.Lock()

# Error-only ring buffer — persists much longer than general logs since errors
# are infrequent. Captures lines containing ERROR, FAIL, Traceback, Exception,
# or similar keywords. Sent alongside recent_logs in every heartbeat.
_ERROR_RING_MAX  = 100
_error_ring      = deque(maxlen=_ERROR_RING_MAX)
_error_ring_lock = threading.Lock()
_ERROR_KEYWORDS  = ("ERROR", "FAIL", "Traceback", "Exception", "CRITICAL", "crash", "UPnP", "HTTP 4", "HTTP 5", "timed out", "refused")

# Command results ring buffer — structured outcomes for agent-side correlation
_CMD_RESULTS_MAX = 20
_command_results = deque(maxlen=_CMD_RESULTS_MAX)
_command_results_lock = threading.Lock()

def record_command_result(action, success, message, cmd_ts=None, detail=None):
    """Append a structured command outcome to the ring buffer."""
    entry = {
        "action": action,
        "status": "ok" if success else "error",
        "message": message,
        "at": now_iso(),
    }
    if cmd_ts:
        entry["cmd_ts"] = cmd_ts
    if detail:
        entry["detail"] = detail
    with _command_results_lock:
        _command_results.append(entry)

def get_command_results():
    """Return recent command results for embedding in heartbeats."""
    with _command_results_lock:
        return list(_command_results)

def _rotate_log_if_needed():
    """Trim log file to last 800 lines if it exceeds 500 KB."""
    try:
        if LOG_FILE.stat().st_size > 500_000:
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            LOG_FILE.write_text("\n".join(lines[-800:]) + "\n", encoding="utf-8")
    except Exception:
        pass

def log(msg):
    global _log_write_count
    line = f"[{now_iso()}] {msg}"
    print(line, flush=True)
    # Append to in-memory ring buffer (lock-free deque is thread-safe for appends,
    # but we use a lock for the snapshot reads in get_recent_logs/get_full_logs)
    with _log_ring_lock:
        _log_ring.append(line)
    # Also capture to error ring if line matches any error keyword
    if any(kw in msg for kw in _ERROR_KEYWORDS):
        with _error_ring_lock:
            _error_ring.append(line)
    try:
        with _log_lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            _log_write_count += 1
            if _log_write_count % 500 == 0:
                _rotate_log_if_needed()
    except Exception:
        pass


def get_recent_logs(n=50):
    """Return last n log lines from ring buffer (for embedding in heartbeats)."""
    with _log_ring_lock:
        lines = list(_log_ring)
    return lines[-n:]


def get_full_logs():
    """Return all log lines in ring buffer (for on-demand get_logs command)."""
    with _log_ring_lock:
        return list(_log_ring)

def get_recent_errors(n=50):
    """Return last n error lines from dedicated error ring buffer."""
    with _error_ring_lock:
        lines = list(_error_ring)
    return lines[-n:]

def gh_headers():
    h = {"Accept": "application/vnd.github.v3+json", "User-Agent": "LifeLog-Service"}
    if gh_token:
        h["Authorization"] = f"token {gh_token}"
    return h

def gh_get(path, retries=1):
    """GET a file from GitHub API. Returns response object or None. Retries once on failure."""
    url = f"{GITHUB_API_BASE}/{path}"
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=gh_headers(), timeout=15)
            if r.status_code == 200:
                return r
            log(f"gh_get {path}: HTTP {r.status_code}{' (retrying)' if attempt < retries else ''}")
        except Exception as e:
            log(f"gh_get error ({path}): {e}{' (retrying)' if attempt < retries else ''}")
        if attempt < retries:
            time.sleep(30)
    return None

def gh_decode(r):
    """Decode base64 GitHub API file content."""
    b64 = r.json().get("content", "").replace("\n", "")
    return base64.b64decode(b64).decode("utf-8")

# --- ERROR REPORTING --------------------------------------------------------
# [ROLLBACK-UNSAFE] post_error() is called by self_update_check() on failure.
_last_error_post = {}  # module -> timestamp of last posted error
ERROR_THROTTLE_SECONDS = 1800  # 30 minutes

def post_error(message, context="", module="service"):
    """POST error to webhook so agent sees it immediately. Throttled: same module suppressed for 30 min."""
    now = time.time()
    last = _last_error_post.get(module, 0)
    if now - last < ERROR_THROTTLE_SECONDS:
        log(f"[error-throttle] Suppressed {module} error (last posted {int(now - last)}s ago)")
        return
    _last_error_post[module] = now
    payload = {
        "type":     "sonos_error",
        "house":    house,
        "computer": computer,
        "message":  message,
        "context":  str(context)[:500],
        "module":   module,
        "version":  SERVICE_VERSION,
        "timestamp": now_iso(),
        "recent_logs": get_recent_logs(100),
        "recent_errors": get_recent_errors(50),
    }
    try:
        requests.post(WEBHOOK, json=payload, timeout=10)
    except Exception:
        pass

# --- SELF-UPDATE ------------------------------------------------------------
# [ROLLBACK-UNSAFE] *** MOST CRITICAL SECTION ***
# This entire function runs in the OLD version. It downloads the new file, overwrites
# itself, releases the mutex, spawns the new process, and monitors for crash-rollback.
# ANY bug here (encoding, syntax, logic) runs in the currently deployed code, NOT the
# new version. The v1.44 crash was caused by a non-ASCII arrow in a log() call here.
# Rules: (1) 100% ASCII, (2) wrap in try/except, (3) test with OLD version in mind.
def self_update_check():
    """Check versions.json; download + restart if service_version changed."""
    import ast as _ast
    try:
        r = gh_get("versions.json", retries=1)
        if not r:
            log("Version check: GitHub unavailable (will retry next cycle)")
            return
        versions = json.loads(gh_decode(r))
        # KEY MUST BE "service_version" — not "service", not "version".
        # If the key is missing, versions.json is malformed. Log a loud warning
        # so silent fallback never hides a broken update again (v1.83 root cause).
        if "service_version" not in versions:
            log(f"[!] versions.json MISSING 'service_version' key! Keys found: {list(versions.keys())}. Update check SKIPPED.")
            return
        latest = versions["service_version"]
        log(f"Version check: GitHub={latest} running={SERVICE_VERSION}")
        if latest == SERVICE_VERSION:
            # Clear any skip_version file if we are now running the target version
            # (e.g. user deployed it via installer)
            skip_path = Path(sys.argv[0]).resolve().parent / "skip_version"
            if skip_path.exists():
                skip_path.unlink(missing_ok=True)
                log("Cleared skip_version (now running target)")
            return
        # -- Skip-version guard: don't retry a version that already crashed --
        skip_path = Path(sys.argv[0]).resolve().parent / "skip_version"
        if skip_path.exists():
            try:
                skip_data = skip_path.read_text(encoding="utf-8").strip()
                # Format: "version|fail_count"
                parts = skip_data.split("|")
                skip_ver = parts[0]
                skip_count = int(parts[1]) if len(parts) > 1 else 1
                if skip_ver == latest:
                    if skip_count >= 2:
                        log(f"Skipping v{latest}: crashed {skip_count}x. Manual restart or new version required.")
                        return
                    else:
                        log(f"Retrying v{latest} (attempt {skip_count + 1}/2)")
                # Different version on GitHub now -- clear the skip file
                elif skip_ver != latest:
                    skip_path.unlink(missing_ok=True)
                    log(f"Cleared skip_version (was {skip_ver}, now trying {latest})")
            except Exception as _se:
                log(f"Warning: bad skip_version file, removing: {_se}")
                skip_path.unlink(missing_ok=True)
        log(f"Update: v{SERVICE_VERSION} -> v{latest}. Downloading...")
        r2 = gh_get("lifelog_service.py", retries=1)
        if not r2:
            log("Download failed -- will retry next cycle")
            post_error(f"Failed to download update v{latest}", module="update")
            return
        new_code = gh_decode(r2)
        # Sanity checks before overwriting
        if len(new_code) < 10_000:
            log(f"Update aborted: downloaded file too small ({len(new_code)} bytes) -- likely partial")
            post_error(f"Update v{latest} aborted: file too small ({len(new_code)} bytes)", module="update")
            return
        try:
            _ast.parse(new_code)
        except SyntaxError as se:
            log(f"Update aborted: syntax error in downloaded v{latest}: {se}")
            post_error(f"Update v{latest} aborted: syntax error: {se}", module="update")
            return
        this_path = Path(sys.argv[0]).resolve()
        bak_path = this_path.with_suffix(".py.bak")
        tmp_path = this_path.with_suffix(".py.tmp")
        flag_dir = this_path.parent
        # Save backup of current working version before overwriting
        try:
            import shutil
            shutil.copy2(str(this_path), str(bak_path))
            (flag_dir / "update_in_progress").write_text(
                f"{SERVICE_VERSION}|{latest}", encoding="utf-8"
            )
            log(f"Saved backup: {bak_path}")
        except Exception as be:
            log(f"Warning: couldn't save backup: {be}")
        # Atomic write: write to .tmp then os.replace() -- no partial files
        tmp_path.write_text(new_code, encoding="utf-8")
        os.replace(str(tmp_path), str(this_path))
        log(f"Updated to v{latest} -- restarting in new window...")
        # Release the single-instance mutex BEFORE spawning so the new process
        # can acquire it immediately (avoids race where new process starts fast,
        # sees ERROR_ALREADY_EXISTS, and exits with "another instance running").
        global _mutex_handle
        if _mutex_handle is not None:
            try:
                import ctypes as _ctypes
                _ctypes.windll.kernel32.CloseHandle(_mutex_handle)
                _mutex_handle = None
                log("Mutex released for handoff to new process")
            except Exception as _me:
                log(f"Warning: couldn't release mutex: {_me}")
        child = subprocess.Popen(
            [sys.executable, str(this_path)] + sys.argv[1:],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        )
        # Monitor the child for 15 seconds -- if it crashes fast, rollback here
        import time as _t
        _t.sleep(15)
        if child.poll() is not None and child.returncode != 0:
            log(f"NEW VERSION CRASHED (exit {child.returncode}) -- rolling back!")
            try:
                import shutil as _sh
                _sh.copy2(str(bak_path), str(this_path))
                (flag_dir / "update_in_progress").unlink(missing_ok=True)
                (flag_dir / "update_started").unlink(missing_ok=True)
                bak_path.unlink(missing_ok=True)
                # Write skip_version to prevent retry loop
                skip_path = flag_dir / "skip_version"
                skip_count = 1
                if skip_path.exists():
                    try:
                        parts = skip_path.read_text(encoding="utf-8").strip().split("|")
                        if parts[0] == latest and len(parts) > 1:
                            skip_count = int(parts[1]) + 1
                    except Exception:
                        pass
                skip_path.write_text(f"{latest}|{skip_count}", encoding="utf-8")
                log(f"Rollback complete -- wrote skip_version={latest} (fail #{skip_count})")
                log("Restarting with previous version...")
                post_error(f"Update v{latest} crashed on startup (exit {child.returncode}). Rolled back to v{SERVICE_VERSION}. Fail #{skip_count}/2.", module="update")
                subprocess.Popen(
                    [sys.executable, str(this_path)] + sys.argv[1:],
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                )
            except Exception as _rbe:
                log(f"Rollback after spawn-crash failed: {_rbe}")
        os._exit(0)
    except Exception as e:
        log(f"Self-update error: {e}")
        post_error(f"Self-update error: {e}", module="update")

# --- HEARTBEAT HELPERS ------------------------------------------------------
def get_rooms_playing():
    """Query each known Sonos device for transport state, return list of rooms currently PLAYING.
    Also updates _current_play_modes with play mode per coordinator (NORMAL, REPEAT_ALL, etc.)."""
    global _current_play_modes, _current_mute_states
    if "sonos" not in modules:
        return []
    playing = []
    states = {}
    modes = {}
    mutes = {}
    for name, dev in current_devices_by_name.items():
        try:
            # Skip group members -- only query coordinators and solo speakers
            # Group members mirror coordinator state and can report stale PLAYING
            if dev.group and dev.group.coordinator and dev.group.coordinator != dev:
                states[name] = "GROUPED_MEMBER_SKIP"
                continue
            info = dev.get_current_transport_info()
            state = info.get("current_transport_state", "STOPPED")
            states[name] = state
            # Capture mute state for all coordinators (not just playing ones)
            try:
                mutes[name] = bool(dev.mute)
            except Exception:
                pass
            if state == "PLAYING":
                # Skip TV/line-in passthrough -- soundbars report PLAYING for external audio
                try:
                    track_uri = dev.get_current_track_info().get("uri", "")
                    pass  # URI checked for TV passthrough (diagnostic block shows details)
                    if track_uri.startswith(("x-sonos-htastream:", "x-rincon-stream:")):
                        states[name] = "PLAYING_TV"
                        continue
                except Exception:
                    pass  # If we can't check, include it
                # Capture play mode for this coordinator (repeat/shuffle status)
                try:
                    mode = dev.play_mode  # NORMAL, REPEAT_ALL, REPEAT_ONE, SHUFFLE, SHUFFLE_NOREPEAT, SHUFFLE_REPEAT_ONE
                    modes[name] = mode
                except Exception:
                    pass
                # Coordinator is playing -- add it and any genuinely grouped members.
                # Only include members whose coordinator IP matches this device,
                # to avoid stale group topology reporting ungrouped speakers.
                playing.append(name)
                if dev.group:
                    try:
                        coord_ip = dev.ip_address
                        for member in dev.group.members:
                            mname = member.player_name
                            if mname == name:
                                continue  # already added
                            # Verify this member still considers our device its coordinator
                            try:
                                mc = member.group.coordinator if member.group else None
                                if mc and mc.ip_address == coord_ip:
                                    playing.append(mname)
                                    if mname not in current_devices_by_name:
                                        current_devices_by_name[mname] = member
                            except Exception:
                                pass  # skip members we can't verify
                    except Exception as e:
                        log(f"[rooms_playing] group check error for {name}: {e}")
        except Exception as e:
            states[name] = f"ERROR:{e}"
    _last_transport_states.clear()
    _last_transport_states.update(states)
    _current_play_modes = modes
    _current_mute_states = mutes
    return sorted(set(playing))


# --- DIAGNOSTIC STATUS BLOCK ------------------------------------------------
def _format_age(seconds):
    """Format age in human-readable form."""
    if seconds is None:
        return "never"
    s = int(seconds)
    if s < 60:
        return f"{s}s ago"
    elif s < 3600:
        return f"{s // 60}m ago"
    else:
        h = s // 3600
        m = (s % 3600) // 60
        return f"{h}h{m}m ago" if m else f"{h}h ago"


def _format_service(svc):
    """Short service label for display."""
    if not svc:
        return ""
    MAP = {"sonos_spotify": "Spotify", "sonos_apple_music": "Apple Music",
           "sonos_qobuz": "Qobuz", "sonos_tunein": "TuneIn", "sonos_radio": "Radio"}
    return MAP.get(svc, svc.replace("sonos_", "").title())


def build_status_snapshot():
    """Build structured diagnostic snapshot from current module state.
    Returns a dict suitable for JSON serialization (future webhook upload)
    or console formatting. Makes ZERO soco calls -- reads only from
    module-level state populated by the normal poll loop."""
    now = time.time()

    # Build room details from room_state + transport states
    active_rooms = []
    stopped_names = []
    grouped_members = set()  # rooms shown as part of a group (skip in stopped)

    all_rooms = sorted(set(room_state.keys()) | set(_last_transport_states.keys()))

    for room_name in all_rooms:
        rs = room_state.get(room_name)
        transport = _last_transport_states.get(room_name, "UNKNOWN")

        entry = {"room": room_name, "state": transport}

        if rs and rs.get("track_info"):
            ti = rs["track_info"]
            entry["track"] = ti.get("title", "")
            entry["artist"] = ti.get("artist", "")
            entry["album"] = ti.get("album", "")
            entry["service"] = ti.get("service", "")
            entry["uri"] = (ti.get("uri", "") or "")[:80]
            ctx = ti.get("container")
            if ctx and isinstance(ctx, dict) and ctx.get("container_name"):
                entry["container"] = ctx["container_name"]
            members = ti.get("rooms", [room_name])
            entry["rooms_in_group"] = members
            entry["coordinator"] = ti.get("coordinator", room_name)
            for m in members:
                if m != room_name:
                    grouped_members.add(m)

        if room_name in _current_play_modes:
            entry["play_mode"] = _current_play_modes[room_name]

        if transport in ("PLAYING", "PAUSED_PLAYBACK", "PLAYING_TV", "TRANSITIONING"):
            active_rooms.append(entry)
        elif transport == "GROUPED_MEMBER_SKIP":
            grouped_members.add(room_name)
        else:
            stopped_names.append(room_name)

    # Remove grouped members from stopped list (they are shown with their coordinator)
    stopped_names = [n for n in stopped_names if n not in grouped_members]

    # Buffer state
    with pending_buffer_lock:
        buf_count = len(pending_buffer)

    snapshot = {
        "diag_version": 1,
        "timestamp": now,
        "uptime_s": now - _service_start_ts if _service_start_ts else 0,
        "active_rooms": active_rooms,
        "stopped_rooms": stopped_names,
        "last_command": {
            "action": _last_command_action,
            "at": _last_command_at,
            "age_s": now - _last_command_at if _last_command_at else None,
            "source": _last_command_source,
        },
        "commands_total": _commands_received_count,
        "buffer": {
            "count": buf_count,
            "last_added_age_s": now - last_track_added_ts if last_track_added_ts else None,
            "last_post_age_s": now - last_post_ts if last_post_ts else None,
        },
        "sse": {
            "last_publish_age_s": now - _sse_last_send_ts if _sse_last_send_ts else None,
            "consecutive_429": _sse_consecutive_429,
            "backoff_remaining_s": max(0, int(_sse_backoff_until - now)) if _sse_backoff_until else 0,
            "topic": ntfy_ui_topic,
            "send_attempts": _sse_send_attempts,
        },
        "speakers": {
            "offline_names": list(speaker_offline_since.keys()),
            "offline_ips": len(_offline_ips),
        },
        "ntfy": {
            "connected": _ntfy_connected,
            "reconnects": _ntfy_reconnects,
        },
        "track_changes": list(_track_changes[-5:]),
    }

    # Check skip_version file
    skip_path = INSTALL_DIR / "skip_version"
    if skip_path.exists():
        try:
            snapshot["skip_version"] = skip_path.read_text(encoding="utf-8").strip()
        except Exception:
            snapshot["skip_version"] = "exists"

    return snapshot


def format_status_log(snapshot):
    """Render structured snapshot as pretty console text for local debugging."""
    lines = []

    # Header with Seattle time + uptime
    try:
        from zoneinfo import ZoneInfo
        seattle = datetime.now(ZoneInfo("America/Los_Angeles"))
        time_str = seattle.strftime("%H:%M:%S %Z")
    except Exception:
        time_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    uptime_s = snapshot.get("uptime_s", 0)
    if uptime_s < 3600:
        up_str = f"{int(uptime_s) // 60}m"
    else:
        h = int(uptime_s) // 3600
        m = (int(uptime_s) % 3600) // 60
        up_str = f"{h}h{m}m"
    lines.append(f"--- Sonos Status ({time_str}) | up {up_str} ---")

    # Active rooms with detail
    for r in snapshot.get("active_rooms", []):
        state = r["state"]
        room = r["room"]

        if state == "PLAYING_TV":
            lines.append(f"  {room}: PLAYING_TV (HDMI)")
            continue

        state_label = "PLAYING" if state == "PLAYING" else "PAUSED" if "PAUSE" in state else state
        track = r.get("track", "?")
        artist = r.get("artist", "?")
        album = r.get("album", "")
        svc = _format_service(r.get("service", ""))

        line = f'  {room}: {state_label} | "{track}" - {artist}'
        if album:
            line += f" - {album}"
        if svc:
            line += f" [{svc}]"
        lines.append(line)

        # Second line: container + play mode + group
        details = []
        if r.get("container"):
            details.append(f"From: {r['container']}")
        mode = r.get("play_mode", "NORMAL")
        if mode and mode != "NORMAL":
            details.append(mode)
        members = r.get("rooms_in_group", [])
        if len(members) > 1:
            others = [m for m in members if m != room]
            if others:
                details.append(f"Group: +{', +'.join(others)}")
        if details:
            lines.append(f"    -> {' | '.join(details)}")

    # Stopped rooms (collapsed)
    stopped = snapshot.get("stopped_rooms", [])
    if stopped:
        lines.append(f"  ({len(stopped)} rooms stopped)")

    # Command summary
    cmd = snapshot.get("last_command", {})
    if cmd.get("action"):
        cmd_str = f"Last cmd: {cmd['action']} {_format_age(cmd.get('age_s'))} ({cmd.get('source', '?')})"
    else:
        cmd_str = "Last cmd: none"
    cmd_str += f" | Total: {snapshot.get('commands_total', 0)}"
    lines.append(f"  {cmd_str}")

    # Buffer + SSE summary
    buf = snapshot.get("buffer", {})
    buf_str = f"Buffer: {buf.get('count', 0)} pending"
    if buf.get("last_added_age_s") is not None:
        buf_str += f", added {_format_age(buf['last_added_age_s'])}"
    if buf.get("last_post_age_s") is not None:
        buf_str += f" | POST {_format_age(buf['last_post_age_s'])}"
    sse = snapshot.get("sse", {})
    if sse.get("last_publish_age_s") is not None:
        buf_str += f" | SSE {_format_age(sse['last_publish_age_s'])}"
    lines.append(f"  {buf_str}")

    # Speaker health (only if issues)
    speakers = snapshot.get("speakers", {})
    offline = speakers.get("offline_names", [])
    if offline:
        lines.append(f"  [WARN] Speakers offline: {', '.join(offline)}")

    # ntfy health (only if disconnected)
    ntfy = snapshot.get("ntfy", {})
    if not ntfy.get("connected"):
        lines.append(f"  [WARN] ntfy disconnected (reconnects: {ntfy.get('reconnects', 0)})")

    # skip_version warning
    if snapshot.get("skip_version"):
        lines.append(f"  [WARN] skip_version: {snapshot['skip_version']}")

    # Recent track changes (last 3)
    changes = snapshot.get("track_changes", [])
    if changes:
        recent = changes[-3:]
        change_parts = []
        now = time.time()
        for c in recent:
            age = _format_age(now - c["at"])
            tag = "cmd" if c.get("commanded") else "organic"
            change_parts.append(f"{c['room']} {age} ({tag})")
        lines.append(f"  Changes: {' | '.join(change_parts)}")

    return "\n".join(lines)


def _diag_fingerprint(snapshot):
    """Generate a fingerprint for change detection."""
    parts = []
    for r in snapshot.get("active_rooms", []):
        parts.append(f"{r['room']}:{r['state']}:{r.get('track','')}")
    parts.append(f"stopped:{len(snapshot.get('stopped_rooms', []))}")
    parts.append(f"buf:{snapshot.get('buffer', {}).get('count', 0)}")
    parts.append(f"cmd:{snapshot.get('commands_total', 0)}")
    parts.append(f"tc:{len(snapshot.get('track_changes', []))}")
    return "|".join(parts)


def _log_diagnostic_status():
    """Build snapshot, check for changes, log if changed."""
    global _prev_diag_fingerprint
    try:
        snapshot = build_status_snapshot()
        fp = _diag_fingerprint(snapshot)
        if fp != _prev_diag_fingerprint:
            _prev_diag_fingerprint = fp
            log("\n" + format_status_log(snapshot))
    except Exception as e:
        log(f"[diag] Error building status: {e}")

# Module-level var: room that was just commanded (set by play handler, cleared after heartbeat)
_just_commanded_room = None

def heartbeat_fields():
    """Return standard heartbeat dict to embed in any outbound payload."""
    boot_iso = datetime.fromtimestamp(_service_start_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fields = {
        "client_id":       client_id,
        "client_type":     "lifelog_service",
        "house":           house,
        "version":         SERVICE_VERSION,
        "boot_time":       boot_iso,
        "modules":         modules,
        "computer":        computer,
        "sonos_capable":   "sonos" in modules,
        "sonos_commander": sonos_commander if "sonos" in modules else False,
        "timestamp":       now_iso(),
    }
    if "sonos" in modules:
        rp = get_rooms_playing()
        fields["rooms_playing"] = rp
        fields["rooms_paused"] = sorted(
            name for name, st in _last_transport_states.items()
            if st == "PAUSED_PLAYBACK" and name not in rp
        )
        # All rooms the service can currently see (for offline detection in frontend)
        fields["rooms_all"] = sorted(current_devices_by_name.keys())
    # SSE diagnostic state — visible in webhook heartbeats
    fields["sse_state"] = {
        "consecutive_429": _sse_consecutive_429,
        "backoff_remaining_s": max(0, int(_sse_backoff_until - time.time())) if _sse_backoff_until else 0,
        "last_send_age_s": int(time.time() - _sse_last_send_ts) if _sse_last_send_ts else None,
        "send_attempts": _sse_send_attempts,
        "topic": ntfy_ui_topic,
    }
    # Now-playing tracks — same data as SSE, for webhook consumers (wellness check, etc.)
    np_tracks = []
    for coord_name in list(_last_ui_track.keys()):
        rs = room_state.get(coord_name)
        if rs and rs.get("track_info"):
            ti = rs["track_info"]
            np_tracks.append({
                "title": ti.get("title", ""),
                "artist": ti.get("artist", ""),
                "album": ti.get("album", ""),
                "rooms": ti.get("rooms", [coord_name]),
                "service": ti.get("service", ""),
                "uri": ti.get("uri", ""),
            })
    if np_tracks:
        fields["now_playing_tracks"] = np_tracks
    # Recent log lines — ride along on every POST for visibility
    fields["recent_logs"] = get_recent_logs(100)
    # Dedicated error buffer — persists longer than general logs
    fields["recent_errors"] = get_recent_errors(50)
    # Structured command outcomes — for agent-side confirmation/debugging
    fields["command_results"] = get_command_results()
    return fields

def _send_heartbeat():
    sse_relay = build_sse_relay_payload()
    payload = {"type": "heartbeat"}
    payload.update(heartbeat_fields())
    if sse_relay:
        payload["sse_relay"] = sse_relay
    try:
        r = requests.post(WEBHOOK, json=payload, timeout=10)
        log(f"* Heartbeat (standalone) -> HTTP {r.status_code}")
    except Exception as e:
        log(f"Heartbeat failed: {e}")

# --- BUFFER FLUSH ------------------------------------------------------------
# --- NTFY UI PUSH (real-time browser SSE) — v1.70 bundled architecture ------
# DESIGN NOTE: Option B bundler — multiple events within a debounce window
# merge into a single ntfy message. Every outbound message carries full state
# (rooms_playing, play_modes, now_playing_tracks). Track changes are urgent
# (3s debounce), periodic snapshots provide keepalive (15 min).
# Budget: ~50 track events/day + ~96 snapshots/day = ~146/day (within ntfy 250/day).
_sse_bundle       = {}                # accumulated payload fields
_sse_bundle_lock  = threading.Lock()
_sse_flush_timer  = None              # threading.Timer for debounced flush
_sse_last_send_ts = 0.0               # epoch of last actual ntfy POST
_sse_backoff_until = 0.0              # epoch — skip all sends until this time (429 backoff)
_sse_consecutive_429 = 0              # count consecutive 429 failures for exponential backoff
_sse_send_attempts = 0                # total flush attempts since startup (diagnostic)
SSE_DEBOUNCE_S    = 3.0               # merge window — events within 3s collapse into one message
SSE_MIN_GAP_S     = 10.0              # absolute floor between sends (burst protection)
SSE_BACKOFF_STEPS = [30, 120, 300, 600, 900, 1800]  # 30s, 2m, 5m, 10m, 15m, 30m (caps at 30m)

def _sse_enrich_state(payload):
    """Inject full state snapshot into any outbound SSE payload.
    This ensures every message the browser receives is a complete picture."""
    rp = get_rooms_playing()
    payload["rooms_playing"] = rp
    payload["rooms_paused"] = sorted(
        name for name, st in _last_transport_states.items()
        if st == "PAUSED_PLAYBACK" and name not in rp
    )
    payload["rooms_all"] = sorted(current_devices_by_name.keys())
    payload["play_modes"] = dict(_current_play_modes)
    payload["mute_states"] = dict(_current_mute_states)
    payload["house"] = house
    payload["client_id"] = client_id
    payload["version"] = SERVICE_VERSION
    # Include now_playing_tracks from room_state
    np_tracks = []
    for coord_name in list(_last_ui_track.keys()):
        rs = room_state.get(coord_name)
        if rs and rs.get("track_info"):
            ti = rs["track_info"]
            np_tracks.append({
                "title": ti.get("title", ""),
                "artist": ti.get("artist", ""),
                "album": ti.get("album", ""),
                "rooms": ti.get("rooms", [coord_name]),
                "service": ti.get("service", ""),
                "uri": ti.get("uri", ""),
            })
    if np_tracks:
        payload["now_playing_tracks"] = np_tracks

def publish_ui_event(event_type, data):
    """Queue event data for bundled SSE publish (v1.70).
    Multiple events within the debounce window merge into one ntfy message.
    Every message includes full state snapshot for the browser."""
    if not ntfy_ui_topic:
        return
    log(f"SSE queue: {event_type}")
    with _sse_bundle_lock:
        _sse_bundle.update(data)
        evts = _sse_bundle.setdefault("_event_types", [])
        if event_type not in evts:
            evts.append(event_type)
    _sse_schedule_flush()

def _sse_schedule_flush():
    """Schedule a debounced flush. Respects SSE_MIN_GAP_S between sends."""
    global _sse_flush_timer
    if _sse_flush_timer:
        _sse_flush_timer.cancel()
    elapsed = time.time() - _sse_last_send_ts
    wait = max(SSE_DEBOUNCE_S, SSE_MIN_GAP_S - elapsed)
    _sse_flush_timer = threading.Timer(wait, _sse_do_flush)
    _sse_flush_timer.daemon = True
    _sse_flush_timer.start()

def _sse_do_flush():
    """Direct ntfy push -- re-enabled in v1.83.
    v1.75-v1.82: disabled due to IP rate-limiting. v1.80 fixed the root cause
    (stopped rooms polling at ~240 pushes/hr). Organic rate is ~10-15/hr,
    well within ntfy free tier (250/hr). Backoff logic retained as safety net."""
    global _sse_send_attempts, _sse_last_send_ts, _sse_consecutive_429, _sse_backoff_until
    _sse_send_attempts += 1
    # Drain the bundle
    with _sse_bundle_lock:
        if not _sse_bundle:
            return
        payload = dict(_sse_bundle)
        _sse_bundle.clear()
    event_types = payload.pop("_event_types", [])
    if not ntfy_ui_topic:
        log(f"SSE flush: no ntfy_ui_topic configured, dropping {event_types}")
        return
    # Backoff check
    if _sse_backoff_until and time.time() < _sse_backoff_until:
        log(f"SSE flush skipped: backoff ({int(_sse_backoff_until - time.time())}s remaining)")
        return
    # Enrich with full state snapshot
    try:
        _sse_enrich_state(payload)
    except Exception as e:
        log(f"SSE enrich failed: {e}")
        return
    payload["events"] = event_types
    payload["ts"] = time.time()
    # POST as plain text body (JSON string) -- browser does JSON.parse(event.data)
    url = f"https://ntfy.sh/{ntfy_ui_topic}"
    body = json.dumps(payload)
    try:
        r = requests.post(url, data=body.encode("utf-8"), timeout=10)
        if r.status_code == 429:
            _sse_consecutive_429 += 1
            step = min(_sse_consecutive_429 - 1, len(SSE_BACKOFF_STEPS) - 1)
            _sse_backoff_until = time.time() + SSE_BACKOFF_STEPS[step]
            log(f"SSE 429 (#{_sse_consecutive_429}): backing off {SSE_BACKOFF_STEPS[step]}s")
        else:
            _sse_consecutive_429 = 0
            _sse_backoff_until = 0.0
            _sse_last_send_ts = time.time()
            log(f"SSE push: {event_types} -> HTTP {r.status_code}")
    except Exception as e:
        log(f"SSE push failed: {e}")

def build_sse_relay_payload():
    """Build SSE data for inclusion in webhook POSTs. Server relays this to ntfy.
    Returns a dict with the same structure the browser expects from ntfy SSE messages."""
    payload = {}
    try:
        _sse_enrich_state(payload)
    except Exception as e:
        log(f"SSE relay enrich failed: {e}")
        return None
    # Determine event types from current state
    events = []
    rp = payload.get("rooms_playing", [])
    if rp:
        events.append("status_update")
        # Check if there's a now-playing track
        npt = payload.get("now_playing_tracks", [])
        if npt:
            events.append("now_playing")
            # Include title/artist at top level for browser compat
            payload["title"] = npt[0].get("title", "")
            payload["artist"] = npt[0].get("artist", "")
            payload["album"] = npt[0].get("album", "")
            payload["uri"] = npt[0].get("uri", "")
            payload["service"] = npt[0].get("service", "")
            if len(npt[0].get("rooms", [])) > 0:
                payload["rooms"] = npt[0]["rooms"]
    else:
        events.append("status_update")
    payload["events"] = events
    payload["ts"] = time.time()
    payload["topic"] = ntfy_ui_topic
    return payload

def flush_buffer(reason=""):
    global last_post_ts
    with pending_buffer_lock:
        if not pending_buffer:
            return
        items = list(pending_buffer)
        pending_buffer.clear()

    # Persist to disk before POSTing (crash safety)
    try:
        PENDING_PATH.write_text(json.dumps(items), encoding="utf-8")
    except Exception as e:
        log(f"Warning: couldn't persist buffer: {e}")

    sse_relay = build_sse_relay_payload()
    payload = {
        "type":      "sonos_history_batch",
        "flush_reason": reason or "unknown",
        "house":     house,
        "items":     items,
        "heartbeat": heartbeat_fields(),
    }
    if sse_relay:
        payload["sse_relay"] = sse_relay
    try:
        r = requests.post(WEBHOOK, json=payload, timeout=20)
        log(f"[OK] Flushed {len(items)} track(s) [{reason}] -> HTTP {r.status_code}")
        last_post_ts = time.time()
        try: PENDING_PATH.unlink(missing_ok=True)
        except: pass
    except Exception as e:
        log(f"[FAIL] Flush failed [{reason}]: {e} -- restoring {len(items)} item(s) to buffer")
        with pending_buffer_lock:
            pending_buffer[:0] = items  # prepend back

# --- HEARTBEAT THREAD -------------------------------------------------------
def heartbeat_thread():
    """60-min keepalive: fires only if no other POST has gone out in 60 min.
    During active sessions, every flush/command result carries heartbeat fields inline,
    so this thread mostly sleeps. Exists for staleness monitor to detect 'service alive'."""
    global last_post_ts

    # Always send on startup so status shows online immediately
    _send_heartbeat()
    last_post_ts = time.time()

    while True:
        time.sleep(60)  # check every minute

        if not is_active_hours():
            log(f"* Heartbeat: quiet hours (Seattle {seattle_hour():02d}:xx) -- paused")
            time.sleep(HEARTBEAT_QUIET_SLEEP)
            continue

        since_last = time.time() - last_post_ts
        if since_last < HEARTBEAT_FALLBACK_SECS:
            continue  # a flush or command result posted recently -- no heartbeat needed

        # Nothing sent in 60 min -- flush pending buffer (carries heartbeat) or send standalone
        with pending_buffer_lock:
            has_pending = len(pending_buffer) > 0
        if has_pending:
            flush_buffer(reason="heartbeat-fallback")
        else:
            _send_heartbeat()
            last_post_ts = time.time()
        log(f"* Heartbeat: fallback fired (idle {int(since_last//60)} min) -- next check in 60s")

# --- BUFFER MONITOR THREAD --------------------------------------------------
def buffer_monitor_thread():
    """Flush buffer on trailing-edge OR max-age timer.
    Max-age (30s) ensures organic track changes relay to browser quickly via SSE relay.
    Trailing-edge (30 min) is the safety net for long pauses."""
    while True:
        time.sleep(10)  # check every 10s (was 30s -- tighter for max-age relay)
        with pending_buffer_lock:
            count = len(pending_buffer)
            oldest_age = (time.time() - pending_buffer[0].get("_buffered_at", time.time())) if pending_buffer else 0
        if count == 0:
            continue
        # Max-age flush: oldest item has been buffered > 30s -> relay to browser ASAP
        if oldest_age >= BUFFER_MAX_AGE_SECS:
            flush_buffer(reason="max-age-relay")
            continue
        # Trailing-edge flush: nothing new added in 30 min
        since_last_track = time.time() - last_track_added_ts
        if since_last_track >= BATCH_TRAILING_SECS:
            flush_buffer(reason="trailing-edge")

# --- VERSION CHECK THREAD ---------------------------------------------------
# [ROLLBACK-UNSAFE] Calls self_update_check() every 60 min. This is the periodic
# trigger path for self-update (vs. ntfy instant trigger below).
def version_check_thread():
    time.sleep(120)  # wait 2 min after start
    while True:
        self_update_check()
        time.sleep(VERSION_CHECK_INTERVAL)

# --- BACKUP MODULE THREAD ---------------------------------------------------
def backup_thread():
    """Run lifelog_extract.py every hour; it handles cursor/hash dedup internally."""
    extract = INSTALL_DIR / "lifelog_extract.py"

    def run_extract():
        if not extract.exists():
            log("Backup: lifelog_extract.py not found -- skipping")
            return
        log("Backup: running lifelog_extract.py...")
        try:
            result = subprocess.run(
                [sys.executable, str(extract)],
                capture_output=True, text=True, timeout=900,
                encoding="utf-8", errors="replace"
            )
            output = ((result.stdout or "") + (result.stderr or "")).strip()
            for line in output.split("\n"):
                if line.strip():
                    log(f"  [extract] {line}")
            if result.returncode not in (0, 1):
                post_error(
                    f"lifelog_extract.py exited {result.returncode}",
                    context=output[-500:], module="backup"
                )
        except subprocess.TimeoutExpired:
            post_error("lifelog_extract.py timed out after 5 min", module="backup")
        except Exception as e:
            post_error(f"Backup run error: {e}",
                       context=traceback.format_exc()[:500], module="backup")

    time.sleep(30)   # let service settle
    run_extract()    # immediate run on start
    while True:
        time.sleep(BACKUP_INTERVAL)
        run_extract()

# --- DEV LOOP THREAD --------------------------------------------------------
def dev_loop_thread():
    """Poll GitHub for dev_next.ps1; run if SHA changed; post output to webhook."""
    last_sha = ""
    while True:
        try:
            r = gh_get("dev_next.ps1")
            if r:
                data       = r.json()
                sha        = data.get("sha", "")[:12]
                if sha and sha != last_sha:
                    script = gh_decode(r)
                    first_line = script.split("\n")[0].strip()
                    log(f"[dev] New SHA: {sha} | {first_line}")
                    tmp = Path(os.environ.get("TEMP", "/tmp")) / "dev_next_run.ps1"
                    tmp.write_text(script, encoding="utf-8")
                    proc = subprocess.run(
                        ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(tmp)],
                        capture_output=True, text=True, timeout=120,
                        encoding="utf-8", errors="replace"
                    )
                    output   = (proc.stdout or "") + (proc.stderr or "")
                    last_sha = sha
                    if output.strip():
                        body = json.dumps({
                            "computer": computer,
                            "sha":      sha,
                            "version":  first_line,
                            "output":   output,
                        })
                        try:
                            requests.post(DEV_WEBHOOK, data=body.encode("utf-8"),
                                          headers={"Content-Type": "application/json"}, timeout=15)
                            log(f"[dev] Output sent ({len(output)} chars)")
                        except Exception as e:
                            log(f"[dev] Failed to post output: {e}")
                    else:
                        log(f"[dev] No-op script ({sha}), skipping webhook post")
        except Exception as e:
            log(f"[dev] Poll error: {e}")
        time.sleep(DEV_POLL_INTERVAL)

# --- SONOS: SERVICE DETECTION -----------------------------------------------
def detect_service(uri, metadata=""):
    import re
    s = (uri + metadata).lower()
    if "spotify"  in s: return "sonos_spotify"
    if "apple"    in s or "itunes" in s or "music.apple" in s: return "sonos_apple_music"
    if "qobuz"    in s: return "sonos_qobuz"
    if "tunein"   in s or "radiotime" in s or "kexp" in s or "kcrw" in s: return "sonos_tunein"
    if "x-rincon-mp3radio" in s or "x-sonosapi-radio" in s or "x-rincon-stream" in s:
        return "sonos_radio"
    SID_MAP = {9:"sonos_spotify",31:"sonos_qobuz",52:"sonos_apple_music",
               204:"sonos_apple_music",254:"sonos_tunein",2:"sonos_amazon",
               13:"sonos_pandora",38:"sonos_siriusxm"}
    m = re.search(r'[?&]sid=(\d+)', uri)
    if m:
        sid = int(m.group(1))
        if sid in SID_MAP: return SID_MAP[sid]
    return "sonos_unknown"

# --- SONOS: DISCOVERY -------------------------------------------------------
def get_coordinators():
    try:
        import soco
        devices = soco.discover(timeout=8)
        if not devices: return []
        coordinators = {}
        now_t = time.time()
        for dev in devices:
            ip = dev.ip_address
            # Skip IPs that timed out recently (avoid 20s hang per offline speaker)
            if ip in _offline_ips:
                if now_t - _offline_ips[ip] < OFFLINE_RECHECK_SECS:
                    continue
                else:
                    del _offline_ips[ip]
                    log(f"Retrying previously offline speaker at {ip}")
            try:
                g = dev.group
                if g and dev == g.coordinator:
                    coordinators[dev.player_name] = dev
            except Exception as e:
                err_s = str(e).lower()
                if any(k in err_s for k in ("timed out", "max retries", "connection")):
                    _offline_ips[ip] = now_t
                    log(f"Speaker at {ip} unreachable -- skipping for {OFFLINE_RECHECK_SECS}s")
                else:
                    try:
                        coordinators[dev.player_name] = dev
                    except Exception:
                        pass
        return list(coordinators.values())
    except Exception as e:
        log(f"Discovery error: {e}")
        return []

# --- SONOS: TRACK INFO ------------------------------------------------------

def get_container_context(device):
    """Get the playlist/album/station context from Sonos position info.
    Uses GetPositionInfo which has the EnqueuedTransportURI -- the actual
    Spotify playlist/album/station URI (not the Sonos queue URI)."""
    try:
        pos = device.avTransport.GetPositionInfo(InstanceID=0)
        container_uri = ""
        container_name = ""
        container_type = ""

        # DEBUG: dump all position info keys
        debug_data = {"position_info": {}, "media_info": {}}
        for k, v in pos.items():
            val_str = str(v)[:500] if v else ""
            debug_data["position_info"][k] = val_str

        # Try GetMediaInfo for the queue-level container
        try:
            media = device.avTransport.GetMediaInfo(InstanceID=0)
            media_uri = media.get("CurrentURI", "")
            media_meta = media.get("CurrentURIMetaData", "")
            for k, v in media.items():
                val_str = str(v)[:500] if v else ""
                debug_data["media_info"][k] = val_str
        except Exception:
            media_uri = ""
            media_meta = ""

        # Write debug dump once (first track only)
        import json, os
        debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sonos_transport_debug.json")
        if not os.path.exists(debug_path):
            try:
                with open(debug_path, "w") as df:
                    json.dump(debug_data, df, indent=2, default=str)
                log(f"DEBUG: Wrote transport debug to {debug_path}")
            except Exception as e:
                log(f"DEBUG: Failed to write debug: {e}")

        # Get enqueued transport URI from position info
        enq_uri = pos.get("EnqueuedTransportURI", "") or ""
        enq_meta = pos.get("EnqueuedTransportURIMetaData", "") or ""

        # Prefer EnqueuedTransportURI -- it's the actual playlist/album
        if enq_uri and not enq_uri.startswith("x-rincon-queue:"):
            container_uri = enq_uri
            meta_xml = enq_meta
        elif media_uri and not media_uri.startswith("x-rincon-queue:") and not media_uri.startswith("x-rincon:"):
            container_uri = media_uri
            meta_xml = media_meta
        else:
            # Both are queue URIs -- try metadata anyway
            container_uri = enq_uri or media_uri
            meta_xml = enq_meta or media_meta

        # Parse metadata XML for name and type
        if meta_xml and meta_xml != "NOT_IMPLEMENTED":
            try:
                clean = re.sub(r'\sxmlns[^"]*"[^"]*"', '', meta_xml)
                root = ET.fromstring(clean)
                for elem in root.iter():
                    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if tag == "title" and elem.text:
                        container_name = elem.text.strip()
                    elif tag == "class" and elem.text:
                        container_type = elem.text.strip()
            except ET.ParseError:
                pass

        # Skip if slave in group or no useful data
        if not container_uri or container_uri.startswith("x-rincon:"):
            return None

        # Decode Spotify URIs from container_uri for cleaner data
        spotify_context = ""
        if "spotify" in container_uri.lower():
            m = re.search(r'spotify[:%]3[aA]([^?&]+)', container_uri)
            if m:
                spotify_context = "spotify:" + m.group(1).replace("%3a", ":").replace("%3A", ":")

        return {
            "container_uri": container_uri,
            "container_name": container_name,
            "container_type": container_type,
            "spotify_context": spotify_context,
        }
    except Exception:
        return None


def get_track_info(device):
    name = device.player_name
    now_epoch = time.time()
    if name in speaker_offline_since:
        if now_epoch - speaker_offline_since[name] < OFFLINE_RECHECK_SECS:
            return None
        log(f"Retrying offline speaker: {name}")
        del speaker_offline_since[name]
        speaker_failures[name] = 0
    try:
        state = device.get_current_transport_info().get("current_transport_state", "STOPPED")
        if state not in ("PLAYING", "TRANSITIONING"):
            speaker_failures[name] = 0
            return None
        info  = device.get_current_track_info()
        title = info.get("title", "").strip()
        uri      = info.get("uri", "")
        metadata = info.get("metadata", "")
        # Radio/TuneIn streams often have empty or garbage titles.
        # Detect stream URIs and provide a synthetic title instead of discarding.
        _STREAM_PREFIXES = ("x-rincon-mp3radio:", "x-sonosapi-stream:", "x-sonosapi-radio:",
                            "x-rincon-stream:", "aac://", "x-sonosapi-hls:")
        _is_radio_stream = any(uri.lower().startswith(p) for p in _STREAM_PREFIXES)
        # Filter Sonos internal state strings that leak through during transitions
        _JUNK_TITLES = ("ZPSTR_CONNECTING", "ZPSTR_BUFFERING", "NOT_IMPLEMENTED", "x-sonosapi-stream:")
        if title.upper() in (j.upper() for j in _JUNK_TITLES):
            title = ""
        if not title:
            if _is_radio_stream:
                # Derive a synthetic title from the URI
                uri_lower = uri.lower()
                if "kcrw" in uri_lower:
                    title = "KCRW Eclectic 24"
                elif "kexp" in uri_lower:
                    title = "KEXP"
                else:
                    title = "Radio Stream"
            else:
                speaker_failures[name] = 0
                return None
        dur_str  = info.get("duration", "0:00:00")
        dur_secs = 0
        try:
            p = dur_str.split(":")
            if len(p) == 3:
                dur_secs = int(p[0])*3600 + int(p[1])*60 + int(p[2])
        except Exception: pass
        try:    members = [m.player_name for m in device.group.members]
        except: members = [device.player_name]
        speaker_failures[name] = 0
        ctx = get_container_context(device)
        return {"title": title, "artist": info.get("artist","").strip(),
                "album": info.get("album","").strip(), "uri": uri,
                "service": detect_service(uri, metadata),
                "duration_seconds": dur_secs, "rooms": members,
                "coordinator": device.player_name,
                "container": ctx}
    except Exception as e:
        failures = speaker_failures.get(name, 0) + 1
        speaker_failures[name] = failures
        if failures == OFFLINE_THRESHOLD:
            speaker_offline_since[name] = now_epoch
            msg = f"Speaker '{name}' offline after {failures} failures: {e}"
            log(f"[WARN] {msg}")
            post_error(msg, context=f"speaker={name}", module="sonos")
        elif failures < OFFLINE_THRESHOLD:
            log(f"Error from {name} (attempt {failures}): {e}")
        return None

# --- SONOS: POST HISTORY (buffered) -----------------------------------------
def post_history(track, room, started_at, ended_at):
    global last_sonos_activity_ts, last_track_added_ts
    duration_played = int((ended_at - started_at).total_seconds())
    if duration_played < 15: return
    last_sonos_activity_ts = time.time()
    # Add to state ring buffer for cross-device state.json
    _retire_to_state_ring(track, track.get("rooms", [room]), started_at)
    uri_or_title = track["uri"] or f"{track['title']}|{track['artist']}"
    fp           = hashlib.md5(uri_or_title.encode()).hexdigest()[:12]
    bucket       = int(started_at.timestamp() // 60)
    dedup_key    = f"sonos_{house}_{room.lower().replace(' ','_')}_{fp}_{bucket}"
    item = {
        "type": "sonos_history", "house": house, "room": room,
        "title": track["title"], "artist": track["artist"], "album": track["album"],
        "uri": track["uri"], "service": track["service"],
        "started_at":  started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_at":    ended_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_played_seconds": duration_played,
        "track_duration_seconds":  track.get("duration_seconds", 0),
        "dedup_key": dedup_key,
    }
    # Add container context (playlist/album/station) if available
    container = track.get("container")
    if container:
        item["container_uri"] = container.get("container_uri", "")
        item["container_name"] = container.get("container_name", "")
        item["container_type"] = container.get("container_type", "")
        if container.get("spotify_context"):
            item["spotify_context"] = container["spotify_context"]
    item["_buffered_at"] = time.time()  # for max-age relay flush
    with pending_buffer_lock:
        pending_buffer.append(item)
        last_track_added_ts = time.time()
        count = len(pending_buffer)
    log(f'+ Buffered: "{track["title"]}" - {track["artist"]} | {room} ({duration_played}s) [buffer: {count}]')
    if count >= BATCH_SIZE:
        flush_buffer(reason="count")

# --- SONOS: MULTI-MACHINE TARGETING -----------------------------------------
def is_my_command(cmd):
    """
    Determines if this machine should execute a Sonos command.

    Rules:
    - If cmd has 'target_client_id': only execute if it matches our client_id
      or computer name (agent always sets this when targeting a specific machine).
    - If no 'target_client_id': only execute if we're the designated commander
      (sonos_commander=True in config). This prevents multiple machines from
      all executing the same unaddressed broadcast command.
    """
    target = cmd.get("target_client_id", "")
    if target:
        mine = target.lower() in (client_id.lower(), computer.lower())
        if not mine:
            log(f"[SKIP] Skipping (targeted to {target}, we are {client_id})")
        return mine
    else:
        if not sonos_commander:
            log(f"[SKIP] Skipping unaddressed command (not commander): {cmd.get('action')}")
        return sonos_commander

# --- SONOS: COMMAND DEDUP ---------------------------------------------------
# TTL-based: hashes expire after CMD_DEDUP_TTL_SECONDS (60s).
# This covers ntfy's since=5m replay window without permanently blocking
# legitimate repeated commands (next/next, update_check, etc.).
def _cmd_hash(cmd):
    return hashlib.md5(json.dumps(cmd, sort_keys=True).encode()).hexdigest()

def _mark_executed(cmd):
    now = time.time()
    executed_cmd_hashes[_cmd_hash(cmd)] = now
    # Prune expired entries periodically
    if len(executed_cmd_hashes) > 50:
        cutoff = now - CMD_DEDUP_TTL_SECONDS
        expired = [h for h, ts in executed_cmd_hashes.items() if ts < cutoff]
        for h in expired:
            del executed_cmd_hashes[h]

def _already_executed(cmd):
    h = _cmd_hash(cmd)
    ts = executed_cmd_hashes.get(h)
    if ts is None:
        return False
    if time.time() - ts > CMD_DEDUP_TTL_SECONDS:
        del executed_cmd_hashes[h]
        return False
    return True

# --- SONOS: EXECUTE COMMAND -------------------------------------------------
def _decode_sonos_spotify_uri(uri):
    """Decode Sonos-encoded Spotify URIs back to native spotify: format.
    
    DESIGN NOTE: The UI sends sonos_uri from the url column (Sonos transport format
    like x-sonos-spotify:spotify%3atrack%3aID?sid=12&flags=...). The service needs
    native spotify:track:ID format for ShareLinkPlugin. This decoder handles that
    conversion so both play_next and add_to_queue route Spotify content correctly.
    """
    if uri and uri.startswith("x-sonos-spotify:"):
        from urllib.parse import unquote
        # Strip x-sonos-spotify: prefix and any ?sid=... suffix
        inner = uri[len("x-sonos-spotify:"):]
        if "?" in inner:
            inner = inner.split("?")[0]
        decoded = unquote(inner)  # spotify%3atrack%3aID -> spotify:track:ID
        if decoded.startswith("spotify:"):
            return decoded
    return uri


def _find_coordinator(cmd, devices):
    """Find the coordinator for the first room in cmd without regrouping.
    Used by play_next/play_uri -- rooms are already grouped via tile taps."""
    rooms = cmd.get("rooms", [])
    if isinstance(rooms, str): rooms = [rooms]
    room = cmd.get("room")
    if room and not rooms: rooms = [room]
    if not rooms: return None, rooms
    dev = devices.get(rooms[0])
    if not dev: return None, rooms
    coordinator = dev.group.coordinator if dev.group and dev.group.coordinator else dev
    return coordinator, rooms

def _setup_rooms(cmd, devices):
    """Incremental room grouping. Returns (coordinator, rooms_list, was_grouped_with).
    Compares current group state vs desired rooms — only unjoins/joins deltas.
    No-op fast path when group already matches desired state."""
    rooms = cmd.get("rooms", [])
    if isinstance(rooms, str): rooms = [rooms]
    room = cmd.get("room")
    if room and not rooms: rooms = [room]
    if not rooms: return None, [], []

    primary = rooms[0]
    dev = devices.get(primary)
    if not dev: return None, rooms, []

    was_grouped = []

    # Get current group state
    try:
        current_members = set(m.player_name for m in dev.group.members) if dev.group else {primary}
        current_coordinator = dev.group.coordinator.player_name if dev.group and dev.group.coordinator else primary
    except Exception:
        current_members = {primary}
        current_coordinator = primary

    desired_members = set(rooms)

    if len(rooms) > 1:
        # Multi-room requested
        if current_coordinator == primary and current_members == desired_members:
            # Already correct — no-op
            log(f"_setup_rooms: group already correct ({primary} + {list(desired_members - {primary})}), no-op")
            coordinator = dev.group.coordinator if dev.group and dev.group.coordinator else dev
            return coordinator, rooms, []

        # Need to change coordinator? Full teardown only in that case.
        if current_coordinator != primary and primary in current_members:
            # Primary is a member but not coordinator — unjoin it first so it becomes independent
            try:
                dev.unjoin()
                time.sleep(0.5)
            except Exception as e:
                log(f"_setup_rooms: unjoin {primary} from old coordinator: {e}")

        # Unjoin members that shouldn't be in the group
        to_remove = current_members - desired_members - {primary}
        for r in to_remove:
            d = devices.get(r)
            if d:
                try:
                    d.unjoin()
                    was_grouped.append(r)
                except Exception as e:
                    log(f"_setup_rooms: failed to unjoin {r}: {e}")

        # Join members that need to be added
        to_add = desired_members - current_members - {primary}
        # Also re-join members that were already there but need primary as coordinator
        if current_coordinator != primary:
            to_add = desired_members - {primary}  # rejoin everyone under new coordinator

        joined = []
        for r in to_add:
            d = devices.get(r)
            if d:
                try:
                    d.join(dev)
                    joined.append(r)
                except Exception as e:
                    log(f"_setup_rooms: failed to join {r} to {primary}: {e}")
        if joined or to_remove:
            time.sleep(1)
        if joined:
            log(f"_setup_rooms: incremental group update — {primary} + {joined} (removed: {list(to_remove)})")
        else:
            log(f"_setup_rooms: group adjusted — removed {list(to_remove)}")
    else:
        # Single room: should be solo
        if len(current_members) == 1:
            # Already solo — no-op
            log(f"_setup_rooms: {primary} already solo, no-op")
        else:
            # In a group — need to isolate
            was_grouped = [m for m in current_members if m != primary]
            try:
                if current_coordinator != primary:
                    dev.unjoin()
                else:
                    for member in list(dev.group.members):
                        if member != dev:
                            member.unjoin()
                time.sleep(1)
            except Exception:
                pass
            log(f"_setup_rooms: isolated {primary} from {was_grouped}")

    # Return the coordinator device
    try:
        coordinator = dev.group.coordinator if dev.group and dev.group.coordinator else dev
    except Exception:
        coordinator = dev
    return coordinator, rooms, was_grouped


# Actions that execute locally without any webhook POST (ack or result).
# Avoids unnecessary agent invocations for high-frequency, low-value commands.
SILENT_ACTIONS = {"volume_up", "volume_down", "set_volume", "volume", "resume", "play_resume", "next", "previous", "get_volume", "pause", "update_check", "get_logs", "flush", "toggle_mute", "cycle_repeat", "play_next", "add_to_queue", "play_radio"}

def execute_command(cmd, source="unknown"):
    action = cmd.get("action", "")
    cmd_id = cmd.get("cmd_id", "")
    if action in ("none", "", "idle") or cmd_id == "idle":
        return

    # Track command for diagnostics
    global _last_command_at, _last_command_action, _last_command_source, _commands_received_count, last_post_ts
    _last_command_at = time.time()
    _last_command_action = action
    _last_command_source = source
    _commands_received_count += 1

    # update_check is always self-targeted (every machine updates itself)
    if action != "update_check" and not is_my_command(cmd):
        return

    is_silent = action in SILENT_ACTIONS

    # Ack removed (v1.57) -- was triggering unnecessary agent invocations.
    # The sonos_result POST provides the confirmation that matters.

    result = {"type":"sonos_result","cmd_id":cmd_id,"action":action,"house":house,
              "success":False,"message":"","data":None}

    # Pass through timing from command sender
    if cmd.get("t_requested"):
        result["t_requested"] = cmd["t_requested"]

    try:
        devices = current_devices_by_name

        if action == "update_check":
            # [ROLLBACK-UNSAFE] This code path triggers self_update_check() from the
            # old version. The 2s delay + thread spawn all run in currently deployed code.
            result["success"] = True
            result["message"] = f"Running update check (v{SERVICE_VERSION})"
            def _do(): time.sleep(2); self_update_check()
            threading.Thread(target=_do, daemon=True).start()

        elif action == "flush":
            # DESIGN NOTE: Flush is a silent action that drains pending_buffer and
            # sends an SSE relay with current state. Even if buffer is empty, we POST
            # the heartbeat + sse_relay so the browser gets fresh now-playing data.
            # This is triggered by the Sync button in the web UI.
            # flush_reason is passed through from the command (e.g. "super_sync")
            cmd_flush_reason = cmd.get("flush_reason", "flush-cmd")
            flush_count = len(pending_buffer)
            flush_buffer(cmd_flush_reason)
            result["success"] = True
            result["message"] = f"Flushed {flush_count} buffered track(s)"
            result["flush_reason"] = cmd_flush_reason
            # Override silent behavior -- always POST this result so Tasklet relays SSE
            sse_relay = build_sse_relay_payload()
            if sse_relay:
                result["sse_relay"] = sse_relay
            result["heartbeat"] = heartbeat_fields()
            result["t_result_sent"] = now_iso()
            try:
                r = requests.post(WEBHOOK, json=result, timeout=15)
                log(f"Flush result -> HTTP {r.status_code}: {result['message']}")
                last_post_ts = time.time()
            except Exception as e:
                log(f"Failed to post flush result: {e}")
            return  # early return -- skip normal silent/non-silent POST logic

        elif action == "get_state":
            state = []
            for dev in get_coordinators():
                info = get_track_info(dev)
                try:    members = [m.player_name for m in dev.group.members]
                except: members = [dev.player_name]
                state.append({"coordinator": dev.player_name, "members": members,
                               "playing": {"title":info["title"],"artist":info["artist"],
                                           "album":info["album"],"service":info["service"]} if info else None})
            result["success"] = True
            result["data"]    = state

        elif action == "get_logs":
            # DESIGN NOTE: Returns full 200-line in-memory log ring buffer.
            # Marked SILENT so it doesn't drain pending_buffer or trigger agent invocations.
            # The result is POSTed directly to DEV_WEBHOOK (not main WEBHOOK) to avoid
            # triggering sonos event processing. Handled separately below.
            full_logs = get_full_logs()
            result["success"] = True
            result["message"] = f"Returning {len(full_logs)} log lines"
            result["data"]    = {"log_lines": full_logs, "buffer_capacity": _LOG_RING_MAX, "error_lines": get_recent_errors(), "command_results": get_command_results()}
            # POST to DEV_WEBHOOK directly (bypasses silent skip below)
            try:
                r = requests.post(DEV_WEBHOOK, json=result, timeout=15)
                log(f"get_logs -> DEV_WEBHOOK HTTP {r.status_code} ({len(full_logs)} lines)")
            except Exception as e:
                log(f"get_logs POST failed: {e}")
            return  # early return -- skip normal silent/non-silent POST logic

        elif action == "group":
            source    = cmd.get("source")
            add_rooms = cmd.get("add", [])
            if isinstance(add_rooms, str): add_rooms = [add_rooms]
            master = devices.get(source)
            if not master:
                result["message"] = f"Room '{source}' not found"
            else:
                # Resolve to actual group coordinator (source may be a member)
                try:
                    coord = master.group.coordinator if master.group and master.group.coordinator else master
                    log(f"group: source={source}, coordinator={coord.player_name}")
                except Exception:
                    coord = master
                joined = []
                failed = []
                for r in add_rooms:
                    dev = devices.get(r)
                    if dev:
                        try:
                            # Stop the joining speaker first so its active session
                            # doesn't hijack the coordinator's queue
                            try:
                                dev.stop()
                            except Exception:
                                pass
                            dev.join(coord)
                            joined.append(r)
                        except Exception as e:
                            log(f"group: failed to join {r} to {coord.player_name}: {e}")
                            failed.append(r)
                result["success"] = len(joined) > 0
                msg = f"Added {', '.join(joined)} to {coord.player_name}" if joined else "No rooms joined"
                if failed: msg += f" (failed: {', '.join(failed)})"
                result["message"] = msg

        elif action == "ungroup":
            room = cmd.get("room") or (cmd.get("rooms") or [None])[0]
            dev  = devices.get(room)
            if dev:
                dev.unjoin()
                result["success"] = True
                result["message"] = f"Removed {room} from its group"
            else:
                result["message"] = f"Room '{room}' not found"

        elif action == "search":
            from soco.music_services import MusicService
            svc_name    = cmd.get("service", "Qobuz")
            query       = cmd.get("query", "")
            search_type = cmd.get("search_type", "albums")
            n           = int(cmd.get("n", 5))
            if not query:
                result["message"] = "No query provided"
            else:
                items = list(MusicService(svc_name).search(search_type, query, 0, n))
                if not items:
                    result["message"] = f"No {search_type} for '{query}' on {svc_name}"
                else:
                    hits = [{"title": getattr(i,"title",str(i)),
                             "artist": getattr(i,"creator",""),
                             "uri":    getattr(i,"uri",None)} for i in items]
                    result["success"] = True
                    result["message"] = f"Found {len(hits)} {search_type} for '{query}' on {svc_name}"
                    result["data"]    = {"query":query,"service":svc_name,"results":hits}

        elif action == "search_and_play":
            from soco.music_services import MusicService
            room        = cmd.get("room") or (cmd.get("rooms") or [None])[0]
            svc_name    = cmd.get("service", "Qobuz")
            query       = cmd.get("query", "")
            search_type = cmd.get("search_type", "albums")
            dev = devices.get(room)
            if not dev:
                result["message"] = f"Room '{room}' not found"
            elif not query:
                result["message"] = "No query provided"
            else:
                items = list(MusicService(svc_name).search(search_type, query, 0, 5))
                if not items:
                    result["message"] = f"No {search_type} for '{query}' on {svc_name}"
                else:
                    first = items[0]
                    title = getattr(first, "title", str(first))
                    uri   = getattr(first, "uri", None)
                    meta  = getattr(first, "to_didl_string", lambda: "")()
                    if uri:
                        dev.play_uri(uri, meta=meta, title=title)
                        result["success"] = True
                        result["message"] = f"Playing '{title}' ({svc_name}) in {room}"
                        result["data"]    = {"title":title,"uri":uri,"service":svc_name}
                    else:
                        result["message"] = f"Found '{title}' but no URI"

        elif action == "play_spotify_uri":
            from soco.plugins.sharelink import ShareLinkPlugin
            spotify_uri = cmd.get("uri", "")
            title       = cmd.get("title", spotify_uri)
            dev, rooms, was_grouped = _setup_rooms(cmd, devices)
            if not dev:
                result["message"] = f"Room '{rooms[0] if rooms else '?'}' not found. Available: {list(devices.keys())}"
            elif not spotify_uri:
                result["message"] = "No Spotify URI provided"
            else:
                uri_type  = "track" if ":track:" in spotify_uri else "album" if ":album:" in spotify_uri else "playlist"
                uri_id    = spotify_uri.split(":")[-1]
                share_url = f"https://open.spotify.com/{uri_type}/{uri_id}"
                dev.clear_queue()
                plugin    = ShareLinkPlugin(dev)
                plugin.add_share_link_to_queue(share_url)
                dev.play_from_queue(0)
                # Set play mode: shuffle + repeat controlled independently
                shuffle = cmd.get("shuffle", False)
                repeat = cmd.get("repeat", True)  # default True for backward compat
                if shuffle and repeat:
                    dev.play_mode = "SHUFFLE"           # shuffle + repeat all
                elif shuffle and not repeat:
                    dev.play_mode = "SHUFFLE_NOREPEAT"  # shuffle, no repeat
                elif not shuffle and repeat:
                    dev.play_mode = "REPEAT_ALL"        # no shuffle, repeat all
                else:
                    dev.play_mode = "NORMAL"            # no shuffle, no repeat
                mode_note = []
                if shuffle: mode_note.append("shuffled")
                if not repeat: mode_note.append("no repeat")
                mode_str = f", {' + '.join(mode_note)}" if mode_note else ""
                result["success"] = True
                room_label = " + ".join(rooms) if len(rooms) > 1 else rooms[0]
                grp_note = f" (unlinked from {', '.join(was_grouped)})" if was_grouped else ""
                result["message"] = f"Playing '{title}' (Spotify{mode_str}) in {room_label}{grp_note}"
                result["data"]    = {"title":title,"uri":spotify_uri,"share_url":share_url,"was_grouped_with":was_grouped,"room":rooms[0],"rooms":rooms}

        elif action in ("queue_next", "queue", "add_to_queue"):
            # Add to Sonos queue WITHOUT clearing it or starting playback
            # v1.65: handles both Spotify URIs (ShareLinkPlugin) and raw Sonos URIs (DIDL metadata)
            from soco.plugins.sharelink import ShareLinkPlugin
            room        = cmd.get("room") or (cmd.get("rooms") or [None])[0]
            track_uri   = cmd.get("uri", "")
            title       = cmd.get("title", track_uri)
            dev = devices.get(room)
            if not dev:
                result["message"] = f"Room '{room}' not found. Available: {list(devices.keys())}"
            elif not track_uri:
                result["message"] = "No URI provided"
            else:
                # DESIGN NOTE: Decode x-sonos-spotify: URIs to native spotify: format
                # so ShareLinkPlugin is used instead of raw DIDL (which causes "No Content")
                track_uri = _decode_sonos_spotify_uri(track_uri)
                is_spotify = track_uri.startswith("spotify:") or "open.spotify.com" in track_uri
                if is_spotify:
                    uri_type  = "track" if ":track:" in track_uri else "album" if ":album:" in track_uri else "playlist"
                    uri_id    = track_uri.split(":")[-1]
                    share_url = f"https://open.spotify.com/{uri_type}/{uri_id}"
                else:
                    share_url = None
                try:
                    if dev.group and dev.group.coordinator != dev:
                        coordinator = dev.group.coordinator
                    else:
                        coordinator = dev
                    # DESIGN NOTE: + button should ALWAYS insert at next position.
                    # User expectation: "play this next" -- never append to end of queue.
                    as_next = True

                    def _do_queue(coord, spotify, pos=None, next_flag=False):
                        """Queue a track -- Spotify via ShareLinkPlugin, others via add_uri_to_queue with DIDL."""
                        if spotify:
                            plugin = ShareLinkPlugin(coord)
                            if pos is not None:
                                plugin.add_share_link_to_queue(share_url, position=pos)
                            elif next_flag:
                                plugin.add_share_link_to_queue(share_url, as_next=True)
                            else:
                                plugin.add_share_link_to_queue(share_url)
                        else:
                            # [DESIGN NOTE] Raw Sonos URI (Qobuz, Apple Music, etc.)
                            # Same DIDL-Lite approach as play_next -- proper item IDs for title display
                            from xml.sax.saxutils import escape as xml_escape
                            safe_title = xml_escape(title or "Unknown Track")
                            safe_uri = xml_escape(track_uri)
                            meta = (
                                '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
                                'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                                'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
                                'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
                                '<item id="R:0/0/0" parentID="R:0/0" restricted="true">'
                                '<dc:title>' + safe_title + '</dc:title>'
                                '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
                                '<res protocolInfo="*:*:*:*">' + safe_uri + '</res>'
                                '</item></DIDL-Lite>'
                            )
                            log(f"add_to_queue: DIDL meta for non-Spotify URI: {track_uri[:80]}")
                            if pos is not None:
                                coord.add_uri_to_queue(uri=track_uri, didl_resource_meta_data=meta, position=pos)
                            elif next_flag:
                                coord.add_uri_to_queue(uri=track_uri, didl_resource_meta_data=meta, as_next=True)
                            else:
                                coord.add_uri_to_queue(uri=track_uri, didl_resource_meta_data=meta)

                    if as_next:
                        try:
                            info = coordinator.get_current_track_info()
                            current_pos = int(info.get('playlist_position', 0))
                            insert_pos = current_pos + 1
                            log(f"Queueing as NEXT at position {insert_pos} (current={current_pos})")
                            _do_queue(coordinator, is_spotify, pos=insert_pos)
                        except Exception as pos_err:
                            log(f"Position-based queue failed ({pos_err}), falling back to as_next flag")
                            _do_queue(coordinator, is_spotify, next_flag=True)
                    else:
                        _do_queue(coordinator, is_spotify)
                    # Auto-play if nothing is currently playing
                    transport = coordinator.get_current_transport_info()
                    state = transport.get('current_transport_state', '')
                    if state in ('STOPPED', 'NO_MEDIA_PRESENT'):
                        queue_size = coordinator.queue_size
                        if queue_size > 0:
                            coordinator.play_from_queue(queue_size - 1)
                            verb = "Queued + started"
                        else:
                            verb = "Queued next" if as_next else "Queued"
                    else:
                        verb = "Queued next" if as_next else "Queued"
                    # Verify queue after add
                    try:
                        qsize = coordinator.queue_size
                        result["data"] = {"title": title, "uri": track_uri, "share_url": share_url or track_uri, "queue_size": qsize, "room": room}
                    except:
                        result["data"] = {"title": title, "uri": track_uri, "share_url": share_url or track_uri, "room": room}
                    result["success"] = True
                    result["message"] = f"{verb} '{title}' in {room} (queue: {result['data'].get('queue_size', '?')} items)"
                except Exception as e:
                    result["message"] = f"Queue error: {e}"

        elif action == "play_next":
            # [DESIGN NOTE - play_next: queue-preserving play for ANY service]
            # "Play now" non-destructive: insert track at next queue position, then skip to it.
            # After track finishes, playback resumes from previous queue position.
            # Works with Spotify URIs (via ShareLinkPlugin) AND raw Sonos URIs like Qobuz/Apple Music
            # (via soco.add_uri_to_queue directly). This is the PRIMARY play action for all services.
            # If a stream (TuneIn, radio, line-in, TV) is playing, there's no queue to insert into,
            # so fall back to full play (clear queue, add, play from 0).
            from soco.plugins.sharelink import ShareLinkPlugin
            from xml.sax.saxutils import escape as xml_escape
            track_uri   = cmd.get("uri", "")
            title       = cmd.get("title", track_uri)
            # Group rooms before playing — ensures all selected rooms play together.
            # _setup_rooms is incremental: no-op if already correct.
            dev, rooms, was_grouped = _setup_rooms(cmd, devices)
            if not dev:
                result["message"] = f"Room '{rooms[0] if rooms else '?'}' not found. Available: {list(devices.keys())}"
            elif not track_uri:
                result["message"] = "No URI provided"
            else:
                # DESIGN NOTE: Decode x-sonos-spotify: URIs to native spotify: format
                # so ShareLinkPlugin is used instead of raw DIDL (which causes "No Content")
                track_uri = _decode_sonos_spotify_uri(track_uri)
                # Determine if this is a Spotify URI (use ShareLinkPlugin) or raw Sonos URI (use add_uri_to_queue)
                is_spotify = track_uri.startswith("spotify:") or "open.spotify.com" in track_uri
                if is_spotify:
                    uri_type  = "track" if ":track:" in track_uri else "album" if ":album:" in track_uri else "playlist"
                    uri_id    = track_uri.split(":")[-1]
                    share_url = f"https://open.spotify.com/{uri_type}/{uri_id}"
                else:
                    share_url = None  # Raw Sonos URI -- no share link needed

                def _build_didl_meta(t, u):
                    """Build DIDL-Lite XML metadata for non-Spotify URIs.
                    Uses proper item IDs (R:0/0/0) so Sonos displays title correctly."""
                    return (
                        '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
                        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                        'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
                        'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
                        '<item id="R:0/0/0" parentID="R:0/0" restricted="true">'
                        '<dc:title>' + xml_escape(t or "Unknown Track") + '</dc:title>'
                        '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
                        '<res protocolInfo="*:*:*:*">' + xml_escape(u) + '</res>'
                        '</item></DIDL-Lite>'
                    )

                try:
                    coordinator = dev.group.coordinator if dev.group and dev.group.coordinator else dev
                    # Check if current source is a stream (no queue to insert into)
                    stream_prefixes = ("x-rincon-mp3radio:", "x-sonosapi-stream:", "x-sonosapi-radio:",
                                       "x-sonos-htastream:", "x-rincon-stream:", "aac:", "x-sonosapi-hls:")
                    is_stream = False
                    try:
                        media_info = coordinator.avTransport.GetMediaInfo([('InstanceID', 0)])
                        current_uri = media_info.get('CurrentURI', '') or ''
                        is_stream = any(current_uri.lower().startswith(p) for p in stream_prefixes)
                        if is_stream:
                            log(f"play_next: stream detected ({current_uri[:60]}), using full play instead of queue insert")
                    except Exception as mi_err:
                        log(f"play_next: GetMediaInfo failed ({mi_err}), assuming queue-based")

                    def _add_to_queue(coord, pos=None):
                        """Add track to queue -- Spotify via ShareLinkPlugin, others via add_uri_to_queue."""
                        if is_spotify:
                            plugin = ShareLinkPlugin(coord)
                            if pos is not None:
                                plugin.add_share_link_to_queue(share_url, position=pos)
                            else:
                                plugin.add_share_link_to_queue(share_url)
                        else:
                            meta = _build_didl_meta(title, track_uri)
                            log(f"play_next: DIDL meta for non-Spotify URI: {track_uri[:80]}")
                            if pos is not None:
                                coord.add_uri_to_queue(uri=track_uri, didl_resource_meta_data=meta, position=pos)
                            else:
                                coord.add_uri_to_queue(uri=track_uri, didl_resource_meta_data=meta, as_next=True)

                    if is_stream:
                        # Stream active -- can't insert into queue; replace the stream
                        if is_spotify:
                            # Spotify: clear queue + add via ShareLinkPlugin + play from queue
                            coordinator.clear_queue()
                            _add_to_queue(coordinator)
                            coordinator.play_from_queue(0)
                        else:
                            # Non-Spotify (Qobuz, Apple Music, etc.): play_uri() is more reliable
                            # than clear_queue + DIDL + play_from_queue which can silently fail
                            # (v1.87 fix: DIDL queue approach showed "Song [1/1]" with no audio)
                            meta = _build_didl_meta(title, track_uri)
                            log(f"play_next: using play_uri() for non-Spotify stream replacement")
                            coordinator.play_uri(track_uri, meta, title=title or '')
                    else:
                        # Queue-based source -- insert at next position and skip
                        info = coordinator.get_current_track_info()
                        current_pos = int(info.get('playlist_position', 0))
                        insert_pos = current_pos + 1
                        log(f"play_next: inserting at position {insert_pos} (current={current_pos})")
                        _add_to_queue(coordinator, pos=insert_pos)
                        try:
                            coordinator.next()
                            # DESIGN NOTE: next() on a STOPPED speaker advances queue pointer
                            # but doesn't start playback. Check and force play if needed.
                            import time as _t; _t.sleep(0.3)
                            ts = coordinator.get_current_transport_info()
                            state = ts.get('current_transport_state', '')
                            if state != 'PLAYING':
                                log(f"play_next: after next(), state={state} -> forcing play_from_queue({insert_pos - 1})")
                                coordinator.play_from_queue(insert_pos - 1)
                        except Exception as skip_err:
                            # Any next() failure -- just play directly
                            log(f"play_next: next() failed ({skip_err}), falling back to play_from_queue(0)")
                            coordinator.play_from_queue(0)
                    room_label = " + ".join(rooms) if len(rooms) > 1 else rooms[0]
                    grp_note = f" (unlinked from {', '.join(was_grouped)})" if was_grouped else ""
                    result["success"] = True
                    mode_note = "full play (was stream)" if is_stream else "queue insert"
                    svc_note = "spotify" if is_spotify else "native"

                    # DESIGN NOTE: If title is still a raw URI (e.g. "spotify:track:xxx"),
                    # Sonos hasn't resolved metadata yet. Wait briefly and re-poll.
                    artist = cmd.get("artist", "")
                    album = cmd.get("album", "")
                    if title.startswith("spotify:") or (not artist and title == track_uri):
                        time.sleep(2)
                        try:
                            resolved = get_track_info(coordinator)
                            if resolved and resolved.get("title") and not resolved["title"].startswith("spotify:"):
                                title = resolved["title"]
                                artist = artist or resolved.get("artist", "")
                                album = album or resolved.get("album", "")
                                log(f"play_next: resolved metadata after delay: '{title}' - {artist}")
                            else:
                                log(f"play_next: metadata still unresolved after 2s delay")
                        except Exception as resolve_err:
                            log(f"play_next: metadata resolve failed ({resolve_err})")

                    result["message"] = f"Playing next: '{title}' in {room_label} [{mode_note}, {svc_note}]{grp_note}"
                    result["data"] = {"title": title, "uri": track_uri, "share_url": share_url or track_uri,
                                      "was_grouped_with": was_grouped, "room": rooms[0], "rooms": rooms}
                    # DESIGN NOTE: For non-Spotify URIs, Sonos may not report track metadata
                    # (shows "No Content" in Sonos app). The polling loop's get_track_info()
                    # returns None for empty titles -> no SSE now_playing fires.
                    # Fix: publish SSE immediately from command payload so browser updates.
                    # Also inject into _last_ui_track to prevent duplicate SSE from polling loop.
                    try:
                        service_name = "Spotify" if is_spotify else detect_service(track_uri, "")
                        # Minimal payload — bundler's _sse_enrich_state() adds
                        # play_modes, rooms_playing, client_id, version, etc.
                        np_data = {
                            "title": title, "artist": artist, "album": album,
                            "rooms": rooms, "service": service_name,
                            "uri": track_uri,
                        }
                        publish_ui_event("now_playing", np_data)
                        coord_name = coordinator.player_name
                        coord_key = f"{title}|{artist}|{track_uri}"
                        _last_ui_track[coord_name] = coord_key
                        # Also inject into room_state so status_update SSE has correct data
                        room_state[coord_name] = {
                            "track_key": coord_key,
                            "track_info": {
                                "title": title, "artist": artist, "album": album,
                                "uri": track_uri, "service": service_name,
                                "rooms": rooms, "coordinator": coord_name,
                            },
                            "started_at": datetime.now(timezone.utc),
                        }
                    except Exception as sse_err:
                        log(f"play_next: SSE publish failed ({sse_err})")
                except Exception as e:
                    result["message"] = f"play_next error: {e}"

        elif action == "play_radio":
            # Play a list of Spotify track URIs as a "radio station"
            # Agent builds the list (from playlist tracks, album tracks, etc.)
            from soco.plugins.sharelink import ShareLinkPlugin
            uris = cmd.get("uris", [])
            title = cmd.get("title", "Radio")
            dev, rooms, was_grouped = _setup_rooms(cmd, devices)
            if not dev:
                result["message"] = f"Room '{rooms[0] if rooms else '?'}' not found. Available: {list(devices.keys())}"
            elif not uris:
                result["message"] = "No URIs provided"
            else:
                dev.clear_queue()
                plugin = ShareLinkPlugin(dev)
                added = 0
                for uri in uris:
                    try:
                        uri_type = "track" if ":track:" in uri else "album" if ":album:" in uri else "playlist"
                        uri_id = uri.split(":")[-1]
                        share_url = f"https://open.spotify.com/{uri_type}/{uri_id}"
                        plugin.add_share_link_to_queue(share_url)
                        added += 1
                    except Exception as e:
                        log(f"play_radio: failed to queue {uri}: {e}")
                if added > 0:
                    dev.play_mode = "NORMAL"
                    dev.play_from_queue(0)
                    result["success"] = True
                    result["message"] = f"Playing radio ({added} tracks) in {room}: {title}"
                    result["data"] = {"title": title, "queued": added}
                else:
                    result["message"] = "Failed to queue any tracks"

        elif action == "play_uri":
            # DESIGN: Generic Sonos-native URI play -- replays a track on its ORIGINAL service.
            # Used by index.html when replaying Qobuz/Apple Music/TuneIn tracks from history.
            # The raw Sonos URI (e.g. x-sonos-http:song%3a1234.mp4?sid=204) is passed through
            # directly to soco.play_uri(), which Sonos resolves via the original service.
            # This avoids Spotify search fallback for non-Spotify content.
            # Future: Apple MusicKit Atmos detection could flag tracks for Apple Music replay.
            uri   = cmd.get("uri", "")
            title = cmd.get("title", uri)
            meta  = cmd.get("meta", "")
            # DESIGN: No regrouping -- rooms already set up via tile taps.
            dev, rooms = _find_coordinator(cmd, devices)
            if not dev:
                result["message"] = f"Room '{rooms[0] if rooms else '?'}' not found. Available: {list(devices.keys())}"
            elif not uri:
                result["message"] = "No URI provided"
            else:
                dev.play_uri(uri, meta=meta, title=title)
                result["success"] = True
                room_label = " + ".join(rooms) if len(rooms) > 1 else rooms[0]
                result["message"] = f"Playing '{title}' in {room_label}"
                result["data"] = {"title": title, "uri": uri, "room": rooms[0], "rooms": rooms}

        elif action == "stop":
            rooms = cmd.get("rooms", list(devices.keys()))
            if isinstance(rooms, str): rooms = [rooms]
            stopped = []
            for r in rooms:
                dev = devices.get(r)
                if dev:
                    try: dev.stop(); stopped.append(r)
                    except: pass
            result["success"] = True
            result["message"] = f"Stopped: {', '.join(stopped)}"

        elif action == "pause":
            rooms = cmd.get("rooms", [])
            if isinstance(rooms, str): rooms = [rooms]
            # Deduplicate by coordinator — one call per group, not per room
            seen_coords = set()
            paused = []
            for r in rooms:
                dev = devices.get(r)
                if dev:
                    coord = dev.group.coordinator if dev.group else dev
                    if coord.player_name not in seen_coords:
                        seen_coords.add(coord.player_name)
                        try: coord.pause(); paused.append(r)
                        except: pass
                    else:
                        paused.append(r)
            result["success"] = True
            result["message"] = f"Paused: {', '.join(paused)}"

        elif action in ("resume", "play_resume"):
            rooms = cmd.get("rooms", [])
            if isinstance(rooms, str): rooms = [rooms]
            # Deduplicate by coordinator — one call per group, not per room
            seen_coords = set()
            resumed = []
            for r in rooms:
                dev = devices.get(r)
                if dev:
                    coord = dev.group.coordinator if dev.group else dev
                    if coord.player_name not in seen_coords:
                        seen_coords.add(coord.player_name)
                        try: coord.play(); resumed.append(r)
                        except: pass
                    else:
                        resumed.append(r)
            result["success"] = True
            result["message"] = f"Resumed: {', '.join(resumed)}"

        elif action == "next":
            rooms = cmd.get("rooms", [])
            if isinstance(rooms, str): rooms = [rooms]
            skipped = []
            for r in rooms:
                dev = devices.get(r)
                if dev:
                    try:
                        coord = dev.group.coordinator if dev.group else dev
                        coord.next(); skipped.append(r)
                    except: pass
            result["success"] = True
            result["message"] = f"Next track: {', '.join(skipped)}"

        elif action == "previous":
            rooms = cmd.get("rooms", [])
            if isinstance(rooms, str): rooms = [rooms]
            backed = []
            for r in rooms:
                dev = devices.get(r)
                if dev:
                    try:
                        coord = dev.group.coordinator if dev.group else dev
                        coord.previous(); backed.append(r)
                    except: pass
            result["success"] = True
            result["message"] = f"Previous track: {', '.join(backed)}"

        elif action == "get_volume":
            vols = {}
            for rname, dev in devices.items():
                try: vols[rname] = dev.volume
                except: pass
            result["success"] = True
            result["data"] = vols
            result["message"] = f"Volume levels for {len(vols)} rooms"

        elif action in ("set_volume", "volume"):
            room   = cmd.get("room") or (cmd.get("rooms") or [None])[0]
            volume = int(cmd.get("volume", 20))
            dev    = devices.get(room)
            if dev:
                dev.volume = volume
                result["success"] = True
                result["message"] = f"Volume -> {volume} in {room}"
            else:
                result["message"] = f"Room '{room}' not found"

        elif action == "volume_up":
            vol_rooms = cmd.get("rooms") or ([cmd["room"]] if cmd.get("room") else [])
            step  = int(cmd.get("step", 10))
            msgs = []
            for room in vol_rooms:
                dev = devices.get(room)
                if dev:
                    if dev.mute:
                        dev.mute = False  # auto-unmute on volume up
                    new_vol = min(100, dev.volume + step)
                    dev.volume = new_vol
                    msgs.append(f"{room} -> {new_vol}")
                else:
                    msgs.append(f"'{room}' not found")
            result["success"] = bool(msgs)
            result["message"] = "Volume up: " + ", ".join(msgs) if msgs else "No rooms specified"

        elif action == "volume_down":
            vol_rooms = cmd.get("rooms") or ([cmd["room"]] if cmd.get("room") else [])
            step  = int(cmd.get("step", 10))
            msgs = []
            for room in vol_rooms:
                dev = devices.get(room)
                if dev:
                    new_vol = max(0, dev.volume - step)
                    dev.volume = new_vol
                    msgs.append(f"{room} -> {new_vol}")
                else:
                    msgs.append(f"'{room}' not found")
            result["success"] = bool(msgs)
            result["message"] = "Volume down: " + ", ".join(msgs) if msgs else "No rooms specified"

        elif action == "toggle_mute":
            room = cmd.get("room") or (cmd.get("rooms") or [None])[0]
            dev  = devices.get(room)
            if dev:
                new_mute = not dev.mute
                dev.mute = new_mute
                result["success"] = True
                result["message"] = f"{'Muted' if new_mute else 'Unmuted'} {room}"
            else:
                result["message"] = f"Room '{room}' not found"

        elif action == "cycle_repeat":
            room = cmd.get("room") or (cmd.get("rooms") or [None])[0]
            dev  = devices.get(room)
            if dev:
                try:
                    cur = dev.play_mode
                    # Cycle: NORMAL -> REPEAT_ALL -> REPEAT_ONE -> NORMAL
                    # Preserve shuffle state if active
                    cycle = {
                        "NORMAL": "REPEAT_ALL",
                        "REPEAT_ALL": "REPEAT_ONE",
                        "REPEAT_ONE": "NORMAL",
                        "SHUFFLE": "SHUFFLE",           # shuffle stays as-is
                        "SHUFFLE_NOREPEAT": "SHUFFLE",  # add repeat
                        "SHUFFLE_REPEAT_ONE": "SHUFFLE_NOREPEAT",  # remove repeat
                    }
                    new_mode = cycle.get(cur, "NORMAL")
                    dev.play_mode = new_mode
                    result["success"] = True
                    result["message"] = f"Repeat: {cur} -> {new_mode} in {room}"
                except Exception as e:
                    result["message"] = f"cycle_repeat error: {e}"
            else:
                result["message"] = f"Room '{room}' not found"

        elif action == "get_services":
            room = cmd.get("room") or (cmd.get("rooms") or [None])[0]
            dev  = devices.get(room) if room else next(iter(devices.values()), None)
            out  = {"speaker": room, "services": [], "soco_accounts": []}
            try:
                from soco import music_services as ms_mod
                try:
                    for svc in ms_mod.get_all_music_services():
                        out["soco_accounts"].append({"name":svc.get("Name",""),"service_id":svc.get("Id","")})
                except Exception as e:
                    out["soco_accounts_error"] = str(e)
                if dev:
                    try:
                        for svc in dev.music_services.get_available_services():
                            entry = {"name":getattr(svc,"service_name",str(svc)),"service_id":getattr(svc,"service_id",None)}
                            try:
                                acct = svc.account
                                entry["account_sn"]       = acct.serial_number if acct else None
                                entry["account_username"] = acct.username      if acct else None
                            except: pass
                            out["services"].append(entry)
                    except Exception as e:
                        out["services_error"] = str(e)
                    try:
                        info = dev.get_current_track_info()
                        out["current_track_uri"]   = info.get("uri","")
                        out["current_track_title"] = info.get("title","")
                    except Exception as e:
                        out["current_track_error"] = str(e)
                result["success"] = True
                result["message"] = f"Services on '{room}'"
                result["data"]    = out
            except Exception as e:
                result["message"] = f"get_services error: {e}"

        elif action == "refresh":
            # Force Sonos re-discovery to get fresh topology (group state, rooms playing).
            # Typically sent by the UI on page load.
            global current_devices_by_name
            try:
                import soco as _soco
                fresh = {}
                for dev in _soco.discover(timeout=5) or []:
                    try:
                        fresh[dev.player_name] = dev
                    except Exception:
                        pass
                current_devices_by_name = fresh
                log(f"[refresh] Re-discovered {len(fresh)} speakers: {sorted(fresh.keys())}")
                result["success"] = True
                result["message"] = f"Refreshed: {len(fresh)} speakers"
            except Exception as e:
                result["message"] = f"refresh error: {e}"

        elif action == "get_queue":
            room = cmd.get("room") or (cmd.get("rooms") or [None])[0]
            dev  = devices.get(room)
            if not dev:
                result["message"] = f"Room '{room}' not found. Available: {list(devices.keys())}"
            else:
                try:
                    # Use coordinator if room is in a group
                    if dev.group and dev.group.coordinator != dev:
                        coordinator = dev.group.coordinator
                    else:
                        coordinator = dev
                    queue = coordinator.get_queue(max_items=100)
                    # Get current track position
                    track_info = coordinator.get_current_track_info()
                    current_pos = int(track_info.get("playlist_position", "0"))
                    transport = coordinator.get_current_transport_info()
                    state = transport.get("current_transport_state", "UNKNOWN")
                    items = []
                    for i, item in enumerate(queue):
                        entry = {
                            "position": i + 1,
                            "title": getattr(item, "title", ""),
                            "artist": getattr(item, "creator", ""),
                            "album": getattr(item, "album", ""),
                        }
                        if i + 1 == current_pos:
                            entry["now_playing"] = True
                        items.append(entry)
                    result["success"] = True
                    result["message"] = f"Queue for {room}: {len(items)} tracks (pos {current_pos}, {state})"
                    result["data"] = {
                        "room": room,
                        "coordinator": coordinator.player_name,
                        "queue_size": len(items),
                        "current_position": current_pos,
                        "transport_state": state,
                        "items": items,
                    }
                except Exception as e:
                    result["message"] = f"get_queue error: {e}"

        else:
            result["message"] = f"Unknown action: {action}"

    except Exception as e:
        result["message"] = f"Error: {e}"
        post_error(f"Command error ({action}): {e}", context=f"cmd_id={cmd_id}", module="sonos")

    # Stamp t_playing immediately after successful play command execution
    if result.get("success") and action in ("play_spotify_uri", "play_album", "play", "play_radio",
                                             "play_next", "play_uri", "queue_next", "queue", "add_to_queue", "search_and_play"):
        result["t_playing"] = now_iso()

    # Brief delay so speakers transition to PLAYING state before we query
    if result.get("success") and action in ("play_spotify_uri", "play_album", "play", "play_next", "play_uri"):
        time.sleep(2)

    # Piggyback heartbeat + any buffered history on this command result
    result["heartbeat"] = heartbeat_fields()

    # Ensure the just-commanded room appears in rooms_playing after a successful play
    if result.get("success") and action in ("play_spotify_uri", "play_album", "play", "play_next", "play_uri"):
        rp = result["heartbeat"].get("rooms_playing", [])
        cmd_room = result.get("data", {}).get("room") if isinstance(result.get("data"), dict) else None
        if not cmd_room:
            # Extract room from the action's rooms list if data doesn't have it
            cmd_room = result.get("data", {}).get("rooms", [None])[0] if isinstance(result.get("data"), dict) else None
        if cmd_room and cmd_room not in rp:
            rp.append(cmd_room)
            result["heartbeat"]["rooms_playing"] = sorted(rp)
            log(f"[sonos] Injected {cmd_room} into rooms_playing (post-play)")
    with pending_buffer_lock:
        if pending_buffer:
            result["pending_history"] = list(pending_buffer)
            pending_buffer.clear()
    if result.get("pending_history"):
        try: PENDING_PATH.write_text(json.dumps(result["pending_history"]), encoding="utf-8")
        except: pass
    result["t_result_sent"] = now_iso()

    # Record structured command outcome (ALL commands, silent or not)
    record_command_result(
        action=action,
        success=result.get("success", False),
        message=result.get("message", ""),
        cmd_ts=cmd.get("cmd_ts"),
        detail=result.get("data", {}).get("room") if isinstance(result.get("data"), dict) else None,
    )

    # Silent actions (volume, etc.) -- log locally, skip webhook POST entirely
    if is_silent:
        log(f"Command (silent): {result['message']}")
        return

    # Include SSE relay data for server-side relay to ntfy
    sse_relay = build_sse_relay_payload()
    if sse_relay:
        result["sse_relay"] = sse_relay
    result["heartbeat"] = heartbeat_fields()

    try:
        r = requests.post(WEBHOOK, json=result, timeout=15)
        log(f"Command result -> HTTP {r.status_code}: {result['message']}")
        last_post_ts = time.time()
        if result.get("pending_history"):
            try: PENDING_PATH.unlink(missing_ok=True)
            except: pass
    except Exception as e:
        log(f"Failed to post command result: {e}")
        # Restore piggybacked history to buffer on failure
        if result.get("pending_history"):
            with pending_buffer_lock:
                pending_buffer[:0] = result["pending_history"]

# --- SONOS: GITHUB CMD FALLBACK ---------------------------------------------
def poll_commands():
    global last_cmd_sha
    r = gh_get(f"sonos_cmd_{house}.json")
    if not r: return
    data = r.json()
    sha  = data.get("sha")
    if sha == last_cmd_sha: return
    try:
        cmd = json.loads(gh_decode(r))
    except: return
    last_cmd_sha = sha
    action = cmd.get("action", "")
    if action in ("none", "", "idle"): return
    # Age guard: skip commands older than 5 minutes to prevent replay on restart
    cmd_ts = cmd.get("cmd_ts")
    if cmd_ts:
        age = time.time() - cmd_ts
        if age > 300:
            log(f"GitHub fallback: stale command (age={int(age)}s): {action}")
            return
    if _already_executed(cmd):
        log(f"GitHub fallback: duplicate (ntfy ran it): {action}")
        return
    _mark_executed(cmd)
    log(f"New command (GitHub fallback): {cmd}")
    execute_command(cmd, source="github")
    # Clear the command file after execution to prevent replay on restart
    _clear_github_cmd(sha)

def _clear_github_cmd(sha):
    """Replace the GitHub command file with idle after successful execution."""
    path = f"sonos_cmd_{house}.json"
    idle = json.dumps({"action": "idle", "cleared_at": time.time()})
    body = {
        "message": "clear command after execution",
        "content": base64.b64encode(idle.encode()).decode(),
        "sha": sha,
    }
    try:
        url = f"{GITHUB_API_BASE}/{path}"
        r = requests.put(url, headers=gh_headers(), json=body, timeout=15)
        if r.status_code in (200, 201):
            log(f"GitHub fallback: cleared command file (was used! ntfy missed this one)")
        else:
            log(f"GitHub fallback: failed to clear command file: HTTP {r.status_code}")
    except Exception as e:
        log(f"GitHub fallback: error clearing command file: {e}")

# --- NTFY LISTENER THREAD ---------------------------------------------------
# [ROLLBACK-UNSAFE] Receives update_check commands from ntfy and dispatches to
# execute_command() -> self_update_check(). The old version's parsing + dispatch runs here.
def ntfy_listener_thread():
    global _ntfy_connected, _ntfy_reconnects
    log(f"ntfy listener: topic={ntfy_topic}")
    while True:
        # Use since=5m so commands sent during restart/reconnect gaps are caught.
        # In-memory dedup (_already_executed) prevents double-execution within same process.
        url   = f"https://ntfy.sh/{ntfy_topic}/json?since=5m"
        log(f"ntfy connecting: {url}")
        try:
            with requests.get(url, stream=True, timeout=90) as r:
                _ntfy_connected = True
                for line in r.iter_lines():
                    if not line: continue
                    try:    msg = json.loads(line)
                    except: continue
                    if msg.get("event") != "message": continue
                    raw = msg.get("message", "")
                    log(f"[!] ntfy: {raw[:120]}")
                    try:
                        cmd    = json.loads(raw)
                        # Use ntfy server timestamp (always correct, in seconds)
                        ntfy_ts = msg.get("time", 0)
                        age     = time.time() - ntfy_ts if ntfy_ts else 0
                        if ntfy_ts and age > 300:
                            log(f"Stale command ({int(age)}s old): {cmd.get('action')}")
                            continue
                        if _already_executed(cmd):
                            log(f"Duplicate: {cmd.get('action')}")
                            continue
                        _mark_executed(cmd)
                        execute_command(cmd, source="ntfy")
                    except Exception as e:
                        log(f"ntfy parse/execute error: {e}")
        except Exception as e:
            _ntfy_connected = False
            _ntfy_reconnects += 1
            log(f"ntfy stream error: {e} -- reconnecting in 5s (reconnects: {_ntfy_reconnects})")
            time.sleep(5)

# --- SONOS MAIN LOOP --------------------------------------------------------
def sonos_main_loop():
    global current_devices_by_name

    _ensure("soco")
    import soco

    log(f"Sonos polling every {POLL_INTERVAL}s | GitHub fallback every ~{POLL_INTERVAL*CMD_POLL_EVERY}s")
    log("Scanning for Sonos speakers...")

    cmd_counter = 0
    first_run   = True

    while True:
        try:
            coordinators = get_coordinators()
            # Build flat device map for commands (before ready heartbeat so SSE has mute data)
            all_devices = {}
            try:
                now_t = time.time()
                for dev in soco.discover(timeout=5) or []:
                    ip = dev.ip_address
                    if ip in _offline_ips and now_t - _offline_ips[ip] < OFFLINE_RECHECK_SECS:
                        continue
                    try:
                        all_devices[dev.player_name] = dev
                    except Exception:
                        _offline_ips[ip] = now_t
            except: pass
            current_devices_by_name = all_devices

            if first_run:
                names = [d.player_name for d in coordinators]
                log(f"Found {len(names)} coordinator(s): {', '.join(names)}" if names
                    else "No speakers found -- retrying...")
                first_run = False
                # -- Startup "ready" heartbeat -- Sonos discovered, full state available
                try:
                    log("Sending startup ready heartbeat...")
                    rdy_payload = {"type": "heartbeat", "startup_phase": "ready"}
                    rdy_payload.update(heartbeat_fields())
                    requests.post(WEBHOOK, json=rdy_payload, timeout=10)
                    log("Ready heartbeat sent")
                    # Send SSE status_update so browser status bar goes green immediately
                    publish_ui_event("status_update", {})
                    log("Startup SSE status_update sent")
                except Exception as e:
                    log(f"Ready heartbeat failed: {e}")

            now = datetime.now(timezone.utc)

            seen_rooms = set()
            for dev in coordinators:
                info = get_track_info(dev)
                try:    rooms_in_group = [m.player_name for m in dev.group.members]
                except: rooms_in_group = [dev.player_name]

                # -- Real-time UI push (ntfy SSE) -------------------------
                coord_name = dev.player_name
                if info:
                    coord_key = f"{info['title']}|{info['artist']}|{info['uri']}"
                    if coord_key != _last_ui_track.get(coord_name):
                        _last_ui_track[coord_name] = coord_key
                        # Minimal payload — bundler's _sse_enrich_state() adds
                        # play_modes, rooms_playing, client_id, version, etc.
                        np_data = {
                            "title": info["title"], "artist": info["artist"],
                            "album": info["album"], "rooms": rooms_in_group,
                            "service": info.get("service", ""),
                            "uri": info.get("uri", ""),
                        }
                        publish_ui_event("now_playing", np_data)
                else:
                    if coord_name in _last_ui_track:
                        del _last_ui_track[coord_name]
                        # If no coordinators playing at all, send stopped
                        if not _last_ui_track:
                            publish_ui_event("stopped", {"rooms": rooms_in_group})

                for room in rooms_in_group:
                    seen_rooms.add(room)
                    prev = room_state.get(room)
                    if info:
                        track_key = f"{info['title']}|{info['artist']}|{info['uri']}"
                        if prev is None or prev.get("track_key") != track_key:
                            if prev and prev.get("track_key") and prev.get("started_at"):
                                post_history(prev["track_info"], room, prev["started_at"], now)
                            room_state[room] = {"track_key": track_key, "track_info": info, "started_at": now}
                            log(f'> {room}: "{info["title"]}" - {info["artist"]} [{info["service"]}]')
                            # Track change detection: commanded (within 8s of a command) vs organic (user/app)
                            _is_commanded = (time.time() - _last_command_at < 8)
                            _track_changes.append({
                                "room": room, "at": time.time(),
                                "track": f'{info["title"]} - {info["artist"]}',
                                "commanded": _is_commanded
                            })
                            if len(_track_changes) > 10:
                                _track_changes.pop(0)
                            schedule_state_push()  # push state-{house}.json on track change
                    else:
                        was_playing = prev and prev.get("track_key")
                        if was_playing and prev.get("started_at"):
                            post_history(prev["track_info"], room, prev["started_at"], now)
                        room_state[room] = None
                        if was_playing:
                            schedule_state_push()  # push on playing->stopped transition only

            # Rooms that disappeared from network
            for room in list(room_state.keys()):
                if room not in seen_rooms:
                    prev = room_state.get(room)
                    if prev and prev.get("track_key") and prev.get("started_at"):
                        post_history(prev["track_info"], room, prev["started_at"], now)
                    room_state[room] = None

            # Clean up _last_ui_track for coordinators that vanished from network
            # Without this, stopped event never fires if coordinator disappears
            seen_coords = {dev.player_name for dev in coordinators}
            stale_coords = [c for c in _last_ui_track if c not in seen_coords]
            for c in stale_coords:
                del _last_ui_track[c]
                log(f"[UI] Cleaned stale _last_ui_track for disappeared coordinator: {c}")
            if stale_coords and not _last_ui_track:
                publish_ui_event("stopped", {"rooms": []})

            cmd_counter += 1
            if cmd_counter >= CMD_POLL_EVERY:
                poll_commands()
                cmd_counter = 0

            # -- Change-driven status_update SSE + 15-min keepalive (v1.70) --
            # DESIGN NOTE: With the bundled architecture, every outbound message
            # carries full state via _sse_enrich_state(). Track changes provide
            # natural state updates every 3-5 min. The status_update here only
            # fires on room-state changes or as a 15-min keepalive (~96/day).
            # state data (rooms_playing, play_modes, now_playing_tracks) is
            # injected automatically by the bundler's _sse_enrich_state().
            global _sse_status_counter, _last_sse_rooms_playing, _last_sse_mute_states
            _sse_status_counter += 1
            rp = get_rooms_playing()
            rooms_changed = (rp != _last_sse_rooms_playing)
            mutes_changed = (dict(_current_mute_states) != _last_sse_mute_states)
            keepalive_due = (_sse_status_counter >= 60)  # 60 x 15s = 15 min (~96/day)
            if rooms_changed or mutes_changed or keepalive_due:
                _sse_status_counter = 0
                # Minimal payload — _sse_enrich_state() in the bundler adds
                # rooms_playing, play_modes, mute_states, now_playing_tracks, etc.
                publish_ui_event("status_update", {})
                _last_sse_rooms_playing = rp
                _last_sse_mute_states = dict(_current_mute_states)

        except Exception as e:
            msg = f"Sonos loop error: {e}"
            log(msg)
            # Suppress speaker connectivity errors from webhook -- transient and noisy
            err_str = str(e).lower()
            if any(k in err_str for k in ("timed out", "max retries", "connection refused", "connectionpool")):
                pass  # log only, no webhook POST
            else:
                post_error(msg, module="sonos")

        # --- Diagnostic status block (change-driven) ---
        _log_diagnostic_status()

        time.sleep(POLL_INTERVAL)

# --- MAIN -------------------------------------------------------------------
# [ROLLBACK-UNSAFE] main() through self_update_check() call (line ~1644).
# The mutex guard, rollback detection, sleep prevention, config logging, and
# the startup self_update_check() all run in the old version before handoff.
def main():
    # -- Single-instance guard (Windows named mutex) --------------------------
    # Prevents multiple copies running simultaneously (e.g. after self-update race).
    # Stored as global so self_update_check() can release it before spawning new process.
    global _mutex_handle
    _mutex_handle = None
    try:
        import ctypes
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, "Global\\LifeLogServiceMutex")
        ERROR_ALREADY_EXISTS = 183
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            log("Another LifeLog instance is already running. Exiting.")
            sys.exit(0)
    except Exception as e:
        log(f"Warning: single-instance check failed ({e}) -- proceeding anyway")

    global _service_start_ts
    _service_start_ts = time.time()
    log(f"LifeLog Unified Service v{SERVICE_VERSION} starting")

    # -- Self-update rollback detection ----------------------------------------
    # Two-phase flag: self_update_check() writes "update_in_progress".
    # First start after update renames it to "update_started".
    # If we see "update_started" it means the LAST update crashed -- roll back.
    script_path = Path(sys.argv[0]).resolve()
    flag_dir = script_path.parent
    bak_path = script_path.with_suffix(".py.bak")
    flag_in_progress = flag_dir / "update_in_progress"
    flag_started = flag_dir / "update_started"

    if flag_started.exists() and bak_path.exists():
        # Previous update crashed before confirming -- ROLLBACK
        old_info = flag_started.read_text(encoding="utf-8").strip()
        log(f"ROLLBACK: Previous update crashed (info: {old_info}). Restoring backup...")
        try:
            import shutil
            shutil.copy2(str(bak_path), str(script_path))
            bak_path.unlink(missing_ok=True)
            flag_started.unlink(missing_ok=True)
            flag_in_progress.unlink(missing_ok=True)
            # Write skip_version so the restored version doesn't immediately
            # re-download the same bad version via startup self_update_check()
            try:
                skip_path = flag_dir / "skip_version"
                failed_ver = old_info.split("|")[-1] if "|" in old_info else old_info
                skip_path.write_text(f"{failed_ver}|2", encoding="utf-8")
                log(f"skip_version written: {failed_ver}|2 (prevent re-download)")
            except Exception:
                pass
            log("Rollback complete -- restarting with previous version...")
            post_error(f"Self-update rollback triggered (info: {old_info}). Reverted to backup.", module="update")
            # Release mutex before respawning
            if _mutex_handle is not None:
                try:
                    import ctypes as _ct2
                    _ct2.windll.kernel32.CloseHandle(_mutex_handle)
                    _mutex_handle = None
                except Exception:
                    pass
            subprocess.Popen(
                [sys.executable, str(script_path)] + sys.argv[1:],
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            )
            os._exit(0)
        except Exception as rbe:
            log(f"ROLLBACK FAILED: {rbe} -- continuing with current version")
    elif flag_in_progress.exists():
        # First start after update -- rename flag to "started" (phase 2)
        info = flag_in_progress.read_text(encoding="utf-8").strip()
        log(f"Post-update first start (info: {info}). Will confirm after init.")
        try:
            flag_in_progress.rename(flag_started)
        except Exception:
            flag_in_progress.unlink(missing_ok=True)
            flag_started.write_text(info, encoding="utf-8")
    else:
        # -- Orphan cleanup -----------------------------------------------
        # Flag exists without .bak -> stale flag, can't rollback anyway
        if flag_started.exists() and not bak_path.exists():
            log(f"Cleaning orphaned update_started flag (no .bak found)")
            flag_started.unlink(missing_ok=True)
        if flag_in_progress.exists() and not bak_path.exists():
            log(f"Cleaning orphaned update_in_progress flag (no .bak found)")
            flag_in_progress.unlink(missing_ok=True)
        # .bak without any flag -> previous update confirmed, orphaned backup
        if bak_path.exists() and not flag_started.exists() and not flag_in_progress.exists():
            log(f"Cleaning orphaned .bak file (no update flags found)")
            bak_path.unlink(missing_ok=True)

    # Prevent Windows from sleeping while service is running.
    # Close the service window when you want the PC to sleep normally.
    try:
        import ctypes
        ES_CONTINUOUS      = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        log("Sleep prevention: active (display may still turn off)")
    except Exception as e:
        log(f"Sleep prevention: unavailable ({e})")

    log(f"Computer: {computer} | House: {house} | Modules: {modules}")
    log(f"ntfy topic: {ntfy_topic}")
    if gh_token:
        log("GitHub token: configured (5000 req/hr)")
    else:
        log("GitHub token: not set (60 req/hr unauthenticated)")

    # Check for updates at startup
    self_update_check()

    # Load state ring buffer (cross-device state.json)
    _load_state_ring_buffer()

    # Drain any crash-persisted pending history
    if PENDING_PATH.exists():
        try:
            saved = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
            if saved:
                log(f"Recovering {len(saved)} buffered track(s) from crash...")
                with pending_buffer_lock:
                    pending_buffer.extend(saved)
                try: PENDING_PATH.unlink(missing_ok=True)
                except: pass
                flush_buffer(reason="crash-recovery")
        except Exception as e:
            log(f"Warning: couldn't load crash buffer: {e}")

    # Start background threads
    # v1.83: buffer_monitor_thread removed — direct SSE replaces real-time relay need.
    # Heartbeat thread (60 min) handles hourly batch flush of pending history.
    threads_to_start = [
        threading.Thread(target=heartbeat_thread,     daemon=True, name="heartbeat"),
        threading.Thread(target=version_check_thread, daemon=True, name="version"),
        threading.Thread(target=ntfy_listener_thread, daemon=True, name="ntfy"),
    ]
    if "backup" in modules:
        threads_to_start.append(threading.Thread(target=backup_thread, daemon=True, name="backup"))
    if "dev" in modules:
        threads_to_start.append(threading.Thread(target=dev_loop_thread, daemon=True, name="devloop"))

    for t in threads_to_start:
        t.start()
        log(f"Thread started: {t.name}")

    # -- Confirm successful update (clear rollback flags) --------------------
    # All threads started, Sonos about to run -- the update is good.
    if flag_started.exists():
        log(f"Update confirmed successful -- clearing rollback files")
        flag_started.unlink(missing_ok=True)
        flag_in_progress.unlink(missing_ok=True)
        bak_path.unlink(missing_ok=True)

    # -- Startup "boot" heartbeat -- immediate visibility after upgrade --------
    try:
        log("Sending startup boot heartbeat...")
        payload = {"type": "heartbeat", "startup_phase": "boot"}
        payload.update(heartbeat_fields())
        requests.post(WEBHOOK, json=payload, timeout=10)
        log("Boot heartbeat sent")
    except Exception as e:
        log(f"Boot heartbeat failed: {e}")

    # Sonos runs on main thread (visible activity in console)
    if "sonos" in modules:
        try:
            sonos_main_loop()
        except Exception as e:
            post_error(f"Sonos module fatal crash: {e}",
                       context=traceback.format_exc(), module="sonos")
            log(f"Sonos crashed: {e}")

    # No Sonos -- keep alive
    log("Service running (no Sonos). Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log("Stopping.")

if __name__ == "__main__":
    main()
