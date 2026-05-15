#!/usr/bin/env python3
"""
LifeLog Sonos Service v2.1 — update_check ntfy action
- Auto-discovers Sonos speakers on local network
- Polls every 15s for what's playing (track changes)
- POSTs listening history to Tasklet webhook
- Polls GitHub every ~60s for commands (fallback)
- ntfy.sh background thread for INSTANT command delivery
- Command ack posted before execution
- 'search' action returns results without playing (album picker)
- Reports command results AND errors back via webhook
- Suppresses repeat timeout errors for offline speakers

Config: C:\\ProgramData\\LifeLog\\sonos_config.json
  { "house": "caphill" }   OR   { "house": "vashon" }
"""

import sys
import json
import time
import hashlib
import base64
import os
import threading
from datetime import datetime, timezone

# Check Python version
if sys.version_info < (3, 8):
    print("ERROR: Python 3.8+ required")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Installing requests...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

try:
    import soco
except ImportError:
    print("Installing soco...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "soco", "-q"])
    import soco

# --- CONFIGURATION ---
SONOS_VERSION = "2.1"
SONOS_WEBHOOK = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=be22b43febe39260b284d21672db539f"
GITHUB_OWNER = "Shumania"
GITHUB_REPO = "lifelog"
POLL_INTERVAL = 15        # seconds between Sonos polls
CMD_POLL_EVERY = 20       # poll GitHub commands every N Sonos poll cycles (~5 min fallback; ntfy handles instant)
VERSION_CHECK_INTERVAL = 3600  # re-check for updates every 60 min (ntfy handles instant delivery)
OFFLINE_THRESHOLD = 3     # consecutive failures before marking speaker offline
OFFLINE_RECHECK_SECS = 300  # retry offline speakers every 5 minutes
HEARTBEAT_INTERVAL = 300    # send heartbeat every 5 minutes

CONFIG_PATH = r"C:\ProgramData\LifeLog\sonos_config.json"

# ntfy.sh topics per house — agent POSTs here for instant delivery
NTFY_TOPICS = {
    "caphill": "lifelog-cmd-caphill-4x8m",
    "vashon":  "lifelog-cmd-vashon-9k3p",
}

CAPHILL_ROOMS = [
    "Backyard", "Basement Study", "Dining Room", "Kitchen",
    "Living Room", "Master Bathroom", "Media Room", "Study Computer Playbar"
]
VASHON_ROOMS = [
    "Garage Living Room", "Main Living Room Maury", "Main Kitchen Green Move 2",
    "Main Upstairs Bathroom White Roams", "Main Upstairs Master Bedroom",
    "Shed Arc", "Shed Boombox", "Main Lower Deck", "Sauna Porch",
    "Main Upstairs Second Bedroom", "Media Room"
]

# Track consecutive failures and offline status per speaker name
speaker_failures = {}       # name -> consecutive failure count
speaker_offline_since = {}  # name -> epoch time when marked offline

# Shared device map — updated by main loop, read by ntfy thread
current_devices_by_name = {}
current_house = "unknown"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def post_error(house, message, context=""):
    """POST an error event to the Tasklet Sonos webhook so agent sees it automatically."""
    payload = {
        "type": "sonos_error",
        "house": house,
        "message": message,
        "context": context,
        "timestamp": now_iso(),
    }
    try:
        requests.post(SONOS_WEBHOOK, json=payload, timeout=10)
    except Exception:
        pass  # Don't recurse on error reporting failure


def post_heartbeat(house):
    """POST a heartbeat so the agent knows this service is alive."""
    payload = {
        "type": "heartbeat",
        "client_id": f"sonos_{house}",
        "client_type": "sonos_service",
        "house": house,
        "version": SONOS_VERSION,
        "timestamp": now_iso(),
    }
    try:
        r = requests.post(SONOS_WEBHOOK, json=payload, timeout=10)
        print(f"[{now_iso()}] ♥ Heartbeat sent → HTTP {r.status_code}")
    except Exception as e:
        print(f"[{now_iso()}] Heartbeat failed: {e}")


def self_update_check(house="unknown"):
    """Check versions.json on GitHub; re-exec if sonos_version has changed."""
    try:
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/versions.json"
        r = requests.get(url, headers={"Accept": "application/vnd.github.v3+json"}, timeout=10)
        if r.status_code != 200:
            print(f"[{now_iso()}] Version check skipped (HTTP {r.status_code})")
            return
        data = r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        versions = json.loads(content)
        latest = versions.get("sonos_version", SONOS_VERSION)
        if latest == SONOS_VERSION:
            print(f"[{now_iso()}] Version {SONOS_VERSION} is current")
            return
        print(f"[{now_iso()}] New version {latest} available (running {SONOS_VERSION}) — updating...")
        script_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/sonos_service.py"
        r2 = requests.get(script_url, headers={"Accept": "application/vnd.github.v3+json"}, timeout=15)
        if r2.status_code != 200:
            print(f"[{now_iso()}] Failed to download update (HTTP {r2.status_code})")
            return
        new_code = base64.b64decode(r2.json()["content"]).decode("utf-8")
        this_path = os.path.abspath(__file__)
        with open(this_path, "w", encoding="utf-8") as f:
            f.write(new_code)
        print(f"[{now_iso()}] Updated to {latest} — restarting...")
        import subprocess
        subprocess.Popen([sys.executable, this_path] + sys.argv[1:],
                        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        sys.exit(0)
    except Exception as e:
        print(f"[{now_iso()}] Self-update check error: {e}")


def load_config():
    """Load house config from C:\\ProgramData\\LifeLog\\sonos_config.json"""
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
            house = cfg.get("house", "").strip().lower()
            if house not in ("caphill", "vashon"):
                print(f"ERROR: 'house' must be 'caphill' or 'vashon' in {CONFIG_PATH}")
                sys.exit(1)
            return house
    except FileNotFoundError:
        print(f"ERROR: Config not found at {CONFIG_PATH}")
        print("Run Install-SonosService.ps1 first, or create the config manually.")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR reading config: {e}")
        sys.exit(1)


def detect_service(uri, metadata=""):
    """Detect music service from Sonos URI."""
    import re
    s = (uri + metadata).lower()
    if "spotify" in s:
        return "sonos_spotify"
    if "apple" in s or "itunes" in s or "music.apple" in s:
        return "sonos_apple_music"
    if "qobuz" in s:
        return "sonos_qobuz"
    if "tunein" in s or "radiotime" in s or "kexp" in s or "kcrw" in s:
        return "sonos_tunein"
    if "x-rincon-mp3radio" in s or "x-sonosapi-radio" in s or "x-rincon-stream" in s:
        return "sonos_radio"
    SID_MAP = {
        9:   "sonos_spotify",
        31:  "sonos_qobuz",
        52:  "sonos_apple_music",
        204: "sonos_apple_music",
        254: "sonos_tunein",
        2:   "sonos_amazon",
        13:  "sonos_pandora",
        38:  "sonos_siriusxm",
    }
    m = re.search(r'[?&]sid=(\d+)', uri)
    if m:
        sid = int(m.group(1))
        if sid in SID_MAP:
            return SID_MAP[sid]
    if uri and uri != "NOT_IMPLEMENTED":
        return "sonos_unknown"
    return "sonos_unknown"


def get_coordinators():
    """Discover all Sonos group coordinators on the network."""
    try:
        devices = soco.discover(timeout=8)
        if not devices:
            return []
        coordinators = {}
        for device in devices:
            try:
                group = device.group
                if group and device == group.coordinator:
                    coordinators[device.player_name] = device
            except Exception:
                coordinators[device.player_name] = device
        return list(coordinators.values())
    except Exception as e:
        print(f"[{now_iso()}] Discovery error: {e}")
        return []


def get_track_info(device, house):
    """Get currently playing track info from a coordinator device.
    Returns None if stopped. Tracks failures and suppresses repeat timeout errors."""
    name = device.player_name
    now_epoch = time.time()

    # Skip speaker if it's been marked offline (recheck every OFFLINE_RECHECK_SECS)
    if name in speaker_offline_since:
        if now_epoch - speaker_offline_since[name] < OFFLINE_RECHECK_SECS:
            return None
        else:
            # Time to retry
            print(f"[{now_iso()}] Retrying offline speaker: {name}")
            del speaker_offline_since[name]
            speaker_failures[name] = 0

    try:
        transport = device.get_current_transport_info()
        state = transport.get("current_transport_state", "STOPPED")
        if state not in ("PLAYING", "TRANSITIONING"):
            speaker_failures[name] = 0
            return None

        info = device.get_current_track_info()
        title = info.get("title", "").strip()
        if not title or title in ("", "NOT_IMPLEMENTED"):
            speaker_failures[name] = 0
            return None

        artist = info.get("artist", "").strip()
        album = info.get("album", "").strip()
        uri = info.get("uri", "")
        metadata = info.get("metadata", "")

        dur_str = info.get("duration", "0:00:00")
        duration_secs = 0
        try:
            parts = dur_str.split(":")
            if len(parts) == 3:
                duration_secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except Exception:
            pass

        try:
            group_members = [m.player_name for m in device.group.members]
        except Exception:
            group_members = [device.player_name]

        # Successful query — reset failure count
        speaker_failures[name] = 0

        return {
            "title": title,
            "artist": artist,
            "album": album,
            "uri": uri,
            "service": detect_service(uri, metadata),
            "duration_seconds": duration_secs,
            "rooms": group_members,
            "coordinator": device.player_name,
        }

    except Exception as e:
        failures = speaker_failures.get(name, 0) + 1
        speaker_failures[name] = failures

        if failures == OFFLINE_THRESHOLD:
            # Just crossed the threshold — mark offline and report once
            speaker_offline_since[name] = now_epoch
            msg = f"Speaker '{name}' marked offline after {failures} failures: {e}"
            print(f"[{now_iso()}] ⚠ {msg}")
            post_error(house, msg, context=f"speaker={name}")
        elif failures < OFFLINE_THRESHOLD:
            # Still within threshold — log locally only
            print(f"[{now_iso()}] Error getting track from {name} (attempt {failures}): {e}")
        # If > threshold and still offline: silence (already reported)
        return None


def post_history(house, track, room, started_at, ended_at):
    """POST a completed track play to the Tasklet Sonos webhook."""
    duration_played = int((ended_at - started_at).total_seconds())
    if duration_played < 15:
        return  # skip plays under 15 seconds

    uri_or_title = track["uri"] or f"{track['title']}|{track['artist']}"
    fp = hashlib.md5(uri_or_title.encode()).hexdigest()[:12]
    minute_bucket = int(started_at.timestamp() // 60)
    dedup_key = f"sonos_{house}_{room.lower().replace(' ', '_')}_{fp}_{minute_bucket}"

    payload = {
        "type": "sonos_history",
        "house": house,
        "room": room,
        "title": track["title"],
        "artist": track["artist"],
        "album": track["album"],
        "uri": track["uri"],
        "service": track["service"],
        "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_at": ended_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_played_seconds": duration_played,
        "track_duration_seconds": track.get("duration_seconds", 0),
        "dedup_key": dedup_key,
    }

    try:
        r = requests.post(SONOS_WEBHOOK, json=payload, timeout=15)
        print(f"[{now_iso()}] ✓ History: \"{track['title']}\" – {track['artist']} | {room} ({duration_played}s) [{track['service']}] → HTTP {r.status_code}")
    except Exception as e:
        print(f"[{now_iso()}] ✗ Failed to post history: {e}")
        post_error(house, f"Failed to post history: {e}", context=f"title={track['title']}")


def execute_command(house, cmd, devices_by_name):
    """Execute a Sonos command and POST result back. Acks immediately before executing."""
    action = cmd.get("action", "")
    cmd_id = cmd.get("cmd_id", "")

    if action in ("none", "") or cmd_id == "idle":
        print(f"[{now_iso()}] Idle command — skipping")
        return

    # Ack immediately so agent knows command was received
    ack = {
        "type": "sonos_ack",
        "cmd_id": cmd_id,
        "action": action,
        "house": house,
        "timestamp": now_iso(),
    }
    try:
        requests.post(SONOS_WEBHOOK, json=ack, timeout=10)
        print(f"[{now_iso()}] ✓ Ack sent for action: {action}")
    except Exception:
        pass

    result = {
        "type": "sonos_result",
        "cmd_id": cmd_id,
        "action": action,
        "house": house,
        "success": False,
        "message": "",
        "data": None,
    }

    try:
        if action == "get_state":
            coordinators = get_coordinators()
            state = []
            for dev in coordinators:
                info = get_track_info(dev, house)
                try:
                    members = [m.player_name for m in dev.group.members]
                except Exception:
                    members = [dev.player_name]
                state.append({
                    "coordinator": dev.player_name,
                    "members": members,
                    "playing": {
                        "title": info["title"],
                        "artist": info["artist"],
                        "album": info["album"],
                        "service": info["service"],
                    } if info else None,
                })
            result["success"] = True
            result["data"] = state

        elif action == "group":
            source = cmd.get("source")
            add_rooms = cmd.get("add", [])
            if isinstance(add_rooms, str):
                add_rooms = [add_rooms]
            master = devices_by_name.get(source)
            if not master:
                result["message"] = f"Room '{source}' not found on network"
            else:
                joined = []
                for room in add_rooms:
                    dev = devices_by_name.get(room)
                    if dev:
                        dev.join(master)
                        joined.append(room)
                result["success"] = True
                result["message"] = f"Added {', '.join(joined)} to {source}"

        elif action == "ungroup":
            room = cmd.get("room")
            dev = devices_by_name.get(room)
            if dev:
                dev.unjoin()
                result["success"] = True
                result["message"] = f"Removed {room} from its group"
            else:
                result["message"] = f"Room '{room}' not found"

        elif action == "search":
            # Return top results without playing — for album picker UX
            service_name = cmd.get("service", "Qobuz")
            query = cmd.get("query", "")
            search_type = cmd.get("search_type", "albums")
            n = int(cmd.get("n", 5))
            if not query:
                result["message"] = "No query provided"
            else:
                try:
                    from soco.music_services import MusicService
                    ms = MusicService(service_name)
                    results_list = ms.search(search_type, query, 0, n)
                    items = list(results_list)
                    if not items:
                        result["message"] = f"No {search_type} found for '{query}' on {service_name}"
                    else:
                        hits = []
                        for item in items:
                            hits.append({
                                "title": getattr(item, "title", str(item)),
                                "artist": getattr(item, "creator", ""),
                                "uri": getattr(item, "uri", None),
                            })
                        result["success"] = True
                        result["message"] = f"Found {len(hits)} {search_type} for '{query}' on {service_name}"
                        result["data"] = {"query": query, "service": service_name, "results": hits}
                except Exception as e:
                    result["message"] = f"Search error: {e}"

        elif action == "search_and_play":
            room = cmd.get("room")
            service_name = cmd.get("service", "Qobuz")
            query = cmd.get("query", "")
            search_type = cmd.get("search_type", "albums")
            dev = devices_by_name.get(room)
            if not dev:
                result["message"] = f"Room '{room}' not found"
            elif not query:
                result["message"] = "No query provided"
            else:
                try:
                    from soco.music_services import MusicService
                    ms = MusicService(service_name)
                    results_list = ms.search(search_type, query, 0, 5)
                    items = list(results_list)
                    if not items:
                        result["message"] = f"No {search_type} found for '{query}' on {service_name}"
                    else:
                        first = items[0]
                        title = getattr(first, "title", str(first))
                        uri = getattr(first, "uri", None)
                        meta = getattr(first, "to_didl_string", lambda: "")()
                        if uri:
                            dev.play_uri(uri, meta=meta, title=title)
                            result["success"] = True
                            result["message"] = f"Playing '{title}' ({service_name}) in {room}"
                            result["data"] = {"title": title, "uri": uri, "service": service_name}
                        else:
                            result["message"] = f"Found '{title}' but could not get URI"
                except Exception as e:
                    result["message"] = f"Search error: {e}"

        elif action == "play_spotify_uri":
            # Agent searches Spotify API, passes URI here; service uses ShareLinkPlugin
            # uri: e.g. "spotify:album:1weenld61qoidwYuZ1GESA" or "spotify:track:..."
            room = cmd.get("room")
            spotify_uri = cmd.get("uri", "")
            title = cmd.get("title", spotify_uri)
            dev = devices_by_name.get(room)
            if not dev:
                result["message"] = f"Room '{room}' not found. Available: {list(devices_by_name.keys())}"
            elif not spotify_uri:
                result["message"] = "No Spotify URI provided"
            else:
                try:
                    from soco.plugins.sharelink import ShareLinkPlugin
                    # Convert spotify URI to share URL
                    uri_type = "track" if ":track:" in spotify_uri else "album" if ":album:" in spotify_uri else "playlist"
                    uri_id = spotify_uri.split(":")[-1]
                    share_url = f"https://open.spotify.com/{uri_type}/{uri_id}"
                    plugin = ShareLinkPlugin(dev)
                    dev.clear_queue()
                    plugin.add_share_link_to_queue(share_url)
                    dev.play_from_queue(0)
                    result["success"] = True
                    result["message"] = f"Playing '{title}' (Spotify) in {room}"
                    result["data"] = {"title": title, "uri": spotify_uri, "share_url": share_url}
                except Exception as e:
                    result["message"] = f"play_spotify_uri error: {e}"

        elif action == "get_services":
            # Discover what music services are configured on a speaker (needed to get correct sn)
            room = cmd.get("room", "")
            dev = devices_by_name.get(room) if room else (next(iter(devices_by_name.values()), None))
            out = {"speaker": room, "services": [], "soco_accounts": []}
            try:
                # Try soco music_services module to enumerate configured accounts
                from soco import music_services as ms_mod
                try:
                    all_ms = ms_mod.get_all_music_services()
                    for svc in all_ms:
                        out["soco_accounts"].append({
                            "name": svc.get("Name", ""),
                            "service_id": svc.get("Id", ""),
                        })
                except Exception as e:
                    out["soco_accounts_error"] = str(e)
                # Try to get services from the speaker device directly
                if dev:
                    try:
                        avail = dev.music_services.get_available_services()
                        for svc in avail:
                            entry = {
                                "name": getattr(svc, "service_name", str(svc)),
                                "service_id": getattr(svc, "service_id", None),
                            }
                            # Try to get account/sn info
                            try:
                                acct = svc.account
                                entry["account_sn"] = acct.serial_number if acct else None
                                entry["account_username"] = acct.username if acct else None
                            except Exception:
                                pass
                            out["services"].append(entry)
                    except Exception as e:
                        out["services_error"] = str(e)
                    # Also dump current track info URI so we can see the format in use
                    try:
                        info = dev.get_current_track_info()
                        out["current_track_uri"] = info.get("uri", "")
                        out["current_track_title"] = info.get("title", "")
                    except Exception as e:
                        out["current_track_error"] = str(e)
                result["success"] = True
                result["message"] = f"Services on '{room}': {len(out['services'])} found"
                result["data"] = out
            except Exception as e:
                result["message"] = f"get_services error: {e}"

        elif action == "play_spotify_tracks":
            # Deprecated — use play_spotify_uri instead (agent searches Spotify API first)
            result["message"] = "play_spotify_tracks deprecated. Use play_spotify_uri with a spotify:album: or spotify:track: URI."

        elif action == "play_uri":
            room = cmd.get("room")
            uri = cmd.get("uri")
            title = cmd.get("title", uri)
            meta = cmd.get("meta", "")
            dev = devices_by_name.get(room)
            if dev and uri:
                dev.play_uri(uri, meta=meta, title=title)
                result["success"] = True
                result["message"] = f"Playing '{title}' in {room}"
            else:
                result["message"] = f"Room '{room}' not found or no URI provided"

        elif action == "stop":
            rooms = cmd.get("rooms", list(devices_by_name.keys()))
            if isinstance(rooms, str):
                rooms = [rooms]
            stopped = []
            for room in rooms:
                dev = devices_by_name.get(room)
                if dev:
                    try:
                        dev.stop()
                        stopped.append(room)
                    except Exception:
                        pass
            result["success"] = True
            result["message"] = f"Stopped: {', '.join(stopped)}"

        elif action == "pause":
            rooms = cmd.get("rooms", [])
            if isinstance(rooms, str):
                rooms = [rooms]
            paused = []
            for room in rooms:
                dev = devices_by_name.get(room)
                if dev:
                    try:
                        dev.pause()
                        paused.append(room)
                    except Exception:
                        pass
            result["success"] = True
            result["message"] = f"Paused: {', '.join(paused)}"

        elif action == "set_volume":
            room = cmd.get("room")
            volume = int(cmd.get("volume", 20))
            dev = devices_by_name.get(room)
            if dev:
                dev.volume = volume
                result["success"] = True
                result["message"] = f"Volume → {volume} in {room}"
            else:
                result["message"] = f"Room '{room}' not found"

        elif action == "update_check":
            # Agent sends this via ntfy to trigger immediate self-update check
            print(f"[{now_iso()}] update_check command received — checking for new version immediately")
            result["success"] = True
            result["message"] = f"Running update check now (currently v{SONOS_VERSION})"
            # Run self-update in a thread so we can post the result first
            def do_update():
                time.sleep(2)  # let result post first
                self_update_check(house)
            threading.Thread(target=do_update, daemon=True).start()

        else:
            result["message"] = f"Unknown action: {action}"


    except Exception as e:
        result["message"] = f"Error: {e}"
        post_error(house, f"Command execution error ({action}): {e}", context=f"cmd_id={cmd_id}")

    try:
        r = requests.post(SONOS_WEBHOOK, json=result, timeout=15)
        print(f"[{now_iso()}] Command result posted → HTTP {r.status_code}: {result['message']}")
    except Exception as e:
        print(f"[{now_iso()}] Failed to post command result: {e}")


def ntfy_listener(house):
    """Background thread: subscribes to ntfy.sh topic for instant command delivery.
    Streams events; reconnects on error. Falls back to GitHub polling every ~60s."""
    topic = NTFY_TOPICS.get(house)
    if not topic:
        print(f"[{now_iso()}] No ntfy topic for house: {house} — skipping ntfy listener")
        return

    print(f"[{now_iso()}] ntfy listener starting for house: {house}, topic: {topic}")

    while True:
        # Use since=<now> so we never replay the backlog on reconnect
        since = int(time.time())
        url = f"https://ntfy.sh/{topic}/json?since={since}"
        print(f"[{now_iso()}] ntfy listener connecting: {url}")
        try:
            # Stream events — reconnects when connection drops
            with requests.get(url, stream=True, timeout=90) as r:
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    event = msg.get("event", "")
                    if event != "message":
                        continue
                    raw = msg.get("message", "")
                    print(f"[{now_iso()}] ⚡ ntfy command received: {raw[:120]}")
                    try:
                        cmd = json.loads(raw)
                        # Drop stale commands older than 2 minutes
                        cmd_ts = cmd.get("cmd_ts", 0)
                        age = time.time() - cmd_ts if cmd_ts else 0
                        if cmd_ts and age > 120:
                            print(f"[{now_iso()}] Ignoring stale command (age: {int(age)}s): {cmd.get('action')}")
                            continue
                        # Cross-channel dedup — skip if GitHub fallback already ran this
                        if _already_executed(cmd):
                            print(f"[{now_iso()}] Skipping duplicate command (already executed): {cmd.get('action')}")
                            continue
                        _mark_executed(cmd)
                        execute_command(house, cmd, current_devices_by_name)
                    except Exception as e:
                        print(f"[{now_iso()}] ntfy parse/execute error: {e}")
        except Exception as e:
            print(f"[{now_iso()}] ntfy stream error: {e} — reconnecting in 5s")
            time.sleep(5)


# --- MAIN ---
last_cmd_sha = None
last_version_check = 0
last_heartbeat = 0
room_state = {}

# Cross-channel command dedup: stores hashes of recently executed commands
# so ntfy and GitHub fallback never both execute the same command
executed_cmd_hashes = set()
MAX_EXECUTED_HASHES = 30

def _cmd_hash(cmd):
    """Stable hash of command content (excluding cmd_ts which varies)."""
    stable = {k: v for k, v in cmd.items() if k != "cmd_ts"}
    return hashlib.md5(json.dumps(stable, sort_keys=True).encode()).hexdigest()

def _mark_executed(cmd):
    """Record that a command was executed. Prune if set gets large."""
    executed_cmd_hashes.add(_cmd_hash(cmd))
    if len(executed_cmd_hashes) > MAX_EXECUTED_HASHES:
        # Remove oldest half (sets don't preserve order, just truncate)
        excess = list(executed_cmd_hashes)[:MAX_EXECUTED_HASHES // 2]
        for h in excess:
            executed_cmd_hashes.discard(h)

def _already_executed(cmd):
    return _cmd_hash(cmd) in executed_cmd_hashes


def poll_commands(house, devices_by_name):
    """Poll GitHub command file for this house and execute any new command (fallback)."""
    global last_cmd_sha

    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/sonos_cmd_{house}.json"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return
        data = r.json()
        sha = data.get("sha")
        if sha == last_cmd_sha:
            return

        content_b64 = data.get("content", "")
        cmd = json.loads(base64.b64decode(content_b64).decode())

        # SHA-only dedup — no cmd_id required
        last_cmd_sha = sha
        action = cmd.get("action", "")
        if action in ("none", "", "idle"):
            return

        # Cross-channel dedup — skip if ntfy already executed this command
        if _already_executed(cmd):
            print(f"[{now_iso()}] GitHub fallback: skipping duplicate command (ntfy already ran): {action}")
            return
        _mark_executed(cmd)

        print(f"[{now_iso()}] New command (GitHub fallback): {cmd}")
        execute_command(house, cmd, devices_by_name)

    except Exception as e:
        print(f"[{now_iso()}] Command poll error: {e}")


def main():
    global current_devices_by_name, current_house, last_version_check, last_heartbeat

    house = load_config()
    current_house = house

    print(f"[{now_iso()}] LifeLog Sonos Service v{SONOS_VERSION} — house: {house}")
    print(f"[{now_iso()}] Polling every {POLL_INTERVAL}s | GitHub fallback every ~{POLL_INTERVAL * CMD_POLL_EVERY}s")
    print(f"[{now_iso()}] ntfy topic: {NTFY_TOPICS.get(house, 'N/A')}")
    print(f"[{now_iso()}] Webhook: {SONOS_WEBHOOK[:60]}...")
    print(f"[{now_iso()}] Checking for updates...")
    self_update_check(house)

    # Send startup heartbeat
    post_heartbeat(house)
    last_heartbeat = time.time()

    # Start ntfy listener in background thread
    t = threading.Thread(target=ntfy_listener, args=(house,), daemon=True)
    t.start()

    print(f"[{now_iso()}] Starting Sonos discovery (timeout=8s)...")

    cmd_counter = 0
    first_run = True

    while True:
        try:
            if first_run:
                print(f"[{now_iso()}] Scanning network for Sonos speakers...")
            coordinators = get_coordinators()
            if first_run:
                names = [d.player_name for d in coordinators]
                if names:
                    print(f"[{now_iso()}] Found {len(names)} coordinator(s): {', '.join(names)}")
                else:
                    print(f"[{now_iso()}] No Sonos speakers found — will retry every {POLL_INTERVAL}s")
                print(f"[{now_iso()}] Polling started. Listening for track changes...")
                first_run = False
            now = datetime.now(timezone.utc)

            # Build flat device map for command execution; share with ntfy thread
            all_devices = {}
            try:
                for dev in soco.discover(timeout=5) or []:
                    all_devices[dev.player_name] = dev
            except Exception:
                pass
            current_devices_by_name = all_devices

            seen_rooms = set()

            for dev in coordinators:
                info = get_track_info(dev, house)
                try:
                    rooms_in_group = [m.player_name for m in dev.group.members]
                except Exception:
                    rooms_in_group = [dev.player_name]

                for room in rooms_in_group:
                    seen_rooms.add(room)
                    prev = room_state.get(room)

                    if info:
                        track_key = f"{info['title']}|{info['artist']}|{info['uri']}"
                        if prev is None or prev.get("track_key") != track_key:
                            if prev and prev.get("track_key") and prev.get("started_at"):
                                post_history(house, prev["track_info"], room, prev["started_at"], now)
                            room_state[room] = {
                                "track_key": track_key,
                                "track_info": info,
                                "started_at": now,
                            }
                            print(f"[{now_iso()}] ▶ {room}: \"{info['title']}\" – {info['artist']} [{info['service']}]")
                    else:
                        if prev and prev.get("track_key") and prev.get("started_at"):
                            post_history(house, prev["track_info"], room, prev["started_at"], now)
                        room_state[room] = None

            # Handle rooms that disappeared
            for room in list(room_state.keys()):
                if room not in seen_rooms:
                    prev = room_state.get(room)
                    if prev and prev.get("track_key") and prev.get("started_at"):
                        post_history(house, prev["track_info"], room, prev["started_at"], now)
                    room_state[room] = None

            # GitHub command fallback polling
            cmd_counter += 1
            if cmd_counter >= CMD_POLL_EVERY:
                poll_commands(house, all_devices)
                cmd_counter = 0

            # Periodic self-update check
            if time.time() - last_version_check >= VERSION_CHECK_INTERVAL:
                last_version_check = time.time()
                self_update_check(house)

            # Periodic heartbeat
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                last_heartbeat = time.time()
                post_heartbeat(house)

        except Exception as e:
            msg = f"Main loop error: {e}"
            print(f"[{now_iso()}] {msg}")
            post_error(house, msg)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
