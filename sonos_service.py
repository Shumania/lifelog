#!/usr/bin/env python3
"""
LifeLog Sonos Service v1.0
- Auto-discovers Sonos speakers on local network
- Polls every 15s for what's playing (track changes)
- POSTs listening history to Tasklet webhook
- Polls GitHub every ~60s for commands (group, play, stop, etc.)
- Reports command results back via webhook

Config: C:\\ProgramData\\LifeLog\\sonos_config.json
  { "house": "caphill" }   OR   { "house": "vashon" }
"""

import sys
import json
import time
import hashlib
import base64
import os
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
SONOS_VERSION = "1.2"
SONOS_WEBHOOK = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=be22b43febe39260b284d21672db539f"
GITHUB_OWNER = "Shumania"
GITHUB_REPO = "lifelog"
POLL_INTERVAL = 15        # seconds between Sonos polls
CMD_POLL_EVERY = 4        # poll commands every N Sonos poll cycles (~60s)
VERSION_CHECK_INTERVAL = 3600  # re-check for updates every 1 hour

CONFIG_PATH = r"C:\ProgramData\LifeLog\sonos_config.json"

# Speaker name → home for display
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


def self_update_check():
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
        os.execv(sys.executable, [sys.executable] + sys.argv)
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


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def detect_service(uri, metadata=""):
    """Detect music service from Sonos URI"""
    s = (uri + metadata).lower()
    if "spotify" in s:
        return "sonos_spotify"
    elif "apple" in s or "itunes" in s or "music.apple" in s:
        return "sonos_apple_music"
    elif "qobuz" in s:
        return "sonos_qobuz"
    elif "tunein" in s or "radiotime" in s or "kexp" in s or "kcrw" in s:
        return "sonos_tunein"
    elif "x-rincon-mp3radio" in s or "x-sonosapi-radio" in s or "x-rincon-stream" in s:
        return "sonos_radio"
    elif uri and uri != "NOT_IMPLEMENTED":
        return "sonos_unknown"
    else:
        return "sonos_unknown"


def get_coordinators():
    """Discover all Sonos group coordinators on the network"""
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


def get_track_info(device):
    """Get currently playing track info from a coordinator device. Returns None if stopped."""
    try:
        transport = device.get_current_transport_info()
        state = transport.get("current_transport_state", "STOPPED")
        if state not in ("PLAYING", "TRANSITIONING"):
            return None

        info = device.get_current_track_info()
        title = info.get("title", "").strip()
        if not title or title in ("", "NOT_IMPLEMENTED"):
            return None

        artist = info.get("artist", "").strip()
        album = info.get("album", "").strip()
        uri = info.get("uri", "")
        metadata = info.get("metadata", "")

        # Parse duration string "H:MM:SS" → seconds
        dur_str = info.get("duration", "0:00:00")
        duration_secs = 0
        try:
            parts = dur_str.split(":")
            if len(parts) == 3:
                duration_secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except Exception:
            pass

        # Get all rooms in this group
        try:
            group_members = [m.player_name for m in device.group.members]
        except Exception:
            group_members = [device.player_name]

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
        print(f"[{now_iso()}] Error getting track from {device.player_name}: {e}")
        return None


def post_history(house, track, room, started_at, ended_at):
    """POST a completed track play to the Tasklet Sonos webhook"""
    duration_played = int((ended_at - started_at).total_seconds())
    if duration_played < 15:
        return  # skip plays under 15 seconds

    # Dedup key: house + room + uri fingerprint + start-minute bucket
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


def poll_commands(house, devices_by_name):
    """Poll GitHub command file for this house and execute any new command"""
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
        cmd_id = cmd.get("cmd_id", "")

        if not cmd_id or cmd_id == last_cmd_sha:
            last_cmd_sha = sha
            return

        last_cmd_sha = sha
        print(f"[{now_iso()}] New command: {cmd}")
        execute_command(house, cmd, devices_by_name)

    except Exception as e:
        print(f"[{now_iso()}] Command poll error: {e}")


def execute_command(house, cmd, devices_by_name):
    """Execute a Sonos command and POST result back"""
    action = cmd.get("action", "")
    result = {
        "type": "sonos_result",
        "cmd_id": cmd.get("cmd_id"),
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
                info = get_track_info(dev)
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
            # Join rooms to a playing source room
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

        elif action == "play_uri":
            room = cmd.get("room")
            uri = cmd.get("uri")
            title = cmd.get("title", uri)
            dev = devices_by_name.get(room)
            if dev and uri:
                dev.play_uri(uri, title=title)
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

        else:
            result["message"] = f"Unknown action: {action}"

    except Exception as e:
        result["message"] = f"Error: {e}"

    try:
        r = requests.post(SONOS_WEBHOOK, json=result, timeout=15)
        print(f"[{now_iso()}] Command result posted → HTTP {r.status_code}: {result['message']}")
    except Exception as e:
        print(f"[{now_iso()}] Failed to post command result: {e}")


# --- MAIN ---
last_cmd_sha = None
last_version_check = 0  # epoch seconds — 0 forces check on first run
room_state = {}  # room_name → {track_key, track_info, started_at} or None


def main():
    house = load_config()
    print(f"[{now_iso()}] LifeLog Sonos Service v{SONOS_VERSION} — house: {house}")
    print(f"[{now_iso()}] Polling every {POLL_INTERVAL}s | Commands every ~{POLL_INTERVAL * CMD_POLL_EVERY}s")
    print(f"[{now_iso()}] Webhook: {SONOS_WEBHOOK[:60]}...")
    print(f"[{now_iso()}] Checking for updates...")
    self_update_check()
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

            # Build flat device map for command execution
            all_devices = {}
            try:
                for dev in soco.discover(timeout=5) or []:
                    all_devices[dev.player_name] = dev
            except Exception:
                pass

            # Track which rooms we saw this cycle
            seen_rooms = set()

            for dev in coordinators:
                info = get_track_info(dev)
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
                            # New track started — log the previous one
                            if prev and prev.get("track_key") and prev.get("started_at"):
                                post_history(house, prev["track_info"], room, prev["started_at"], now)
                            room_state[room] = {
                                "track_key": track_key,
                                "track_info": info,
                                "started_at": now,
                            }
                            print(f"[{now_iso()}] ▶ {room}: \"{info['title']}\" – {info['artist']} [{info['service']}]")
                    else:
                        # Nothing playing — log any previously playing track
                        if prev and prev.get("track_key") and prev.get("started_at"):
                            post_history(house, prev["track_info"], room, prev["started_at"], now)
                        room_state[room] = None

            # Handle rooms that disappeared (device offline or ungrouped)
            for room in list(room_state.keys()):
                if room not in seen_rooms:
                    prev = room_state.get(room)
                    if prev and prev.get("track_key") and prev.get("started_at"):
                        post_history(house, prev["track_info"], room, prev["started_at"], now)
                    room_state[room] = None

            # Poll GitHub commands
            cmd_counter += 1
            if cmd_counter >= CMD_POLL_EVERY:
                poll_commands(house, all_devices)
                cmd_counter = 0

            # Hourly self-update check
            global last_version_check
            if time.time() - last_version_check >= VERSION_CHECK_INTERVAL:
                last_version_check = time.time()
                self_update_check()  # re-execs process if new version found

        except Exception as e:
            print(f"[{now_iso()}] Main loop error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
