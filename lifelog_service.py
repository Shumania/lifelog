#!/usr/bin/env python3
"""
LifeLog Unified Service v1.0
Replaces LifeLog-BackupService.ps1 + sonos_service.py

Modules (set in lifelog_config.json):
  - sonos:  real-time Sonos listening history + remote control
  - backup: periodic iPhone backup extraction (calls lifelog_extract.py)
  - dev:    GitHub dev_next.ps1 remote-control loop

Config: C:\ProgramData\LifeLog\lifelog_config.json
{
  "house": "caphill",
  "modules": ["sonos", "backup", "dev"],
  "github_token": ""    <- optional, raises API rate limit 60->5000/hr
}
Falls back to sonos_config.json if lifelog_config.json not found.
"""

import sys
import json
import time
import hashlib
import base64
import os
import threading
import subprocess
import traceback
from datetime import datetime, timezone
from pathlib import Path

if sys.version_info < (3, 8):
    print("ERROR: Python 3.8+ required")
    sys.exit(1)

def _ensure(pkg, import_as=None):
    try:
        __import__(import_as or pkg)
    except ImportError:
        print(f"Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

_ensure("requests")
import requests

# ─── CONSTANTS ──────────────────────────────────────────────────────────────
SERVICE_VERSION = "1.7"
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

# WiFi SSID → house mapping (overrides config file setting)
WIFI_HOUSE_MAP = {
    "shumickernet": "caphill",
    "coconetz":     "vashon",
}

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
HEARTBEAT_FALLBACK_SECS    = 3600  # standalone heartbeat only if no POST in 60 min
HEARTBEAT_QUIET_SLEEP      = 1800  # 30 min retry during quiet hours
ACTIVITY_WINDOW            = 7200  # "active" if Sonos track in last 2h
VERSION_CHECK_INTERVAL     = 3600  # 60 min
BACKUP_INTERVAL            = 3600  # run extract every 60 min
DEV_POLL_INTERVAL          = 100   # dev_next.ps1 poll (s)
OFFLINE_THRESHOLD          = 3
OFFLINE_RECHECK_SECS       = 300
BATCH_SIZE                 = 5     # flush buffer when this many tracks queued
BATCH_TRAILING_SECS        = 300   # flush 5 min after last track was added

# ─── CONFIG ─────────────────────────────────────────────────────────────────
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
                # sonos_commander: this machine executes unaddressed Sonos commands
                # Set False on non-primary machines sharing the same house network
                if "sonos_commander" not in cfg:
                    cfg["sonos_commander"] = True
                return cfg
            except Exception as e:
                print(f"Config parse error ({p}): {e}")
    print("WARNING: No config found. Using defaults.")
    return {"house": "caphill", "modules": ["sonos", "backup", "dev"],
            "ntfy_topic": NTFY_TOPICS["caphill"]}

config          = load_config()
house           = config["house"]
modules         = config["modules"]
ntfy_topic      = config["ntfy_topic"]
gh_token        = config.get("github_token", "")
computer        = os.environ.get("COMPUTERNAME", house)
sonos_commander = config.get("sonos_commander", True)
client_id       = f"lifelog_{computer.lower()}"   # canonical ID used in heartbeats

# ─── ACTIVE HOURS ───────────────────────────────────────────────────────────
def seattle_hour():
    """Return current hour in Seattle time (America/Los_Angeles)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).hour
    except ImportError:
        try:
            import pytz
            return datetime.now(pytz.timezone("America/Los_Angeles")).hour
        except ImportError:
            # Fallback: approximate UTC-8 (PDT = UTC-7, PST = UTC-8)
            return (datetime.now(timezone.utc).hour - 8) % 24

def is_active_hours():
    """Returns True if Seattle time is 7 AM–10 PM."""
    return 7 <= seattle_hour() < 22

# ─── GLOBAL SONOS STATE ─────────────────────────────────────────────────────
current_devices_by_name  = {}
room_state               = {}
speaker_failures         = {}
speaker_offline_since    = {}
last_cmd_sha             = None
executed_cmd_hashes      = set()
MAX_EXECUTED_HASHES      = 30
last_sonos_activity_ts   = 0.0  # updated when a track is buffered
last_track_added_ts      = 0.0  # updated when a track is added to buffer
last_post_ts             = 0.0  # updated whenever any POST succeeds
pending_buffer           = []   # tracks waiting to be flushed
pending_buffer_lock      = threading.Lock()
PENDING_PATH             = INSTALL_DIR / "pending_history.json"

# ─── UTILITIES ──────────────────────────────────────────────────────────────
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg):
    print(f"[{now_iso()}] {msg}", flush=True)

def gh_headers():
    h = {"Accept": "application/vnd.github.v3+json", "User-Agent": "LifeLog-Service"}
    if gh_token:
        h["Authorization"] = f"token {gh_token}"
    return h

def gh_get(path):
    """GET a file from GitHub API. Returns response object or None."""
    url = f"{GITHUB_API_BASE}/{path}"
    try:
        r = requests.get(url, headers=gh_headers(), timeout=15)
        return r if r.status_code == 200 else None
    except Exception as e:
        log(f"gh_get error ({path}): {e}")
        return None

def gh_decode(r):
    """Decode base64 GitHub API file content."""
    b64 = r.json().get("content", "").replace("\n", "")
    return base64.b64decode(b64).decode("utf-8")

# ─── ERROR REPORTING ────────────────────────────────────────────────────────
def post_error(message, context="", module="service"):
    """POST error to webhook so agent sees it immediately."""
    payload = {
        "type":     "sonos_error",
        "house":    house,
        "computer": computer,
        "message":  message,
        "context":  str(context)[:500],
        "module":   module,
        "version":  SERVICE_VERSION,
        "timestamp": now_iso(),
    }
    try:
        requests.post(WEBHOOK, json=payload, timeout=10)
    except Exception:
        pass

# ─── SELF-UPDATE ────────────────────────────────────────────────────────────
def self_update_check():
    """Check versions.json; download + restart if service_version changed."""
    try:
        r = gh_get("versions.json")
        if not r:
            log("Version check: GitHub unavailable")
            return
        versions = json.loads(gh_decode(r))
        latest = versions.get("service_version", SERVICE_VERSION)
        if latest == SERVICE_VERSION:
            log(f"Version OK (v{SERVICE_VERSION})")
            return
        log(f"Update: v{SERVICE_VERSION} → v{latest}. Downloading...")
        r2 = gh_get("lifelog_service.py")
        if not r2:
            log("Download failed")
            post_error(f"Failed to download update v{latest}", module="update")
            return
        new_code  = gh_decode(r2)
        this_path = Path(sys.argv[0]).resolve()
        this_path.write_text(new_code, encoding="utf-8")
        log(f"Updated to v{latest} — restarting in new window...")
        subprocess.Popen(
            [sys.executable, str(this_path)] + sys.argv[1:],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        )
        sys.exit(0)
    except Exception as e:
        log(f"Self-update error: {e}")
        post_error(f"Self-update error: {e}", module="update")

# ─── HEARTBEAT HELPERS ──────────────────────────────────────────────────────
def heartbeat_fields():
    """Return standard heartbeat dict to embed in any outbound payload."""
    return {
        "client_id":       client_id,
        "client_type":     "lifelog_service",
        "house":           house,
        "version":         SERVICE_VERSION,
        "modules":         modules,
        "computer":        computer,
        "sonos_capable":   "sonos" in modules,
        "sonos_commander": sonos_commander if "sonos" in modules else False,
        "timestamp":       now_iso(),
    }

def _send_heartbeat():
    payload = {"type": "heartbeat"}
    payload.update(heartbeat_fields())
    try:
        r = requests.post(WEBHOOK, json=payload, timeout=10)
        log(f"♥ Heartbeat (standalone) → HTTP {r.status_code}")
    except Exception as e:
        log(f"Heartbeat failed: {e}")

# ─── BUFFER FLUSH ────────────────────────────────────────────────────────────
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

    payload = {
        "type":      "sonos_history_batch",
        "house":     house,
        "items":     items,
        "heartbeat": heartbeat_fields(),
    }
    try:
        r = requests.post(WEBHOOK, json=payload, timeout=20)
        log(f"✓ Flushed {len(items)} track(s) [{reason}] → HTTP {r.status_code}")
        last_post_ts = time.time()
        try: PENDING_PATH.unlink(missing_ok=True)
        except: pass
    except Exception as e:
        log(f"✗ Flush failed [{reason}]: {e} — restoring {len(items)} item(s) to buffer")
        with pending_buffer_lock:
            pending_buffer[:0] = items  # prepend back

# ─── HEARTBEAT THREAD ───────────────────────────────────────────────────────
def heartbeat_thread():
    """Fallback heartbeat: fires only if no other POST has gone out in HEARTBEAT_FALLBACK_SECS.
    During active sessions, every flush/command result carries heartbeat fields inline,
    so this thread mostly sleeps."""
    global last_post_ts

    # Always send on startup so status shows online immediately
    _send_heartbeat()
    last_post_ts = time.time()

    while True:
        time.sleep(60)  # check every minute

        if not is_active_hours():
            log(f"♥ Heartbeat: quiet hours (Seattle {seattle_hour():02d}:xx) — paused")
            time.sleep(HEARTBEAT_QUIET_SLEEP)
            continue

        since_last = time.time() - last_post_ts
        if since_last < HEARTBEAT_FALLBACK_SECS:
            continue  # a flush or command result posted recently — no heartbeat needed

        # Nothing sent in 60 min — flush pending buffer (carries heartbeat) or send standalone
        with pending_buffer_lock:
            has_pending = len(pending_buffer) > 0
        if has_pending:
            flush_buffer(reason="heartbeat-fallback")
        else:
            _send_heartbeat()
            last_post_ts = time.time()
        log(f"♥ Heartbeat: fallback fired (idle {int(since_last//60)} min) — next check in 60s")

# ─── BUFFER MONITOR THREAD ──────────────────────────────────────────────────
def buffer_monitor_thread():
    """Flush buffer on trailing-edge timer: 5 min after last track was added."""
    while True:
        time.sleep(30)  # check every 30s
        with pending_buffer_lock:
            count = len(pending_buffer)
        if count == 0:
            continue
        since_last_track = time.time() - last_track_added_ts
        if since_last_track >= BATCH_TRAILING_SECS:
            flush_buffer(reason="trailing-edge")

# ─── VERSION CHECK THREAD ───────────────────────────────────────────────────
def version_check_thread():
    time.sleep(120)  # wait 2 min after start
    while True:
        self_update_check()
        time.sleep(VERSION_CHECK_INTERVAL)

# ─── BACKUP MODULE THREAD ───────────────────────────────────────────────────
def backup_thread():
    """Run lifelog_extract.py every hour; it handles cursor/hash dedup internally."""
    extract = INSTALL_DIR / "lifelog_extract.py"

    def run_extract():
        if not extract.exists():
            log("Backup: lifelog_extract.py not found — skipping")
            return
        log("Backup: running lifelog_extract.py...")
        try:
            result = subprocess.run(
                [sys.executable, str(extract)],
                capture_output=True, text=True, timeout=900
            )
            output = (result.stdout + result.stderr).strip()
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

# ─── DEV LOOP THREAD ────────────────────────────────────────────────────────
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
                        capture_output=True, text=True, timeout=120
                    )
                    output   = proc.stdout + proc.stderr
                    last_sha = sha
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
        except Exception as e:
            log(f"[dev] Poll error: {e}")
        time.sleep(DEV_POLL_INTERVAL)

# ─── SONOS: SERVICE DETECTION ───────────────────────────────────────────────
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

# ─── SONOS: DISCOVERY ───────────────────────────────────────────────────────
def get_coordinators():
    try:
        import soco
        devices = soco.discover(timeout=8)
        if not devices: return []
        coordinators = {}
        for dev in devices:
            try:
                g = dev.group
                if g and dev == g.coordinator:
                    coordinators[dev.player_name] = dev
            except Exception:
                coordinators[dev.player_name] = dev
        return list(coordinators.values())
    except Exception as e:
        log(f"Discovery error: {e}")
        return []

# ─── SONOS: TRACK INFO ──────────────────────────────────────────────────────
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
        if not title or title == "NOT_IMPLEMENTED":
            speaker_failures[name] = 0
            return None
        uri      = info.get("uri", "")
        metadata = info.get("metadata", "")
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
        return {"title": title, "artist": info.get("artist","").strip(),
                "album": info.get("album","").strip(), "uri": uri,
                "service": detect_service(uri, metadata),
                "duration_seconds": dur_secs, "rooms": members,
                "coordinator": device.player_name}
    except Exception as e:
        failures = speaker_failures.get(name, 0) + 1
        speaker_failures[name] = failures
        if failures == OFFLINE_THRESHOLD:
            speaker_offline_since[name] = now_epoch
            msg = f"Speaker '{name}' offline after {failures} failures: {e}"
            log(f"⚠ {msg}")
            post_error(msg, context=f"speaker={name}", module="sonos")
        elif failures < OFFLINE_THRESHOLD:
            log(f"Error from {name} (attempt {failures}): {e}")
        return None

# ─── SONOS: POST HISTORY (buffered) ─────────────────────────────────────────
def post_history(track, room, started_at, ended_at):
    global last_sonos_activity_ts, last_track_added_ts
    duration_played = int((ended_at - started_at).total_seconds())
    if duration_played < 15: return
    last_sonos_activity_ts = time.time()
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
    with pending_buffer_lock:
        pending_buffer.append(item)
        last_track_added_ts = time.time()
        count = len(pending_buffer)
    log(f'+ Buffered: "{track["title"]}" – {track["artist"]} | {room} ({duration_played}s) [buffer: {count}]')
    if count >= BATCH_SIZE:
        flush_buffer(reason="count")

# ─── SONOS: MULTI-MACHINE TARGETING ─────────────────────────────────────────
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
            log(f"⏭ Skipping (targeted to {target}, we are {client_id})")
        return mine
    else:
        if not sonos_commander:
            log(f"⏭ Skipping unaddressed command (not commander): {cmd.get('action')}")
        return sonos_commander

# ─── SONOS: COMMAND DEDUP ───────────────────────────────────────────────────
def _cmd_hash(cmd):
    stable = {k: v for k, v in cmd.items() if k != "cmd_ts"}
    return hashlib.md5(json.dumps(stable, sort_keys=True).encode()).hexdigest()

def _mark_executed(cmd):
    executed_cmd_hashes.add(_cmd_hash(cmd))
    if len(executed_cmd_hashes) > MAX_EXECUTED_HASHES:
        for h in list(executed_cmd_hashes)[:MAX_EXECUTED_HASHES // 2]:
            executed_cmd_hashes.discard(h)

def _already_executed(cmd):
    return _cmd_hash(cmd) in executed_cmd_hashes

# ─── SONOS: EXECUTE COMMAND ─────────────────────────────────────────────────
def execute_command(cmd):
    action = cmd.get("action", "")
    cmd_id = cmd.get("cmd_id", "")
    if action in ("none", "", "idle") or cmd_id == "idle":
        return

    # update_check is always self-targeted (every machine updates itself)
    if action != "update_check" and not is_my_command(cmd):
        return

    # Ack immediately
    try:
        requests.post(WEBHOOK, json={"type":"sonos_ack","cmd_id":cmd_id,
                                     "action":action,"house":house,"timestamp":now_iso()}, timeout=10)
        log(f"✓ Ack: {action}")
    except Exception: pass

    result = {"type":"sonos_result","cmd_id":cmd_id,"action":action,"house":house,
              "success":False,"message":"","data":None}

    try:
        devices = current_devices_by_name

        if action == "update_check":
            result["success"] = True
            result["message"] = f"Running update check (v{SERVICE_VERSION})"
            def _do(): time.sleep(2); self_update_check()
            threading.Thread(target=_do, daemon=True).start()

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

        elif action == "group":
            source    = cmd.get("source")
            add_rooms = cmd.get("add", [])
            if isinstance(add_rooms, str): add_rooms = [add_rooms]
            master = devices.get(source)
            if not master:
                result["message"] = f"Room '{source}' not found"
            else:
                joined = []
                for r in add_rooms:
                    dev = devices.get(r)
                    if dev:
                        dev.join(master)
                        joined.append(r)
                result["success"] = True
                result["message"] = f"Added {', '.join(joined)} to {source}"

        elif action == "ungroup":
            room = cmd.get("room")
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
            room        = cmd.get("room")
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
            room        = cmd.get("room")
            spotify_uri = cmd.get("uri", "")
            title       = cmd.get("title", spotify_uri)
            dev = devices.get(room)
            if not dev:
                result["message"] = f"Room '{room}' not found. Available: {list(devices.keys())}"
            elif not spotify_uri:
                result["message"] = "No Spotify URI provided"
            else:
                uri_type  = "track" if ":track:" in spotify_uri else "album" if ":album:" in spotify_uri else "playlist"
                uri_id    = spotify_uri.split(":")[-1]
                share_url = f"https://open.spotify.com/{uri_type}/{uri_id}"
                plugin    = ShareLinkPlugin(dev)
                dev.clear_queue()
                plugin.add_share_link_to_queue(share_url)
                dev.play_from_queue(0)
                result["success"] = True
                result["message"] = f"Playing '{title}' (Spotify) in {room}"
                result["data"]    = {"title":title,"uri":spotify_uri,"share_url":share_url}

        elif action == "play_uri":
            room  = cmd.get("room")
            uri   = cmd.get("uri")
            title = cmd.get("title", uri)
            meta  = cmd.get("meta", "")
            dev   = devices.get(room)
            if dev and uri:
                dev.play_uri(uri, meta=meta, title=title)
                result["success"] = True
                result["message"] = f"Playing '{title}' in {room}"
            else:
                result["message"] = f"Room '{room}' not found or no URI"

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
            paused = []
            for r in rooms:
                dev = devices.get(r)
                if dev:
                    try: dev.pause(); paused.append(r)
                    except: pass
            result["success"] = True
            result["message"] = f"Paused: {', '.join(paused)}"

        elif action == "set_volume":
            room   = cmd.get("room")
            volume = int(cmd.get("volume", 20))
            dev    = devices.get(room)
            if dev:
                dev.volume = volume
                result["success"] = True
                result["message"] = f"Volume → {volume} in {room}"
            else:
                result["message"] = f"Room '{room}' not found"

        elif action == "get_services":
            room = cmd.get("room", "")
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

        else:
            result["message"] = f"Unknown action: {action}"

    except Exception as e:
        result["message"] = f"Error: {e}"
        post_error(f"Command error ({action}): {e}", context=f"cmd_id={cmd_id}", module="sonos")

    # Piggyback heartbeat + any buffered history on this command result
    result["heartbeat"] = heartbeat_fields()
    with pending_buffer_lock:
        if pending_buffer:
            result["pending_history"] = list(pending_buffer)
            pending_buffer.clear()
    if result.get("pending_history"):
        try: PENDING_PATH.write_text(json.dumps(result["pending_history"]), encoding="utf-8")
        except: pass
    try:
        r = requests.post(WEBHOOK, json=result, timeout=15)
        log(f"Command result → HTTP {r.status_code}: {result['message']}")
        global last_post_ts
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

# ─── SONOS: GITHUB CMD FALLBACK ─────────────────────────────────────────────
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
    if _already_executed(cmd):
        log(f"GitHub fallback: duplicate (ntfy ran it): {action}")
        return
    _mark_executed(cmd)
    log(f"New command (GitHub fallback): {cmd}")
    execute_command(cmd)

# ─── NTFY LISTENER THREAD ───────────────────────────────────────────────────
def ntfy_listener_thread():
    log(f"ntfy listener: topic={ntfy_topic}")
    while True:
        since = int(time.time())
        url   = f"https://ntfy.sh/{ntfy_topic}/json?since={since}"
        log(f"ntfy connecting: {url}")
        try:
            with requests.get(url, stream=True, timeout=90) as r:
                for line in r.iter_lines():
                    if not line: continue
                    try:    msg = json.loads(line)
                    except: continue
                    if msg.get("event") != "message": continue
                    raw = msg.get("message", "")
                    log(f"⚡ ntfy: {raw[:120]}")
                    try:
                        cmd    = json.loads(raw)
                        cmd_ts = cmd.get("cmd_ts", 0)
                        age    = time.time() - cmd_ts if cmd_ts else 0
                        if cmd_ts and age > 120:
                            log(f"Stale command ({int(age)}s old): {cmd.get('action')}")
                            continue
                        if _already_executed(cmd):
                            log(f"Duplicate: {cmd.get('action')}")
                            continue
                        _mark_executed(cmd)
                        execute_command(cmd)
                    except Exception as e:
                        log(f"ntfy parse/execute error: {e}")
        except Exception as e:
            log(f"ntfy stream error: {e} — reconnecting in 5s")
            time.sleep(5)

# ─── SONOS MAIN LOOP ────────────────────────────────────────────────────────
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
            if first_run:
                names = [d.player_name for d in coordinators]
                log(f"Found {len(names)} coordinator(s): {', '.join(names)}" if names
                    else "No speakers found — retrying...")
                first_run = False

            now = datetime.now(timezone.utc)

            # Build flat device map for commands
            all_devices = {}
            try:
                for dev in soco.discover(timeout=5) or []:
                    all_devices[dev.player_name] = dev
            except: pass
            current_devices_by_name = all_devices

            seen_rooms = set()
            for dev in coordinators:
                info = get_track_info(dev)
                try:    rooms_in_group = [m.player_name for m in dev.group.members]
                except: rooms_in_group = [dev.player_name]

                for room in rooms_in_group:
                    seen_rooms.add(room)
                    prev = room_state.get(room)
                    if info:
                        track_key = f"{info['title']}|{info['artist']}|{info['uri']}"
                        if prev is None or prev.get("track_key") != track_key:
                            if prev and prev.get("track_key") and prev.get("started_at"):
                                post_history(prev["track_info"], room, prev["started_at"], now)
                            room_state[room] = {"track_key": track_key, "track_info": info, "started_at": now}
                            log(f'▶ {room}: "{info["title"]}" – {info["artist"]} [{info["service"]}]')
                    else:
                        if prev and prev.get("track_key") and prev.get("started_at"):
                            post_history(prev["track_info"], room, prev["started_at"], now)
                        room_state[room] = None

            # Rooms that disappeared from network
            for room in list(room_state.keys()):
                if room not in seen_rooms:
                    prev = room_state.get(room)
                    if prev and prev.get("track_key") and prev.get("started_at"):
                        post_history(prev["track_info"], room, prev["started_at"], now)
                    room_state[room] = None

            cmd_counter += 1
            if cmd_counter >= CMD_POLL_EVERY:
                poll_commands()
                cmd_counter = 0

        except Exception as e:
            msg = f"Sonos loop error: {e}"
            log(msg)
            post_error(msg, module="sonos")

        time.sleep(POLL_INTERVAL)

# ─── MAIN ───────────────────────────────────────────────────────────────────
def main():
    log(f"LifeLog Unified Service v{SERVICE_VERSION} starting")

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
    threads_to_start = [
        threading.Thread(target=heartbeat_thread,     daemon=True, name="heartbeat"),
        threading.Thread(target=buffer_monitor_thread, daemon=True, name="buffer-monitor"),
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

    # Sonos runs on main thread (visible activity in console)
    if "sonos" in modules:
        try:
            sonos_main_loop()
        except Exception as e:
            post_error(f"Sonos module fatal crash: {e}",
                       context=traceback.format_exc(), module="sonos")
            log(f"Sonos crashed: {e}")

    # No Sonos — keep alive
    log("Service running (no Sonos). Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log("Stopping.")

if __name__ == "__main__":
    main()
