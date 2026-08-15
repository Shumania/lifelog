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
import re  # v2.44: module-level -- get_container_context() used re without import (silent NameError)
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
# SERVICE_VERSION is read from the VERSION file in the same directory as this script.
# The VERSION file is the SINGLE SOURCE OF TRUTH for the service version number.
# The same file on GitHub is fetched during update checks — no versions.json needed.
# On update, both lifelog_service.py AND VERSION are downloaded together.
_FALLBACK_VERSION = "2.62.1"  # Only used if VERSION file is missing (bootstrap)

def _read_version():
    """Read version from VERSION file next to this script."""
    for base in (Path(sys.argv[0]).resolve().parent, Path(__file__).resolve().parent):
        vf = base / "VERSION"
        try:
            if vf.exists():
                ver = vf.read_text(encoding="utf-8").strip()
                if ver:
                    return ver
        except Exception as exc:
            print(f"[VERSION] Warning: could not read {vf}: {exc}")
    # No VERSION file found — first install or file deleted
    print(f"[VERSION] WARNING: VERSION file not found! Using fallback {_FALLBACK_VERSION}")
    print(f"[VERSION] Searched: {Path(sys.argv[0]).resolve().parent} and {Path(__file__).resolve().parent}")
    return _FALLBACK_VERSION
SERVICE_VERSION = "2.34"  # Legacy anchor — v2.33 updater regex parses this line. Remove after all machines are on v2.34+.
SERVICE_VERSION = _read_version()  # Real version from VERSION file (overwrites anchor above)
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

# ntfy auth token (Pro plan — higher rate limits, reserved topics)
NTFY_TOKEN = "tk_lo3wjmt4yxkznzt4m3wxhfhspb93t"

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
# CMD_POLL_EVERY removed in v2.24 — GitHub command polling retired (ntfy is sole transport)
HEARTBEAT_FALLBACK_SECS    = 14400  # v2.49: keepalive every 4h when idle (was 2h; only if no real POST). Liveness signal only — delta payloads make it near-empty anyway.
HEARTBEAT_QUIET_SLEEP      = 1800  # 30 min retry during quiet hours
ACTIVITY_WINDOW            = 7200  # "active" if Sonos track in last 2h
VERSION_CHECK_INTERVAL     = 3600  # 60 min
BACKUP_INTERVAL            = 3600  # run extract every 60 min
DEV_POLL_INTERVAL          = 100   # dev_next.ps1 poll (s)
OFFLINE_THRESHOLD          = 3
OFFLINE_RECHECK_SECS       = 300
BATCH_SIZE                 = 20    # flush buffer when this many tracks queued
BATCH_TRAILING_SECS        = 1800  # flush 30 min after last track was added
BUFFER_MAX_AGE_SECS        = 1800  # v2.48.5: flush when OLDEST buffered track is 30 min old (checked in heartbeat_thread). Guarantees plays reach the server within ~30 min even during long continuous sessions (fixes Roaming-label race with the Spotify backstop poll)
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

# --- URI METADATA CACHE (v2.37) ----------------------------------------------
# When play_next enqueues a non-Spotify track with known title/artist/album,
# cache it keyed by URI. get_track_info() checks this cache before falling back
# to synthetic titles like "Qobuz Track". Qobuz and Apple Music DIDL metadata
# from Sonos is often empty even though we sent correct metadata when enqueuing.
# Cache entries expire after 4 hours to avoid stale data buildup.
_uri_metadata_cache      = {}   # uri -> {"title": str, "artist": str, "album": str, "ts": float}

# --- POLL SNAPSHOT (v2.24) ---------------------------------------------------
# Single-pass snapshot computed once per poll cycle. All downstream consumers
# (SSE enrichment, state push, heartbeat, diagnostics) read from this instead
# of independently querying every speaker.
_last_topology_sig = None  # v2.53: JSON signature of group topology for change detection
_paused_at_by_room = {}    # v2.54 rider: room -> ISO ts when it entered PAUSED (cleared on leave)
_poll_snapshot = {
    "rooms_playing": [],
    "rooms_paused": [],
    "paused_at": {},          # v2.54 rider: real pause timestamps for cold-load UI

    "rooms_all": [],
    "groups": [],             # v2.53: [{"coordinator","members","state"}] for ALL groups
    "play_modes": {},
    "mute_states": {},
    "transport_states": {},   # room -> state string
    "timestamp": None,
}
_offline_ips             = {}   # ip -> epoch timestamp; skip timed-out speakers
# last_cmd_sha removed in v2.24 (GitHub command polling retired)
executed_cmd_hashes      = {}   # hash -> timestamp (TTL-based dedup)
# v2.44: TTL raised 60s -> 360s. ntfy replays since=5m on reconnect; a 60s TTL
# let already-executed commands re-execute after restarts/reconnects.
CMD_DEDUP_TTL_SECONDS    = 360  # must exceed ntfy's since=5m replay window
last_sonos_activity_ts   = 0.0  # updated when a track is buffered
last_track_added_ts      = 0.0  # updated when a track is added to buffer
last_post_ts             = 0.0  # updated whenever any POST succeeds
pending_buffer           = []   # tracks waiting to be flushed
pending_buffer_lock      = threading.Lock()
PENDING_PATH             = INSTALL_DIR / "pending_history.json"
STATE_RING_PATH          = INSTALL_DIR / "state_ring_buffer.json"
# v2.44: persist executed command hashes across restarts so ntfy's since=5m
# replay after a restart can't re-execute commands the old process already ran.
EXECUTED_CMDS_PATH       = INSTALL_DIR / "executed_cmds.json"
try:
    if EXECUTED_CMDS_PATH.exists():
        _loaded_hashes = json.loads(EXECUTED_CMDS_PATH.read_text(encoding="utf-8"))
        _cutoff = time.time() - CMD_DEDUP_TTL_SECONDS
        executed_cmd_hashes.update({h: ts for h, ts in _loaded_hashes.items() if ts > _cutoff})
        print(f"[dedup] loaded {len(executed_cmd_hashes)} unexpired command hash(es) from disk")
except Exception as _dh_err:
    print(f"[dedup] WARNING: failed to load executed_cmds.json: {_dh_err}")

# --- QUEUE PROVENANCE (v2.55) ------------------------------------------------
# DESIGN NOTE: Sonos reports container = x-rincon-queue for queue playback, and
# Spotify's recently-played API reports context=null for Sonos-driven plays, so
# the playlist/album identity of a queue load is LOST unless WE remember it
# (confirmed 2026-07-30: 58/58 Sonos plays with NULL context; "2020s Mix" and
# Wesleyan playlist sessions showed "led by <artist>" instead of the name).
# When a command loads the queue from a container URI (play_spotify_uri
# playlist/album, play_album play-now), we record {coordinator -> context} here
# and post_history stamps it onto history items whose own container is missing
# or a queue URI. Cleared by clear_queue and replaced by the next queue load.
# LIMITATION (accepted): queues rebuilt via the native Sonos/Spotify apps are
# undetectable — stale provenance could mislabel those plays. Mitigations: the
# 24h TTL below (Rule 11: every guard/memory needs an expiry) + native app
# loads usually DO carry a real EnqueuedTransportURI, which wins the overlay.
queue_provenance         = {}   # coordinator name -> {"uri","name","type","loaded_at"}
QUEUE_PROV_PATH          = INSTALL_DIR / "queue_provenance.json"
QUEUE_PROV_TTL_SECONDS   = 24 * 3600

def _save_queue_provenance():
    try:
        QUEUE_PROV_PATH.write_text(json.dumps(queue_provenance), encoding="utf-8")
    except Exception as _qp_err:
        log(f"[queue-prov] WARNING: persist failed: {_qp_err}")

def _set_queue_provenance(coord_name, uri, name, ctype):
    """Remember that coord_name's queue was loaded from container uri/name."""
    queue_provenance[coord_name] = {"uri": uri or "", "name": name or "",
                                    "type": ctype or "", "loaded_at": time.time()}
    _save_queue_provenance()
    log(f"[queue-prov] {coord_name}: queue loaded from {ctype} '{name}' ({uri})")
    # v2.59 C3: a queue load/replace invalidates any stale-Enqueued marker for
    # this coordinator. (Insert-play paths that WANT a marker set it AFTER this
    # call — order matters there.) Defined below; resolved at call time.
    _clear_stale_enqueued(coord_name, "queue provenance replaced")

def _clear_queue_provenance(coord_name, reason=""):
    if queue_provenance.pop(coord_name, None) is not None:
        _save_queue_provenance()
        log(f"[queue-prov] {coord_name}: provenance cleared ({reason})")
    # v2.59 C3: queue clears/replaces also invalidate the stale-Enqueued marker
    # (runs even when there was no provenance to pop — the marker is independent).
    _clear_stale_enqueued(coord_name, f"queue cleared/replaced ({reason})")

def _get_queue_provenance(coord_name):
    """Return live provenance for a coordinator, or None (expired entries pruned)."""
    p = queue_provenance.get(coord_name)
    if not p: return None
    if time.time() - p.get("loaded_at", 0) > QUEUE_PROV_TTL_SECONDS:
        _clear_queue_provenance(coord_name, "TTL expired")
        return None
    return p

def _overlay_prov_guard(prov, track_album, coord_name):
    """v2.59.2 OVERLAY GUARD (Rule 27): the v2.55 provenance overlay previously
    stamped the remembered pointer with NO validation against the observed
    track -- a stale album-typed pointer poisoned every subsequent queue play
    (2026-08-06 Shed Arc incident: Beastie Boys pointer stamped on Zappa and
    Shuggie Otis organic plays; sanitize_container guards only the CAPTURED
    DIDL container, never the overlay).
    Album-typed provenance is verifiable at stamp time: if the pointer's album
    name mismatches the track's own DIDL album, the pointer is provably stale
    -> skip the overlay AND clear the pointer (level-triggered self-heal on
    the next poll; no manual scrub needed). Returns True when the overlay must
    be skipped. Fails open (False) when either name is missing or on internal
    error. Playlist/station-typed pointers are not verifiable this way and
    pass through unchanged (setter-side fix v2.59.1 covers those)."""
    try:
        if ((prov.get("type") or "").lower() == "album" and track_album
                and prov.get("name")
                # v2.61: fuzzy match (was strict equality) — decorated sender
                # titles ('🎛️ Album (2005) — Artist') are NOT stale (Rule 10).
                and not _ctx_names_match(prov["name"], track_album)):
            log(f"[overlay-guard] {coord_name}: STALE album provenance "
                f"'{prov.get('name','')}' vs track album '{track_album}' -- "
                f"overlay SKIPPED, pointer CLEARED (self-heal)")
            _clear_queue_provenance(coord_name,
                                    f"overlay-guard album mismatch (track_album='{track_album}')")
            return True
    except Exception as _og_err:
        log(f"[overlay-guard] ERROR (fail-open, overlay allowed): {type(_og_err).__name__}: {_og_err}")
    return False

try:
    if QUEUE_PROV_PATH.exists():
        _qp_loaded = json.loads(QUEUE_PROV_PATH.read_text(encoding="utf-8"))
        _qp_cut = time.time() - QUEUE_PROV_TTL_SECONDS
        queue_provenance.update({k: v for k, v in _qp_loaded.items()
                                 if v.get("loaded_at", 0) > _qp_cut})
    # v2.55 boot marker (Rule 24: verify a log line UNIQUE to the new version)
    print(f"[queue-prov] v2.55 queue provenance active: {len(queue_provenance)} entry(ies) loaded")
except Exception as _qp_err:
    print(f"[queue-prov] WARNING: failed to load queue_provenance.json: {_qp_err}")

# --- v2.59 STALE-ENQUEUED MARKER (C3 L1) ---------------------------------------
# DESIGN (review_impl_v1.md §C3): AVTransport's EnqueuedTransportURI is only
# rewritten by SetAVTransportURI (queue replaces via native app, direct loads,
# radio tunes). Our own queue INSERTS (play_album play-now, play_next) and
# play_from_queue do NOT touch it — so after an insert-play, Sonos keeps
# reporting the PREVIOUS load's container while our content plays (Bug 1).
# At insert-play command time we KNOW the Enqueued value just went stale, and
# we know the exact stale URI. Remember it here so capture (sanitize_container)
# can suppress the stale container. Two marker kinds:
#   "insert_album"   — play-now album insert. Suppress only while the OBSERVED
#                      track album matches expected_album; when the album ends
#                      and the old queue resumes, the old container is again
#                      CORRECT for those tracks and L1 must release (T-C3.4).
#   "inserted_track" — single-track play_next injection (Q6, signed off):
#                      suppress with NO overlay fallback — honest "no context"
#                      instead of stamping the surrounding playlist on a one-off.
# Cleared when: a genuinely NEW Enqueued URI is observed at capture (a real
# load happened — ours or native), any queue replace/clear that goes through
# _set/_clear_queue_provenance (all of ours do), or 24h TTL (Rule 11).
# Persisted beside queue_provenance.json so a service restart never reverts
# capture to the old (stale-stamping) behavior mid-listen.
stale_enqueued          = {}   # coordinator name -> {"uri","kind","expected_album","expected_uri","ts"}
STALE_ENQ_PATH          = INSTALL_DIR / "stale_enqueued.json"
STALE_ENQ_TTL_SECONDS   = 24 * 3600  # Rule 11: every guard needs a release

def _save_stale_enqueued():
    try:
        STALE_ENQ_PATH.write_text(json.dumps(stale_enqueued), encoding="utf-8")
    except Exception as _se_err:
        log(f"[stale-enq] WARNING: persist failed: {_se_err}")

def _set_stale_enqueued(coord_name, uri, kind, expected_album="", expected_uri=""):
    """Mark coord_name's EnqueuedTransportURI as known-stale after an insert-play."""
    stale_enqueued[coord_name] = {"uri": uri or "", "kind": kind,
                                  "expected_album": expected_album or "",
                                  "expected_uri": expected_uri or "",
                                  "ts": time.time()}
    _save_stale_enqueued()
    log(f"[stale-enq] {coord_name}: marker SET kind={kind} stale_uri='{(uri or '')[:80]}' "
        f"expected_album='{expected_album}' expected_uri='{(expected_uri or '')[:80]}'")

def _clear_stale_enqueued(coord_name, reason=""):
    if stale_enqueued.pop(coord_name, None) is not None:
        _save_stale_enqueued()
        log(f"[stale-enq] {coord_name}: marker cleared ({reason})")

def _get_stale_enqueued(coord_name):
    """Return the live stale-Enqueued marker for a coordinator, or None (TTL-pruned)."""
    m = stale_enqueued.get(coord_name)
    if not m: return None
    if time.time() - m.get("ts", 0) > STALE_ENQ_TTL_SECONDS:
        _clear_stale_enqueued(coord_name, "TTL expired")
        return None
    return m

def _read_enqueued_uri(device):
    """Read the CURRENT EnqueuedTransportURI from a coordinator (marker capture).
    Inserts don't touch this variable, so reading at marker-set time (just after
    the insert) still yields the previous load's URI. Fail-open: '' on error."""
    try:
        pos = device.avTransport.GetPositionInfo(InstanceID=0)
        return pos.get("EnqueuedTransportURI", "") or ""
    except Exception as _re_err:
        log(f"[stale-enq] EnqueuedTransportURI read failed on {getattr(device, 'player_name', '?')}: {_re_err}")
        return ""

try:
    if STALE_ENQ_PATH.exists():
        _se_loaded = json.loads(STALE_ENQ_PATH.read_text(encoding="utf-8"))
        _se_cut = time.time() - STALE_ENQ_TTL_SECONDS
        stale_enqueued.update({k: v for k, v in _se_loaded.items()
                               if v.get("ts", 0) > _se_cut})
        print(f"[stale-enq] loaded {len(stale_enqueued)} stale-Enqueued marker(s) from disk")
except Exception as _se_err:
    print(f"[stale-enq] WARNING: failed to load stale_enqueued.json: {_se_err}")

# --- QUEUE SOURCES (v2.60) ------------------------------------------------------
# DESIGN (queue preview enrichment, 2026-08-08): the preview's provenance line
# only knows the container that LOADED the queue; anything added afterwards
# (an album queued behind a playlist, one-off tracks) is invisible. This module
# keeps a small per-coordinator ADDITIVE list of what went into the queue so the
# page can render "Dusk & Dinner (playlist) · +Remain in Light (album) · +3 tracks".
# Deliberately INDEPENDENT of queue_provenance (which stamps history context —
# see the 2026-08-06 overlay-guard lore): a rendering nicety must never be able
# to poison history. Track adds merge into a trailing {"type":"tracks","count":N}
# accumulator. Replace/clear verbs RESET the list; truncate keeps only the head
# entry (adds after current are dropped); 24h TTL (Rule 11); capped at 8 entries.
# LIMITATION (accepted, same as provenance): queue mutations via the native
# Sonos/Spotify apps are undetectable — this list describes OUR adds only.
queue_sources          = {}   # coordinator name -> {"entries":[...], "updated_at": epoch}
QUEUE_SOURCES_PATH     = INSTALL_DIR / "queue_sources.json"
QUEUE_SOURCES_TTL_S    = 24 * 3600
QUEUE_SOURCES_MAX      = 8

def _save_queue_sources():
    try:
        QUEUE_SOURCES_PATH.write_text(json.dumps(queue_sources), encoding="utf-8")
    except Exception as _qsrc_err:
        log(f"[queue-sources] WARNING: persist failed: {_qsrc_err}")

def _reset_queue_sources(coord_name, entries, reason=""):
    """Queue replaced/cleared: the sources list starts over. NEVER raises —
    a bookkeeping failure must not break a playback verb."""
    try:
        queue_sources[coord_name] = {"entries": list(entries or []), "updated_at": time.time()}
        _save_queue_sources()
        log(f"[queue-sources] {coord_name}: RESET -> {len(entries or [])} entry(ies) ({reason})")
    except Exception as _qsrc_err:
        log(f"[queue-sources] reset failed on {coord_name} (benign): {_qsrc_err}")

def _append_queue_source(coord_name, ctype, name="", uri="", pos_start=None, num_tracks=None):
    """Something was ADDED to an existing queue. Containers append an entry;
    tracks merge into a trailing {"type":"tracks","count":N} accumulator.
    v2.62 INSERT-RANGE ATTRIBUTION: when the caller verified WHERE the add
    landed (pos_start, 1-indexed) and HOW MANY rows it expanded to (num_tracks),
    the entry carries a [pos_start, pos_end] range. Capture-side stamping
    (_range_context) may then attribute honest-blank inserted rows to this
    container — deterministic receipt, not a guess. Entries WITHOUT ranges
    remain rendering-only, exactly as v2.60 designed. NEVER raises."""
    try:
        rec = queue_sources.get(coord_name) or {}
        entries = list(rec.get("entries", []))
        if ctype == "track":
            if entries and entries[-1].get("type") == "tracks":
                entries[-1] = dict(entries[-1], count=int(entries[-1].get("count", 0)) + 1)
            else:
                entries.append({"type": "tracks", "count": 1})
        else:
            e = {"type": ctype or "container", "name": name or "", "uri": uri or ""}
            if pos_start and num_tracks and int(num_tracks) > 0:
                # v2.62: shift ranges that sit AT/AFTER the insert point — the new
                # rows pushed them down. (Insert INSIDE an existing range would
                # corrupt it: drop that range instead of guessing — honest.)
                _ps, _n = int(pos_start), int(num_tracks)
                for prev in entries:
                    if "pos_start" not in prev: continue
                    if prev["pos_start"] >= _ps:
                        prev["pos_start"] += _n
                        prev["pos_end"] += _n
                    elif prev["pos_end"] >= _ps:
                        log(f"[queue-sources] {coord_name}: insert at {_ps} lands INSIDE "
                            f"range [{prev['pos_start']},{prev['pos_end']}] of '{prev.get('name','')}' "
                            f"-- dropping that range (honest, no guessing)")
                        prev.pop("pos_start", None); prev.pop("pos_end", None)
                e["pos_start"] = _ps
                e["pos_end"] = _ps + _n - 1
                log(f"[queue-sources] {coord_name}: range [{_ps},{e['pos_end']}] recorded for '{name or ''}' ({ctype})")
            entries.append(e)
        queue_sources[coord_name] = {"entries": entries[-QUEUE_SOURCES_MAX:], "updated_at": time.time()}
        _save_queue_sources()
        log(f"[queue-sources] {coord_name}: +{ctype} '{name or ''}' ({len(entries)} entry(ies))")
    except Exception as _qsrc_err:
        log(f"[queue-sources] append failed on {coord_name} (benign): {_qsrc_err}")

def _range_context(coord_name, position):
    """v2.62: If 1-indexed queue `position` falls inside exactly one recorded
    insert range, return that source entry (dict) — else None. Used by capture
    to attribute inserted-container rows. TTL/pruning rides _get_queue_sources.
    NEVER raises."""
    try:
        if not position or int(position) <= 0:
            return None
        p = int(position)
        hits = [e for e in _get_queue_sources(coord_name)
                if e.get("type") in ("playlist", "album")
                and "pos_start" in e and e["pos_start"] <= p <= e["pos_end"]]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            log(f"[insert-range] {coord_name}: position {p} matched {len(hits)} ranges (overlap?) -- refusing to stamp")
        return None
    except Exception as _rc_err:
        log(f"[insert-range] _range_context failed (benign): {_rc_err}")
        return None

def _expansion_count(coordinator, before_size, label="", timeout_s=6.0):
    """v2.62: container adds expand ASYNCHRONOUSLY (v2.58 A9). Poll queue_size
    until two consecutive reads agree (or timeout), then return the growth
    (stable - before). Returns None when growth can't be trusted (timeout with
    no stability, read failures, shrinkage). Called AFTER playback is started
    so the wait never delays the audible verb. NEVER raises."""
    try:
        deadline = time.time() + float(timeout_s)
        prev = None
        while time.time() < deadline:
            try:
                cur = int(coordinator.queue_size)
            except Exception as _qs_err:
                log(f"[insert-range]{label} queue_size read failed during expansion wait: {_qs_err}")
                cur = None
            if cur is not None and cur == prev:
                grown = cur - int(before_size)
                if grown > 0:
                    log(f"[insert-range]{label} expansion stable: {before_size} -> {cur} (+{grown})")
                    return grown
                log(f"[insert-range]{label} expansion stable but growth={grown} -- no range")
                return None
            prev = cur
            time.sleep(0.7)
        log(f"[insert-range]{label} expansion never stabilized in {timeout_s}s (last={prev}) -- no range")
        return None
    except Exception as _ec_err:
        log(f"[insert-range]{label} _expansion_count failed (benign): {_ec_err}")
        return None

def _get_queue_sources(coord_name):
    """Live sources entries for a coordinator, or [] (TTL-pruned, Rule 11).
    NEVER raises — called from the state-push path."""
    try:
        rec = queue_sources.get(coord_name)
        if not rec: return []
        if time.time() - rec.get("updated_at", 0) > QUEUE_SOURCES_TTL_S:
            queue_sources.pop(coord_name, None)
            _save_queue_sources()
            log(f"[queue-sources] {coord_name}: TTL expired -> cleared")
            return []
        return rec.get("entries", [])
    except Exception:
        return []

try:
    if QUEUE_SOURCES_PATH.exists():
        _qsrc_loaded = json.loads(QUEUE_SOURCES_PATH.read_text(encoding="utf-8"))
        _qsrc_cut = time.time() - QUEUE_SOURCES_TTL_S
        queue_sources.update({k: v for k, v in _qsrc_loaded.items()
                              if v.get("updated_at", 0) > _qsrc_cut})
    # v2.60 boot marker (Rule 24: verify a log line UNIQUE to the new version)
    print(f"[queue-sources] v2.60 queue sources active: {len(queue_sources)} entry(ies) loaded")
    # v2.62.1 boot marker (Rule 24)
    print("[insert-range] v2.62.1 insert-range attribution active (blankish fix): "
          "capture stamps honest-blank rows INCLUDING bare x-rincon-queue containers; "
          "verified [pos_start,pos_end] receipts; [ctx-diag] field study logging")
except Exception as _qsrc_err:
    print(f"[queue-sources] WARNING: failed to load queue_sources.json: {_qsrc_err}")

# --- STALE-QUEUE GUARD (v2.58 Phase B) ----------------------------------------
# DESIGN (design_v258_release_plan.md SS4, decisions LOCKED 2026-08-04):
# Insert verbs (play_next, add_to_queue insert-next mode, play_album non-replace)
# on a STOPPED coordinator whose queue has been untouched > 24h silently convert
# the insert into a queue REPLACE (proven load-then-trim), so playback can never
# flow into forgotten leftovers. Live incident 2026-08-03: a day-old 9-row queue
# swallowed an album insert and leaked Sister Sledge/MJQ when the album ended.
# "Touched" = last service queue mutation on that coordinator OR last observed
# PLAYING transport activity, whichever is newer. Persisted per-coordinator so
# it survives service restarts. When age is unknowable (no record), treat as
# STALE -- unknown old queues are exactly the hazard (D1 = 24h).
# D2 (locked): add_to_queue in end/append mode NEVER converts -- stays literal.
queue_touched_at        = {}   # coordinator name -> epoch of last queue touch
QUEUE_TOUCHED_PATH      = INSTALL_DIR / "queue_touched.json"
STALE_QUEUE_THRESHOLD_S = 24 * 3600   # D1 (locked 2026-08-04): 24 hours
_queue_touched_lock     = threading.Lock()
_queue_touched_last_persist = 0.0

def _persist_queue_touched():
    """Write the per-coordinator touch stamps to disk. Callers hold
    _queue_touched_lock. Failure is loud but non-fatal (worst case: a stamp
    is lost across restart and the guard errs toward STALE, the safe side)."""
    global _queue_touched_last_persist
    try:
        QUEUE_TOUCHED_PATH.write_text(json.dumps(queue_touched_at), encoding="utf-8")
        _queue_touched_last_persist = time.time()
    except Exception as _qt_err:
        log(f"[stale-guard] WARNING: persist of queue_touched.json did not succeed: {_qt_err}")

def _touch_queue(coord_name, reason="", persist=True):
    """Stamp coord_name's queue as freshly touched (service queue mutation or
    observed PLAYING transport). persist=False (poll-loop PLAYING observations,
    every ~15s) throttles disk writes to one per 60s -- a stamp that is 60s
    stale on disk is irrelevant against a 24h threshold."""
    if not coord_name:
        return
    with _queue_touched_lock:
        queue_touched_at[coord_name] = time.time()
        if persist or (time.time() - _queue_touched_last_persist) > 60:
            _persist_queue_touched()
    if reason:
        log(f"[stale-guard] {coord_name}: queue touched ({reason})")

def _fmt_queue_age(age_s):
    """Human age string for stale-guard honesty lines, e.g. '2d 4h' / '3h 12m'."""
    try:
        age_s = int(age_s)
        d, rem = divmod(age_s, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d > 0:
            return f"{d}d {h}h"
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"
    except Exception:
        return "?"

def _queue_is_stale(coord_name):
    """Return (is_stale, age_str). Stale = untouched > STALE_QUEUE_THRESHOLD_S.
    No record at all -> (True, 'unknown age') -- the unknown-old-queue hazard."""
    with _queue_touched_lock:
        ts = queue_touched_at.get(coord_name)
    if not ts:
        return True, "unknown age"
    age = time.time() - ts
    return (age > STALE_QUEUE_THRESHOLD_S), _fmt_queue_age(age)

try:
    if QUEUE_TOUCHED_PATH.exists():
        _qt_loaded = json.loads(QUEUE_TOUCHED_PATH.read_text(encoding="utf-8"))
        if isinstance(_qt_loaded, dict):
            queue_touched_at.update({k: float(v) for k, v in _qt_loaded.items()})
    print(f"[stale-guard] loaded {len(queue_touched_at)} queue-touch stamp(s) from disk")
except Exception as _qt_err:
    print(f"[stale-guard] WARNING: failed to load queue_touched.json: {_qt_err} (all queues treated as stale until touched)")
# v2.58 boot banner (Rule 24 SS3 / release checklist: verify a log line UNIQUE
# to the new version post-update). Deterministic -- do not reword.
print("[stale-guard] armed: threshold=24h")
# v2.59 boot banner (Rule 24 §3: verify a log line UNIQUE to the new version
# post-update). Deterministic -- do not reword.
print("[v2.59] capture sanitize active (L1/L2/L3) + cu wire + svc-name normalize")
# v2.59.1 boot banner (Rule 24 §3: log line UNIQUE to this version)
print("[v2.59.1] play_next provenance fix active: playlist REPLACE + stream takeover now set/clear queue provenance")
print("[v2.59.2] overlay guard active: album-typed provenance validated at stamp time (skip + self-heal on mismatch)")
# v2.61 boot marker (Rule 24 §3: verify a log line UNIQUE to the new version)
print("[v2.61] fuzzy ctx-name match active: decorated container names no longer read as stale; stream hidden-queue watch on")

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
        "didl_parent_id": track_info.get("didl_parent_id", ""),
        "didl_album_art_uri": track_info.get("didl_album_art_uri", ""),
    }
    # v2.57 rider: ring rows feed the page's provisional sessions (p2.89 cold-load
    # backfill) — give them the same context + duration fields the SSE pending
    # rows carry, with the v2.55 provenance overlay applied for queue playback.
    # v2.59 F9: track_info["container"] arrives PRE-SANITIZED (sanitize_container
    # runs upstream in get_track_info) — ring context fields are clean at source.
    try:
        _c = track_info.get("container") or {}
        _ctx_uri = _c.get("container_uri", "")
        _ctx_type = _c.get("container_type", "")
        # v2.59 Q6: injected one-offs carry NO context — block the overlay here
        # exactly as post_history does (honest no-context on all surfaces).
        if (((not _ctx_uri) or _ctx_uri.startswith("x-rincon-queue"))
                and track_info.get("context_source") != "inserted_track"):
            _rg_coord = (track_info.get("coordinator")
                         or (rooms_list[0] if isinstance(rooms_list, list) and rooms_list else ""))
            _prov = _get_queue_provenance(_rg_coord)
            # v2.59.2 overlay guard: never stamp a provably-stale album pointer
            # (guard also self-heals by clearing it; see _overlay_prov_guard).
            if (_prov and _prov.get("uri")
                    and not _overlay_prov_guard(_prov, track_info.get("album", ""), _rg_coord)):
                _ctx_uri, _ctx_type = _prov["uri"], _prov["type"]
        entry["context_uri"] = _ctx_uri or ""
        entry["context_type"] = _ctx_type or ""
        _dur = track_info.get("duration_seconds") or 0
        entry["duration_ms"] = int(_dur * 1000) if _dur else None
    except Exception as _en_err:
        log(f"[state-ring] enrichment failed (benign): {_en_err}")
    _state_ring_buffer.insert(0, entry)
    while len(_state_ring_buffer) > STATE_RING_MAX:
        _state_ring_buffer.pop()
    _persist_state_ring_buffer()

def _update_state_ring_rooms(track_info, started_str, rooms_list):
    """v2.50: when a grouped room coalesces into an existing buffered play, grow
    the matching ring entry's rooms string in place (match on title+artist+ts).
    NEVER raises — ring maintenance must not break history buffering."""
    try:
        for entry in _state_ring_buffer:
            if (entry.get("title") == track_info.get("title", "")
                    and entry.get("artist") == track_info.get("artist", "")
                    and entry.get("timestamp") == started_str):
                entry["rooms"] = ", ".join(rooms_list)
                _persist_state_ring_buffer()
                return
        log(f"[state-ring] coalesce update: no matching entry for '{track_info.get('title','')}' @ {started_str} (benign no-op)")
    except Exception as e:
        log(f"[state-ring] coalesce update failed: {e}")

# v2.57: stream-source URI prefixes (no queue to insert into). Shared by
# play_next's stream detection and _build_queue_summary. x-sonos-vli = live
# session source (Spotify Connect / AirPlay) — treated as a stream since v2.52.1.
STREAM_URI_PREFIXES = ("x-rincon-mp3radio:", "x-sonosapi-stream:", "x-sonosapi-radio:",
                       "x-sonos-htastream:", "x-rincon-stream:", "aac:", "x-sonosapi-hls:",
                       "x-sonos-vli:")
# v2.61: last-logged hidden-queue depth per coordinator during stream playback
# (change-only logging for the queue_rows_hidden watch; in-memory only).
_stream_qrows_seen = {}
# v2.57 boot marker (Rule 24 §3: verify a log line UNIQUE to the new version)
print("[queue-mgmt] v2.57 queue management active: replace_queue / truncate_queue / queue_summary")

def _build_queue_summary():
    """v2.57 queue management (design_queue_management_v2 §3.3, D7/MG4):
    per-coordinator queue map for the state file. Level-triggered — published on
    EVERY state push so it is self-healing (Rule 27: messages carry state).
    Keyed by coordinator name so multiple simultaneous group queues are each
    fully described (MG4). One entry per coordinator whose group is active
    (PLAYING/PAUSED) or STOPPED with a non-empty queue; streams get
    {"stream": true, "stream_label"} instead of queue fields.
    NEVER raises — a queue read failure must not break the state push."""
    out = {}
    try:
        for g in _poll_snapshot.get("groups", []):
            cname = g.get("coordinator") or ""
            state = g.get("state", "")
            if not cname or state.startswith("ERROR"):
                continue
            dev = current_devices_by_name.get(cname)
            if not dev:
                continue
            active = state in ("PLAYING", "PAUSED_PLAYBACK", "TRANSITIONING")
            try:
                if state == "PLAYING_TV":
                    out[cname] = {"stream": True, "stream_label": "TV / line-in"}
                    continue
                if active:
                    # Stream check only for active groups — stopped groups render
                    # from their (possibly stale) queue, which is exactly what the
                    # sheet needs for the stale-queue scenario.
                    try:
                        mi = dev.avTransport.GetMediaInfo([("InstanceID", 0)])
                        cur_uri = (mi.get("CurrentURI") or "").lower()
                        if any(cur_uri.startswith(p) for p in STREAM_URI_PREFIXES):
                            ti = (room_state.get(cname) or {}).get("track_info") or {}
                            _st_entry = {"stream": True,
                                         "stream_label": ti.get("title") or ti.get("service") or "stream"}
                            # v2.61 watch (backlog "queue rows=0 during x-sonos-vli
                            # casts"): a live-session cast often coexists with a real
                            # queue the user wants back. Carry the hidden depth in
                            # state (Rule 27: state, not events); log only on change.
                            try:
                                _st_q = int(dev.queue_size)
                                if _st_q:
                                    _st_entry["queue_rows_hidden"] = _st_q
                                if _stream_qrows_seen.get(cname) != _st_q:
                                    _stream_qrows_seen[cname] = _st_q
                                    log(f"[queue-summary] {cname}: stream active, "
                                        f"{_st_q}-row queue retained behind it")
                            except Exception:
                                pass
                            out[cname] = _st_entry
                            continue
                    except Exception:
                        pass  # can't read media info -> fall through to queue fields
                qsize = dev.queue_size
                if not qsize and not active:
                    continue  # stopped + empty queue -> no entry (design §3.3)
                cur_pos = 0
                try:
                    cur_pos = int(dev.get_current_track_info().get("playlist_position", 0))
                except Exception:
                    pass
                upcoming = []
                try:
                    # get_queue start is 0-indexed; playlist_position is 1-indexed,
                    # so start=cur_pos yields the tracks AFTER the current one.
                    # v2.60: depth 4 -> 8, and items become {"title","artist"} objects
                    # (creator rides along free in the same queue read). Page p3.10+
                    # renders both shapes; older pages only ever saw strings.
                    for it in dev.get_queue(start=cur_pos, max_items=8):
                        t = getattr(it, "title", "") or ""
                        if t:
                            upcoming.append({"title": t,
                                             "artist": getattr(it, "creator", "") or ""})
                except Exception:
                    pass
                prov = _get_queue_provenance(cname) or {}
                _pname, _ptype = prov.get("name", ""), prov.get("type", "")
                _loaded = prov.get("loaded_at")
                out[cname] = {
                    # Absent/unknown provenance -> null label; the page renders
                    # "Queue: N tracks - source unknown" (honest, design §3.3).
                    "provenance_label": (f"{_pname} ({_ptype})" if _pname and _ptype
                                         else (_pname or None)),
                    "container_type": _ptype or None,
                    "container_uri": prov.get("uri") or None,
                    "loaded_at": (datetime.fromtimestamp(_loaded, tz=timezone.utc)
                                  .strftime("%Y-%m-%dT%H:%M:%SZ") if _loaded else None),
                    "track_count": qsize,
                    "current_pos": cur_pos,
                    "upcoming": upcoming,
                    # v2.60: additive sources chain (what our verbs put in this queue)
                    "sources": _get_queue_sources(cname),
                }
            except Exception as qe:
                log(f"[queue-summary] {cname}: {qe}")
    except Exception as e:
        log(f"[queue-summary] build failed: {e}")
    return out

def _build_state_payload():
    """Build the state-{house}.json payload from current live state.
    v2.24: Reads from _poll_snapshot instead of calling get_rooms_playing()."""
    # Now playing: derive from room_state (same data source as SSE)
    np = None
    rp = list(_poll_snapshot.get("rooms_playing", []))
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
                "play_modes": dict(_poll_snapshot.get("play_modes", {})),
                "timestamp": now_iso(),
            }
            break  # first active coordinator

    # v2.51: version + boot_time stamp. state-{house}.json is pushed by the
    # client DIRECTLY to GitHub (bypassing webhooks), so it is the agent's only
    # true PULL channel while a session is open (standing rule 25: webhook
    # events queue behind open sessions). These two fields let the agent verify
    # a fleet update mid-session without waiting for a queued heartbeat.
    _boot_iso = (datetime.fromtimestamp(_service_start_ts, tz=timezone.utc)
                 .strftime("%Y-%m-%dT%H:%M:%SZ")) if _service_start_ts else None
    return {
        "house": house,
        "version": SERVICE_VERSION,
        "boot_time": _boot_iso,
        "last_updated": now_iso(),
        "now_playing": np,
        "rooms_playing": rp,
        "rooms_paused": list(_poll_snapshot.get("rooms_paused", [])),
        "paused_at": dict(_poll_snapshot.get("paused_at", {})),  # v2.54 rider
        "rooms_all": list(_poll_snapshot.get("rooms_all", [])),
        # v2.58 A6: full group topology (coordinator + members + transport state)
        # so cold-load UIs stop falling back to now_playing.rooms for member
        # chips. Level-triggered: published on EVERY push (Rule 27).
        "groups": list(_poll_snapshot.get("groups", [])),
        "queue_summary": _build_queue_summary(),  # v2.57 queue management (§3.3)
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
_ntfy_last_event_ts      = 0.0    # monotonic ts of last ntfy stream event (keepalive or message)
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
# v2.48: matching is CASE-INSENSITIVE. The old case-sensitive filter missed
# transport errors like "ntfy stream error" and "ConnectionResetError", so they
# never reached the error ring and were invisible to the watchdog audits.
_ERROR_KEYWORDS  = ("error", "fail", "traceback", "exception", "critical", "crash", "upnp", "http 4", "http 5", "timed out", "refused", "unreachable")
_ERROR_BENIGN    = ("errors: 0", "0 error", "no error", "recent_errors", "error_lines", "(benign no-op)",
                    # v2.50: crash-RECOVERY lines are informational successes, not errors --
                    # "Recovering N buffered track(s) from crash" and "[OK] Flushed ... [crash-recovery]"
                    # matched the "crash" keyword and polluted the error ring for days.
                    # NOTE: deliberately NO blanket "[ok]" token -- "[OK] Flushed ... -> HTTP 500"
                    # is a real error and must still reach the ring. "-> http 200" makes any
                    # success-status line benign regardless of other keyword matches.
                    "recovering ", "-> http 200")  # noise guards; v2.48.2: lines tagged '(benign no-op)' are deliberate non-events, never errors

# Command results ring buffer — structured outcomes for agent-side correlation
_CMD_RESULTS_MAX = 50  # v2.48: 20 -> 50 so command bursts survive until the debounced heartbeat delivers them
_command_results = deque(maxlen=_CMD_RESULTS_MAX)
_command_results_lock = threading.Lock()

def record_command_result(action, success, message, cmd_ts=None, detail=None, queue_op=None):
    """Append a structured command outcome to the ring buffer.
    v2.58 A7: queue_op (optional dict) carries coordinator / group_members /
    transport_state / queue_before / queue_after / pos_landed / converted_from
    for queue-affecting verbs, so heartbeat command_results are self-describing
    about WHOSE queue was touched (silent verbs never POST a full result)."""
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
    if queue_op:
        entry["queue_op"] = queue_op
    # v2.58 A4 FIX (lost clear_queue result, 2026-08-04 03:12Z): seq is assigned
    # AND the entry appended under ONE _delta_lock hold, so build_delta_fields
    # (which snapshots counters + deque under the same lock) can never observe a
    # counted-but-absent entry. Root cause of the loss: the delta builder sliced
    # the deque by COUNT (all_cmds[-cship:]) -- a result appended between the
    # counter snapshot and the deque read displaced an older undelivered entry,
    # which was then committed as delivered without ever shipping.
    global _cmd_total
    with _delta_lock:
        _cmd_total += 1
        entry["seq"] = _cmd_total
        with _command_results_lock:
            _command_results.append(entry)

def get_command_results():
    """Return recent command results for embedding in heartbeats."""
    with _command_results_lock:
        return list(_command_results)

# --- DELTA DELIVERY (v2.49) ---------------------------------------------------
# DESIGN NOTE: deliver-once piggyback payloads. Before v2.49 every webhook POST
# re-shipped 100 log lines + 50 errors + 50 command results (the same ones, over
# and over) — ~64% of every payload was duplicate piggyback. Now each ring has a
# monotonic "total appended" counter and a "delivered" high-water mark; POSTs
# carry only lines/results the agent has NOT yet seen (capped), and the mark
# advances ONLY after an HTTP 2xx (failed POSTs lose nothing — items re-ship on
# the next attempt). Boot/ready heartbeats are exempt for logs (standing rule:
# raw console visibility on boot). get_logs full dump is untouched.
_delta_lock      = threading.Lock()
_log_total       = 0   # incremented in log() for every ring append
_err_total       = 0   # incremented in log() for every error-ring append
_cmd_total       = 0   # incremented in record_command_result()
_log_delivered   = 0
_err_delivered   = 0
_cmd_delivered   = 0
_LOG_DELTA_MAX   = 60  # cap per POST; overflow reported via delta.logs_suppressed
_ERR_DELTA_MAX   = 10
_CMD_DELTA_MAX   = 10
_BOOT_LOG_LINES  = 50  # boot/ready heartbeats always carry last 50 lines (standing rule)
_delta_boot_logged = False

def build_delta_fields(boot=False):
    """Return (fields, snap): piggyback fields containing only undelivered
    log lines / errors / command results, plus a snapshot to pass to
    _delta_commit() after the POST succeeds (HTTP 2xx). If boot=True, logs are
    the last _BOOT_LOG_LINES lines regardless of delivery state."""
    global _delta_boot_logged
    if not _delta_boot_logged:
        _delta_boot_logged = True
        # NOTE: wording avoids _ERROR_KEYWORDS substrings ("err" not "errors")
        # so this info line never lands in the error ring as a false positive.
        log(f"[delta] deliver-once payloads active (caps: logs {_LOG_DELTA_MAX} / err {_ERR_DELTA_MAX} / cmd {_CMD_DELTA_MAX}; boot exempt {_BOOT_LOG_LINES} lines)")
    with _delta_lock:
        lt, et, ct = _log_total, _err_total, _cmd_total
        ld, ed, cd = _log_delivered, _err_delivered, _cmd_delivered
        # v2.58 A4: snapshot the results deque under the SAME lock hold as the
        # counters -- entries are appended with their seq under this lock, so
        # every seq <= ct is guaranteed present in this snapshot.
        with _command_results_lock:
            _cmds_snapshot = list(_command_results)
    fields = {}
    delta_meta = {}
    # Logs
    log_new = max(0, lt - ld)
    if boot:
        fields["recent_logs"] = get_recent_logs(_BOOT_LOG_LINES)
        delta_meta["logs_new"] = log_new
        delta_meta["boot_full_logs"] = True
    else:
        ship = min(log_new, _LOG_DELTA_MAX)
        fields["recent_logs"] = get_recent_logs(ship) if ship else []
        delta_meta["logs_new"] = log_new
        if log_new > ship:
            delta_meta["logs_suppressed"] = log_new - ship  # evicted or over cap; get_logs has the ring
    # Errors
    err_new = max(0, et - ed)
    eship = min(err_new, _ERR_DELTA_MAX)
    fields["recent_errors"] = get_recent_errors(eship) if eship else []
    delta_meta["errors_new"] = err_new
    if err_new > eship:
        delta_meta["errors_suppressed"] = err_new - eship
    # Command results -- v2.58 A4: seq-windowed selection replaces count-based
    # slicing. Only entries with cd < seq <= ct ship; entries appended DURING
    # payload assembly (seq > ct) are excluded here and excluded from the
    # commit, so they ship on the next POST instead of being silently skipped.
    cmd_new = max(0, ct - cd)
    _cmd_undelivered = [e for e in _cmds_snapshot if cd < e.get("seq", 0) <= ct]
    cship = min(len(_cmd_undelivered), _CMD_DELTA_MAX)
    fields["command_results"] = _cmd_undelivered[-cship:] if cship else []
    delta_meta["cmds_new"] = cmd_new
    if cmd_new > cship:
        delta_meta["cmds_suppressed"] = cmd_new - cship
        log(f"[delta] command results over cap or evicted: shipping {cship} of {cmd_new} undelivered (get_logs dump has the full ring)")
    fields["delta"] = delta_meta
    return fields, (lt, et, ct)

def _delta_commit(snap):
    """Advance delivered high-water marks after a successful (2xx) POST.
    Monotonic (max) so out-of-order commits can never move marks backward."""
    global _log_delivered, _err_delivered, _cmd_delivered
    if not snap:
        return
    try:
        lt, et, ct = snap
        with _delta_lock:
            _log_delivered = max(_log_delivered, lt)
            _err_delivered = max(_err_delivered, et)
            _cmd_delivered = max(_cmd_delivered, ct)
    except Exception as _dc_err:
        log(f"[delta] commit failed (payloads will re-ship, no data lost): {_dc_err}")

def delta_pending_counts():
    """Return (undelivered_cmds, undelivered_errors) — used by the purposeful
    debounced-heartbeat gate (v2.49): no undelivered payload = no POST."""
    with _delta_lock:
        return max(0, _cmd_total - _cmd_delivered), max(0, _err_total - _err_delivered)

def _rotate_log_if_needed():
    """Trim log file to last 800 lines if it exceeds 500 KB."""
    try:
        if LOG_FILE.stat().st_size > 500_000:
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            LOG_FILE.write_text("\n".join(lines[-800:]) + "\n", encoding="utf-8")
    except Exception:
        pass

def log(msg):
    global _log_write_count, _log_total, _err_total
    line = f"[{now_iso()}] {msg}"
    print(line, flush=True)
    # Append to in-memory ring buffer (lock-free deque is thread-safe for appends,
    # but we use a lock for the snapshot reads in get_recent_logs/get_full_logs)
    with _log_ring_lock:
        _log_ring.append(line)
    # v2.49 delta delivery: count every ring append
    with _delta_lock:
        _log_total += 1
    # Also capture to error ring if line matches any error keyword (v2.48: case-insensitive)
    # v2.48.1: skip [DIDL-*] debug dumps — the DIDL XML contains "xmlns:upnp", which
    # matched the case-insensitive "upnp" keyword and flooded the error ring with
    # metadata dumps that aren't errors.
    _mlow = msg.lower()
    if (not _mlow.startswith("[didl-")) and any(kw in _mlow for kw in _ERROR_KEYWORDS) and not any(b in _mlow for b in _ERROR_BENIGN):
        with _error_ring_lock:
            _error_ring.append(line)
        # v2.49 delta delivery: count every error-ring append
        with _delta_lock:
            _err_total += 1
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
        # v2.49: trimmed 100->30 / 50->15. Error posts stay OUTSIDE delta
        # accounting on purpose — error context deserves redundant delivery.
        "recent_logs": get_recent_logs(30),
        "recent_errors": get_recent_errors(15),
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
    """Check VERSION file on GitHub; download + restart if version changed."""
    import ast as _ast
    try:
        r = gh_get("VERSION", retries=1)
        if not r:
            log("Version check: GitHub unavailable (will retry next cycle)")
            return
        latest = gh_decode(r).strip()
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
        import hashlib as _hl
        new_hash = _hl.sha256(new_code.encode("utf-8")).hexdigest()[:12]
        log(f"Downloaded: {len(new_code)} bytes, sha256={new_hash}")
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
        # No version-mismatch check needed: VERSION file IS the source of truth.
        # The .py no longer contains a hardcoded version — it reads VERSION at startup.
        # Compare with current file to detect no-op updates
        try:
            _cur_code = Path(sys.argv[0]).resolve().read_text(encoding="utf-8")
            _cur_hash = _hl.sha256(_cur_code.encode("utf-8")).hexdigest()[:12]
            if _cur_hash == new_hash:
                log(f"[!] Downloaded code is IDENTICAL to running code (both {new_hash}). Possible wrong-file push or stale cache.")
            else:
                log(f"Code diff confirmed: running={_cur_hash} -> new={new_hash}")
        except Exception:
            pass  # non-fatal
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
        # Write VERSION file alongside the .py so the new process reads it at startup
        ver_path = flag_dir / "VERSION"
        ver_path.write_text(latest + "\n", encoding="utf-8")
        log(f"Wrote VERSION file: {ver_path} = {latest}")
        log(f"Updated to v{latest} -- restarting in new window...")
        # v2.44 CRITICAL FIX: flush buffered + in-flight history BEFORE handing off.
        # Previously os._exit(0) discarded pending_buffer and the currently-playing
        # room_state tracks -- guaranteed data loss on every self-update.
        try:
            _now = datetime.now(timezone.utc)
            for _room, _st in list(room_state.items()):
                try:
                    if _st and _st.get("track_key") and _st.get("started_at"):
                        log(f"[update] retiring in-flight track on {_room}: {_st['track_key'][:80]}")
                        post_history(_st["track_info"], _room, _st["started_at"], _now)
                except Exception as _rerr:
                    log(f"[update] WARNING: retire failed for {_room}: {_rerr}")
            flush_buffer("pre-update")
            log("[update] pre-update history flush complete")
        except Exception as _ferr:
            log(f"[update] WARNING: pre-update flush failed: {_ferr}")
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
def _build_poll_snapshot(coordinators):
    """Build a single poll snapshot from the coordinator list. Called once per cycle.
    Replaces the old get_rooms_playing() which was called 3x per cycle (~99 HTTP calls).
    Now: one pass over coordinators only (~5-25 calls total)."""
    global _current_play_modes, _current_mute_states, _poll_snapshot
    if "sonos" not in modules:
        _poll_snapshot = {"rooms_playing": [], "rooms_paused": [], "rooms_all": sorted(current_devices_by_name.keys()),
                          "play_modes": {}, "mute_states": {}, "transport_states": {}, "timestamp": now_iso()}
        return
    playing = []
    states = {}
    modes = {}
    mutes = {}
    for dev in coordinators:
        name = dev.player_name
        try:
            info = dev.get_current_transport_info()
            state = info.get("current_transport_state", "STOPPED")
            states[name] = state
            # Capture mute state for all coordinators (not just playing ones)
            try:
                mutes[name] = bool(dev.mute)
            except Exception:
                # v2.47.2: carry over last known mute on transient read failure.
                # A vanishing dict key made mute_states != last cycle's dict,
                # firing a spurious status_update SSE every poll (idle chatter bug).
                if name in _current_mute_states:
                    mutes[name] = _current_mute_states[name]
            if state == "PLAYING":
                # Skip TV/line-in passthrough -- soundbars report PLAYING for external audio
                try:
                    track_uri = dev.get_current_track_info().get("uri", "")
                    if track_uri.startswith(("x-sonos-htastream:", "x-rincon-stream:")):
                        states[name] = "PLAYING_TV"
                        continue
                except Exception:
                    pass  # If we can't check, include it
                # Capture play mode for this coordinator (repeat/shuffle status)
                try:
                    mode = dev.play_mode
                    modes[name] = mode
                except Exception:
                    pass
                # v2.58 Phase B: observed PLAYING transport counts as a queue
                # touch (a playing queue is by definition not stale). persist=False
                # -> in-memory stamp every poll, disk write throttled to 60s.
                _touch_queue(name, persist=False)
                # Coordinator is playing -- add it and all grouped members.
                # v2.36: Simplified — use dev.group.members directly (same as get_track_info).
                # Old IP-verification code silently dropped members when SoCo cache was stale.
                playing.append(name)
                if dev.group:
                    try:
                        for member in dev.group.members:
                            # v2.54 rider: bonded/invisible units (Sub, stereo-pair
                            # slaves) leak into rooms lists via group membership —
                            # discovery excludes them but group.members does not.
                            if not getattr(member, "is_visible", True):
                                continue
                            mname = member.player_name
                            if mname == name:
                                continue
                            playing.append(mname)
                            if mname not in current_devices_by_name:
                                current_devices_by_name[mname] = member
                    except Exception as e:
                        log(f"[snapshot] group member enumeration error for {name}: {e}")
            elif state == "PAUSED_PLAYBACK":
                pass  # captured in states dict, used for rooms_paused below
        except Exception as e:
            states[name] = f"ERROR:{e}"
            # v2.47.2: transport-info failure also skipped the mute read —
            # carry over last known mute so the key doesn't flap out of the dict.
            if name in _current_mute_states:
                mutes[name] = _current_mute_states[name]

    _last_transport_states.clear()
    _last_transport_states.update(states)
    _current_play_modes = modes
    _current_mute_states = mutes

    rooms_playing = sorted(set(playing))
    rooms_paused = sorted(
        name for name, st in states.items()
        if st == "PAUSED_PLAYBACK" and name not in rooms_playing
    )

    # v2.54 rider: stamp WHEN each room entered paused state, so the UI can show
    # real pause ages on cold load instead of history-approximated ones. A room
    # keeps its original stamp while it stays paused; leaving paused clears it.
    _now_stamp = now_iso()
    for _pr in rooms_paused:
        if _pr not in _paused_at_by_room:
            _paused_at_by_room[_pr] = _now_stamp
    for _pr in list(_paused_at_by_room.keys()):
        if _pr not in rooms_paused:
            del _paused_at_by_room[_pr]

    # v2.53: capture FULL group topology for ALL coordinators, regardless of
    # transport state. Before this, group members were only enumerated for
    # PLAYING coordinators — a paused/stopped group degraded to its bare
    # coordinator name on the wire, so the UI showed 2 chips when 5 rooms were
    # grouped (2026-07-22 bug). groups = [{"coordinator", "members", "state"}].
    groups = []
    for dev in coordinators:
        name = dev.player_name
        members = [name]
        try:
            if dev.group:
                # v2.54 rider: filter invisible bonded units (Sub/pair slaves) and
                # dedupe — bonded pairs share a player_name, producing the
                # "Garage, Garage" duplicate chips seen in the relay payload.
                _vis = [m for m in dev.group.members if getattr(m, "is_visible", True)]
                members = sorted(set(m.player_name for m in _vis))
                for m in _vis:
                    if m.player_name not in current_devices_by_name:
                        current_devices_by_name[m.player_name] = m
        except Exception as e:
            log(f"[snapshot] group topology enumeration error for {name}: {e}")
        groups.append({
            "coordinator": name,
            "members": members,
            "state": states.get(name, "UNKNOWN"),
        })
    groups.sort(key=lambda g: g["coordinator"])

    _poll_snapshot = {
        "rooms_playing": rooms_playing,
        "rooms_paused": rooms_paused,
        "paused_at": dict(_paused_at_by_room),  # v2.54 rider
        "rooms_all": sorted(current_devices_by_name.keys()),
        "groups": groups,
        "play_modes": dict(modes),
        "mute_states": dict(mutes),
        "transport_states": dict(states),
        "timestamp": now_iso(),
    }

    # v2.53: topology change detection — any regroup (from ANY controller:
    # native Sonos app, us, Alexa) fires a "topology" SSE event so every open
    # page updates its chips without a manual refresh. Signature deliberately
    # ignores transport state (play/pause is status_update's job, Rule 5).
    global _last_topology_sig
    try:
        sig = json.dumps([[g["coordinator"], g["members"]] for g in groups])
        if _last_topology_sig is not None and sig != _last_topology_sig:
            log(f"[topology] group change detected -> SSE topology event")
            publish_ui_event("topology", {})
        _last_topology_sig = sig
    except Exception as e:
        log(f"[topology] change detection error: {e}")


def get_rooms_playing():
    """Returns rooms_playing from the cached poll snapshot.
    v2.24: No longer queries speakers directly — reads from _poll_snapshot
    built once per cycle by _build_poll_snapshot()."""
    return list(_poll_snapshot.get("rooms_playing", []))


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
            # v2.45: populate staleness age so the [STALE] diagnostic can actually fire
            "last_event_age_s": int(time.time() - _ntfy_last_event_ts) if _ntfy_last_event_ts else None,
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

    # ntfy health — always show
    ntfy = snapshot.get("ntfy", {})
    if ntfy.get("connected"):
        age = ntfy.get("last_event_age_s")
        age_str = _format_age(age) if age is not None else "?"
        ntfy_str = f"  ntfy: connected | last event {age_str}"
        if age is not None and age > 120:
            ntfy_str += " [STALE]"
        lines.append(ntfy_str)
    else:
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

def heartbeat_fields(boot=False):
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
        # v2.24: Read from poll snapshot instead of querying speakers
        fields["rooms_playing"] = list(_poll_snapshot.get("rooms_playing", []))
        fields["rooms_paused"] = list(_poll_snapshot.get("rooms_paused", []))
        fields["paused_at"] = dict(_poll_snapshot.get("paused_at", {}))  # v2.54 rider
        fields["rooms_all"] = list(_poll_snapshot.get("rooms_all", []))
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
    # v2.49: piggyback payloads are DELTAS — only lines/results the agent hasn't
    # seen yet ride along (boot=True ships last 50 log lines per standing rule).
    # POST sites must pop fields["_delta_snap"] before sending and pass it to
    # _delta_commit() after HTTP 2xx; skipping the commit only means re-shipping.
    _dfields, _dsnap = build_delta_fields(boot=boot)
    fields.update(_dfields)
    fields["_delta_snap"] = list(_dsnap)
    return fields

# v2.49: the v2.48 pure-idle trimming state is gone — delta delivery makes every
# idle payload near-empty by construction. hb_light survives as a flag only.

def _send_heartbeat():
    sse_relay = build_sse_relay_payload()
    payload = {"type": "heartbeat"}
    fields = heartbeat_fields()
    snap = fields.pop("_delta_snap", None)  # v2.49: never ship the snapshot
    try:
        _pend_cmds, _pend_errs = delta_pending_counts()
        if (not (fields.get("rooms_playing") or []) and not fields.get("now_playing_tracks")
                and _pend_cmds == 0 and _pend_errs == 0):
            fields["hb_light"] = True
            log("* Heartbeat: pure-idle (delta payload near-empty)")
    except Exception as _le:
        log(f"* Heartbeat: light-flag check failed ({_le}) -- sending anyway")
    payload.update(fields)
    if sse_relay:
        payload["sse_relay"] = sse_relay
    try:
        r = requests.post(WEBHOOK, json=payload, timeout=10)
        log(f"* Heartbeat (standalone) -> HTTP {r.status_code}")
        if 200 <= r.status_code < 300:
            _delta_commit(snap)  # v2.49: mark piggyback delivered
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
SSE_MIN_GAP_S     = 15.0              # absolute floor between sends — 240/hr max, under ntfy 250/hr limit
SSE_BACKOFF_STEPS = [30, 120, 300, 600, 900, 1800]  # 30s, 2m, 5m, 10m, 15m, 30m (caps at 30m)

# --- v2.56 SSE LIVE HISTORY (design_sse_live_history_v1.md Rev 2, Rule 27) ---
# D1: every SSE push carries pending_session — a compact STATE SNAPSHOT of the
#     history buffer (never deltas: any push may be lost; the next carries all).
# D2: trim ladder under SSE_BODY_BUDGET — (1) strip art, (2) cap rows stepwise,
#     floor = freshness fields only. Verbose log at every step (no silent trims).
# D8: pending_count / buffer_head_ts / boot_id ride EVERY push, never trimmed.
# Measured 2026-07-30 (design step 0): ntfy.sh flips body->attachment at 4096
# bytes (4079 = body, 4183 = attachment). Budget = 3072 (25% headroom).
SSE_BODY_BUDGET   = 3072
_SSE_PENDING_CAPS = (12, 8, 6, 4, 3, 2, 1)  # D2 step-2 row caps, tried in order

def _compact_pending_rows():
    """Map pending_buffer items to compact wire rows (design §4). Never raises."""
    with pending_buffer_lock:
        items = list(pending_buffer)
    rows = []
    for it in items:
        try:
            row = {"t": it.get("title", ""), "a": it.get("artist", ""),
                   "al": it.get("album", "")}
            try:
                row["s"] = int(datetime.strptime(it.get("started_at", ""), "%Y-%m-%dT%H:%M:%SZ")
                               .replace(tzinfo=timezone.utc).timestamp())
            except Exception:
                row["s"] = int(it.get("_buffered_at") or time.time())
            row["d"] = int(it.get("duration_played_seconds") or 0)
            row["rm"] = it.get("rooms") or ([it.get("room")] if it.get("room") else [])
            uri = it.get("uri") or ""
            if uri:
                row["u"] = uri
            if uri.startswith("spotify:track:"):
                row["id"] = uri.rsplit(":", 1)[-1]
            art = it.get("didl_album_art_uri") or ""
            if art.startswith("https://"):
                row["art"] = art  # public URLs only; speaker-LAN getaa URLs are dead off-LAN
            cx = it.get("container_name") or ""
            # v2.59 V-2 (C2/Q4): prefer the decoded spotify_context as cu so the
            # page's stale-container guard can judge x-rincon-cpcontainer loads
            # (S1: real Enqueued containers are x-* and were dropped from the
            # wire, blinding the guard). Never send raw x-* URIs as cu.
            cu = it.get("spotify_context") or it.get("container_uri") or ""
            if cx: row["cx"] = cx
            if cu and not cu.startswith("x-"): row["cu"] = cu
            rows.append(row)
        except Exception as e:
            log(f"[sse-pending] row compact failed (skipping item): {e}")
    return rows

def _attach_pending_session(payload):
    """v2.56 D1/D2/D8: attach the pending_session snapshot + always-on freshness
    fields to an outbound SSE payload, trimming to SSE_BODY_BUDGET. Never raises —
    live-history decoration must never break the push that carries it."""
    try:
        rows = _compact_pending_rows()
        # D8 freshness fields FIRST — they survive every trim and any row failure.
        payload["pending_count"] = len(rows)
        payload["buffer_head_ts"] = (min(r.get("s", 0) for r in rows) if rows else None)
        payload["boot_id"] = int(_service_start_ts) if _service_start_ts else None
        if not rows:
            payload["pending_session"] = []
            return
        def _sz(p):
            try: return len(json.dumps(p).encode("utf-8"))
            except Exception: return SSE_BODY_BUDGET + 1
        payload["pending_session"] = rows
        sz = _sz(payload)
        if sz <= SSE_BODY_BUDGET:
            log(f"[sse-pending] snapshot: {len(rows)} rows, {sz}B (fits)")
            return
        # D2 step 1: strip art (cosmetic; page renders an initials placeholder)
        for r in rows: r.pop("art", None)
        sz = _sz(payload)
        log(f"[sse-pending] trim step 1 (art stripped): {len(rows)} rows, {sz}B")
        if sz <= SSE_BODY_BUDGET: return
        # D2 step 2: cap rows (newest kept), stepping down until it fits
        for cap in _SSE_PENDING_CAPS:
            if cap >= len(rows): continue
            payload["pending_session"] = rows[-cap:]
            sz = _sz(payload)
            log(f"[sse-pending] trim step 2 (cap {cap}): {sz}B")
            if sz <= SSE_BODY_BUDGET: return
        # Floor: freshness fields only — the chip still tells the truth (D8).
        payload["pending_session"] = []
        log(f"[sse-pending] trim FLOOR: 0 rows kept, {_sz(payload)}B — chip carries pending_count={payload['pending_count']}")
    except Exception as e:
        payload.setdefault("pending_count", None)
        payload.setdefault("boot_id", None)
        payload.setdefault("pending_session", [])
        log(f"[sse-pending] attach FAILED (push continues without rows): {e}")

def _sse_enrich_state(payload):
    """Inject full state snapshot into any outbound SSE payload.
    v2.24: Reads from _poll_snapshot instead of querying speakers."""
    payload["rooms_playing"] = list(_poll_snapshot.get("rooms_playing", []))
    payload["rooms_paused"] = list(_poll_snapshot.get("rooms_paused", []))
    payload["paused_at"] = dict(_poll_snapshot.get("paused_at", {}))  # v2.54 rider
    payload["rooms_all"] = list(_poll_snapshot.get("rooms_all", []))
    payload["groups"] = list(_poll_snapshot.get("groups", []))  # v2.53: full group topology
    payload["play_modes"] = dict(_poll_snapshot.get("play_modes", {}))
    payload["mute_states"] = dict(_poll_snapshot.get("mute_states", {}))
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

def _sse_schedule_recovery(delay_s):
    """Schedule a flush after backoff expires so queued data eventually sends."""
    global _sse_flush_timer
    if _sse_flush_timer:
        _sse_flush_timer.cancel()
    _sse_flush_timer = threading.Timer(delay_s, _sse_do_flush)
    _sse_flush_timer.daemon = True
    _sse_flush_timer.start()
    log(f"SSE recovery scheduled in {delay_s}s")

def _sse_do_flush():
    """Direct ntfy push -- re-enabled in v1.83.
    v1.75-v1.82: disabled due to IP rate-limiting. v1.80 fixed the root cause
    (stopped rooms polling at ~240 pushes/hr). Organic rate is ~10-15/hr,
    well within ntfy free tier (250/hr). Backoff logic retained as safety net."""
    global _sse_send_attempts, _sse_last_send_ts, _sse_consecutive_429, _sse_backoff_until
    _sse_send_attempts += 1
    if not ntfy_ui_topic:
        log(f"SSE flush: no ntfy_ui_topic configured, dropping")
        return
    # Backoff check — BEFORE draining so data stays in bundle
    if _sse_backoff_until and time.time() < _sse_backoff_until:
        remaining = int(_sse_backoff_until - time.time())
        # Only log every ~5 minutes to reduce noise
        if remaining % 300 < 20 or remaining < 30:
            log(f"SSE flush skipped: backoff ({remaining}s remaining)")
        return
    # Drain the bundle
    with _sse_bundle_lock:
        if not _sse_bundle:
            return
        payload = dict(_sse_bundle)
        _sse_bundle.clear()
    event_types = payload.pop("_event_types", [])
    # v2.48: quiet-hours idle gate — during Seattle quiet hours (22:00-07:00)
    # with nothing playing, drop pure status wiggles. Nobody is watching, and
    # every SSE message carries full state, so dropped bundles lose nothing.
    if not is_active_hours():
        _rp_q = list(_poll_snapshot.get("rooms_playing", []))
        _urgent = [et for et in event_types if et != "status_update"]
        if not _rp_q and not _urgent:
            log(f"SSE flush skipped: quiet hours + idle (dropped {event_types})")
            return
    # Enrich with full state snapshot
    try:
        _sse_enrich_state(payload)
    except Exception as e:
        log(f"SSE enrich failed: {e}")
        return
    payload["events"] = event_types
    payload["ts"] = time.time()
    # v2.56 D1/D8: every push carries the pending-session snapshot + freshness fields
    _attach_pending_session(payload)
    # POST as plain text body (JSON string) -- browser does JSON.parse(event.data)
    url = f"https://ntfy.sh/{ntfy_ui_topic}"
    body = json.dumps(payload)
    ntfy_headers = {"Authorization": f"Bearer {NTFY_TOKEN}"} if NTFY_TOKEN else {}
    try:
        r = requests.post(url, data=body.encode("utf-8"), headers=ntfy_headers, timeout=10)
        if r.status_code == 429:
            _sse_consecutive_429 += 1
            step = min(_sse_consecutive_429 - 1, len(SSE_BACKOFF_STEPS) - 1)
            backoff_s = SSE_BACKOFF_STEPS[step]
            _sse_backoff_until = time.time() + backoff_s
            log(f"SSE 429 (#{_sse_consecutive_429}): backing off {backoff_s}s")
            # Re-queue the payload so data isn't lost
            with _sse_bundle_lock:
                # Merge back — current bundle may have newer data, so
                # only restore keys not already present
                for k, v in payload.items():
                    if k not in _sse_bundle:
                        _sse_bundle[k] = v
                evts = _sse_bundle.setdefault("_event_types", [])
                for et in event_types:
                    if et not in evts:
                        evts.append(et)
            # Schedule a retry after backoff expires
            _sse_schedule_recovery(backoff_s + 2)
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
    # v2.56 D1/D8: relay path carries the same pending-session snapshot (the server
    # re-posts this to the browser ntfy topic — same 4096B attachment cliff applies)
    _attach_pending_session(payload)
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
    _hb = heartbeat_fields()
    _snap = _hb.pop("_delta_snap", None)  # v2.49: never ship the snapshot
    # v2.50: strip internal coalesce bookkeeping fields from the wire copy
    # (the persisted buffer file KEEPS them so a crash-restore can still coalesce).
    wire_items = [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]
    payload = {
        "type":      "sonos_history_batch",
        "format":    2,   # v2.50 room-factored items (rooms lists, roomless dedup keys)
        "flush_reason": reason or "unknown",
        "house":     house,
        "items":     wire_items,
        "heartbeat": _hb,
    }
    if sse_relay:
        payload["sse_relay"] = sse_relay
    try:
        r = requests.post(WEBHOOK, json=payload, timeout=20)
        _room_plays = sum(len(it.get("rooms") or [1]) for it in items)
        log(f"[OK] Flushed {len(items)} play(s) ({_room_plays} room-plays, format 2) [{reason}] -> HTTP {r.status_code}")
        if 200 <= r.status_code < 300:
            _delta_commit(_snap)  # v2.49: mark piggyback delivered
        last_post_ts = time.time()
        # v2.48: don't blind-unlink -- a track added during the POST window would
        # vanish from disk. Rewrite the file with whatever is currently buffered.
        try:
            with pending_buffer_lock:
                _leftover = list(pending_buffer)
            if _leftover:
                PENDING_PATH.write_text(json.dumps(_leftover), encoding="utf-8")
            else:
                PENDING_PATH.unlink(missing_ok=True)
        except: pass
    except Exception as e:
        log(f"[FAIL] Flush failed [{reason}]: {e} -- restoring {len(items)} item(s) to buffer")
        with pending_buffer_lock:
            pending_buffer[:0] = items  # prepend back

# --- DEBOUNCED STATE HEARTBEAT (v2.48) ---------------------------------------
# After ANY state-changing command (silent or not), schedule ONE heartbeat POST
# ~75s later. New commands in the burst reset the timer -> one agent run per
# interaction burst. Refreshes the agent-side mirror (client_status, now_playing)
# and delivers command_results promptly. Per-command acks stay rejected (v1.57).
_debounce_hb_timer = None
_debounce_hb_lock  = threading.Lock()
DEBOUNCE_HB_DELAY_S = 75

def _schedule_debounced_heartbeat(action):
    global _debounce_hb_timer
    with _debounce_hb_lock:
        if _debounce_hb_timer:
            _debounce_hb_timer.cancel()
        _debounce_hb_timer = threading.Timer(DEBOUNCE_HB_DELAY_S, _fire_debounced_heartbeat)
        _debounce_hb_timer.daemon = True
        _debounce_hb_timer.start()
    log(f"[debounce-hb] state heartbeat in {DEBOUNCE_HB_DELAY_S}s (after '{action}'; new commands reset timer)")

def _fire_debounced_heartbeat():
    global last_post_ts
    try:
        with pending_buffer_lock:
            _has_pending = len(pending_buffer) > 0
        if _has_pending:
            log("[debounce-hb] firing post-command state heartbeat (history pending)")
            flush_buffer(reason="debounce-post-command")
            last_post_ts = time.time()
            return
        # v2.49 PURPOSEFUL GATE: only POST if the agent has something to collect —
        # undelivered command results (e.g. silent commands never POST their own
        # result) or undelivered errors. Non-silent commands POST + commit their
        # result immediately, so this debounce firing 75s later would be a pure
        # duplicate agent run. Browser state needs no POST either way (direct SSE).
        # Gate self-expires by definition (Rule 11): any new result/error makes
        # counts non-zero and the next debounce fires normally.
        _pend_cmds, _pend_errs = delta_pending_counts()
        if _pend_cmds == 0 and _pend_errs == 0:
            log("[debounce-hb] skipped -- nothing undelivered (purposeful gate, v2.49); SSE carries state (benign no-op)")
            return
        # NOTE: "err-delta" wording avoids the "error" keyword -> error-ring false positive
        log(f"[debounce-hb] firing post-command state heartbeat ({_pend_cmds} result(s), {_pend_errs} err-delta undelivered)")
        _send_heartbeat()
        last_post_ts = time.time()
    except Exception as _e:
        log(f"[debounce-hb] FAILED: {_e}")

# --- HEARTBEAT THREAD -------------------------------------------------------
def heartbeat_thread():
    """60-min keepalive: fires only if no other POST has gone out in 60 min.
    During active sessions, every flush/command result carries heartbeat fields inline,
    so this thread mostly sleeps. Exists for staleness monitor to detect 'service alive'."""
    try:
        _heartbeat_thread_inner()
    except Exception as e:
        log(f"[FATAL] heartbeat_thread crashed: {e}")
        log(traceback.format_exc())
        try: post_error(f"heartbeat_thread crashed: {e}", context=traceback.format_exc()[:500], module="heartbeat")
        except: pass

def _heartbeat_thread_inner():
    global last_post_ts

    # v2.45: no startup send here -- the ready heartbeat (sent after Sonos
    # discovery, ~3s after boot) is the single startup heartbeat. Sending
    # here too made every boot cost 2-3 redundant webhook runs.
    last_post_ts = time.time()

    _quiet_entry_flushed = False
    while True:
        time.sleep(60)  # check every minute

        # v2.48.5: MAX-BUFFER-AGE FLUSH — if the OLDEST buffered track has been
        # sitting > BUFFER_MAX_AGE_SECS (30 min), flush regardless of ongoing
        # playback. Without this, a long continuous session (each new track
        # resetting the trailing timer) could hold plays client-side for hours,
        # letting the roaming Spotify backstop poll publish them first with a
        # wrong "Roaming" location label. Runs BEFORE the quiet-hours gate so
        # late-night listens also land within ~30 min (quiet loop = 30 min cadence).
        try:
            with pending_buffer_lock:
                _oldest_ts = pending_buffer[0].get("_buffered_at", 0) if pending_buffer else None
            if _oldest_ts and (time.time() - _oldest_ts) >= BUFFER_MAX_AGE_SECS:
                log(f"* Heartbeat: max-buffer-age flush (oldest item {int((time.time()-_oldest_ts)//60)} min old)")
                flush_buffer(reason="max-age")
                last_post_ts = time.time()
        except Exception as _ma_err:
            log(f"* Heartbeat: max-age flush check failed: {_ma_err}")

        if not is_active_hours():
            # v2.45: flush once on entry into quiet hours so evening listens
            # don't sit buffered until 7 AM (S-M4). Lands within ~1 min of
            # 22:00, inside the wellness-check grace window.
            if not _quiet_entry_flushed:
                _quiet_entry_flushed = True
                with pending_buffer_lock:
                    has_pending = len(pending_buffer) > 0
                if has_pending:
                    log("* Heartbeat: quiet-hours entry -- flushing pending buffer before pause")
                    try:
                        flush_buffer(reason="quiet-hours-entry")
                        last_post_ts = time.time()
                    except Exception as e:
                        log(f"* Heartbeat: quiet-hours entry flush failed: {e}")
                # v2.54 B3: nightly auth canary — piggybacks on the once-per-night
                # quiet-hours entry gate (~22:00 Seattle). One cheap SMAPI search
                # per service; expiry -> error ring badge (once per episode).
                try:
                    _run_auth_canary()
                except Exception as _can_err:
                    log(f"[canary] run failed: {_can_err}")
            log(f"* Heartbeat: quiet hours (Seattle {seattle_hour():02d}:xx) -- paused")
            time.sleep(HEARTBEAT_QUIET_SLEEP)
            continue
        _quiet_entry_flushed = False

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

# --- BUFFER MONITOR THREAD (REMOVED) -----------------------------------------
# buffer_monitor_thread was unwired in v1.83 (direct SSE replaced the relay need)
# but its dead body lingered here until v2.48.5 and misled debugging (it looked
# like a live 30s max-age / 30-min trailing-edge flusher). Its useful job — the
# max-age flush — now lives in heartbeat_thread above. Do not resurrect.

# --- VERSION CHECK THREAD ---------------------------------------------------
# [ROLLBACK-UNSAFE] Calls self_update_check() every 60 min. This is the periodic
# trigger path for self-update (vs. ntfy instant trigger below).
def version_check_thread():
    try:
        time.sleep(120)  # wait 2 min after start
        while True:
            self_update_check()
            time.sleep(VERSION_CHECK_INTERVAL)
    except Exception as e:
        log(f"[FATAL] version_check_thread crashed: {e}")
        log(traceback.format_exc())
        try: post_error(f"version_check_thread crashed: {e}", context=traceback.format_exc()[:500], module="version")
        except: pass

# --- BACKUP MODULE THREAD ---------------------------------------------------
def backup_thread():
    """Run lifelog_extract.py every hour; it handles cursor/hash dedup internally."""
    try:
        _backup_thread_inner()
    except Exception as e:
        log(f"[FATAL] backup_thread crashed: {e}")
        log(traceback.format_exc())
        try: post_error(f"backup_thread crashed: {e}", context=traceback.format_exc()[:500], module="backup")
        except: pass

def _backup_thread_inner():
    extract = INSTALL_DIR / "lifelog_extract.py"
    # v2.58 A5: the iPhone-backup pipeline is on hold (2026-07-28), so
    # lifelog_extract.py prints "ERROR: No iPhone backup found" EVERY hourly run
    # -- pure log noise that lands in the error ring. Demote to a once-per-boot
    # informational note (worded to dodge _ERROR_KEYWORDS). The extractor itself
    # (cursor/hash/mtime/config) is protected and deliberately untouched; we
    # only filter the echo on the service side.
    _no_backup_noted = {"done": False}

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
                if not line.strip():
                    continue
                if "No iPhone backup found" in line:
                    # v2.58 A5: hourly noise -> once-per-boot note (see above)
                    if not _no_backup_noted["done"]:
                        _no_backup_noted["done"] = True
                        log("  [extract] note: no iPhone backup present -- extractor idle (further hourly repeats suppressed this boot)")
                    continue
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
    try:
        _dev_loop_thread_inner()
    except Exception as e:
        log(f"[FATAL] dev_loop_thread crashed: {e}")
        log(traceback.format_exc())
        try: post_error(f"dev_loop_thread crashed: {e}", context=traceback.format_exc()[:500], module="dev")
        except: pass

def _dev_loop_thread_inner():
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

# --- v2.54 PLAYBACK-TRUST HELPERS --------------------------------------------
def _verified_queue_add(coord, add_fn, intended_pos, label="", num_tracks=1):
    """v2.54 B1: AddURIToQueue's DesiredFirstTrackNumberEnqueued is ADVISORY —
    Sonos sometimes ignores it and silently APPENDS to the end of a long/stale
    queue while the call succeeds (2026-07-29: 'Gravity's Angel' landed at the
    tail of a 16-item stale Bach queue; green toast, wrong slot). Never trust
    the request; verify the response.

    add_fn() performs the add and must RETURN the raw SoCo return value
    (int FirstTrackNumberEnqueued for ShareLinkPlugin.add_share_link_to_queue,
    soco.add_uri_to_queue, and soco.add_to_queue alike).

    Returns (actual_pos, placement):
      placement = "direct"    — landed where intended (or append requested)
                  "reordered" — misfiled, ReorderTracksInQueue recovered it
                  "degraded"  — misfiled AND reorder failed/impossible; actual_pos
                                tells the caller where the track really lives.
                                Callers MUST surface this (no green lies) and
                                play from actual_pos rather than intended_pos.
    intended_pos=None means append-to-end (no verification possible/needed).
    num_tracks: container adds (albums) enqueue N tracks; reorder must move all
    N. Pass 0/None for unknown count -> mismatch becomes degraded (no blind move).
    """
    actual = add_fn()
    # v2.58 Phase B: any successful service queue add freshens the stale-guard
    # stamp for this coordinator (add_fn raises on failure, so we only get here
    # when the mutation landed).
    try:
        _touch_queue(coord.player_name)
    except Exception as _tq_err:
        log(f"[stale-guard] touch after add did not succeed (benign no-op): {_tq_err}")
    try:
        actual = int(actual)
    except (TypeError, ValueError):
        log(f"[queue-verify]{label} add returned non-int ({actual!r}); placement unverifiable")
        return None, "direct"
    if intended_pos is None or actual == int(intended_pos):
        log(f"[queue-verify]{label} landed at {actual} (intended {'append' if intended_pos is None else intended_pos}) -> direct")
        return actual, "direct"
    log(f"[queue-verify]{label} MISFILED: intended {intended_pos}, landed {actual} -- attempting reorder")
    if not num_tracks:
        log(f"[queue-verify]{label} unknown track count for container; NOT reordering -> degraded")
        return actual, "degraded"
    # v2.58 A9: container adds expand ASYNCHRONOUSLY -- FirstTrackNumberEnqueued
    # can return before all N rows exist, and a reorder issued then moves a
    # PARTIAL span. Verify the expansion is complete (queue holds rows through
    # actual+num_tracks-1) before reordering; on timeout skip the reorder and
    # say so, rather than guessing.
    if int(num_tracks) > 1:
        _want_end = actual + int(num_tracks) - 1
        _exp_deadline = time.time() + 5.0
        _got_size = None
        while time.time() < _exp_deadline:
            try:
                _got_size = int(coord.queue_size)
            except Exception as _qs_err:
                log(f"[queue-verify]{label} expansion check read of queue_size did not succeed: {_qs_err}")
                _got_size = None
            if _got_size is not None and _got_size >= _want_end:
                break
            time.sleep(0.5)
        if _got_size is None or _got_size < _want_end:
            _have = max(0, (_got_size or 0) - actual + 1)
            log(f"[queue-op] reorder skipped: expansion incomplete ({_have}/{num_tracks})")
            return actual, "degraded"
    try:
        coord.avTransport.ReorderTracksInQueue([
            ("InstanceID", 0),
            ("StartingIndex", actual),
            ("NumberOfTracks", int(num_tracks)),
            ("InsertBefore", int(intended_pos)),
            ("UpdateID", 0),
        ])
        log(f"[queue-verify]{label} reordered {actual} -> {intended_pos} ({num_tracks} track(s))")
        return int(intended_pos), "reordered"
    except Exception as re_err:
        log(f"[queue-verify]{label} reorder FAILED ({re_err}); track(s) remain at {actual} -> degraded")
        return actual, "degraded"


def _queue_op_log(verb, target, coordinator, transport_state="", queue_before=None,
                  queue_after=None, pos_requested=None, pos_landed=None):
    """v2.58 A7 (coordinator transparency, awareness half): one consistent
    [queue-op] log line on every queue-affecting verb, and the same fields
    returned as a dict for the command-result payload. When target != acting
    coordinator (slaved room), the result SAYS so -- any future UX warning is a
    pure rendering decision over these fields (Rule 27: messages carry state).
    NEVER raises -- transparency must not break the verb."""
    try:
        cname = getattr(coordinator, "player_name", "?")
        members = [cname]
        try:
            if coordinator.group:
                members = sorted(set(m.player_name for m in coordinator.group.members
                                     if getattr(m, "is_visible", True)))
        except Exception as _gm_err:
            log(f"[queue-op] group member read did not succeed (using coordinator only): {_gm_err}")
        if not transport_state:
            try:
                transport_state = coordinator.get_current_transport_info().get("current_transport_state", "?")
            except Exception:
                transport_state = "?"
        log(f"[queue-op] verb={verb} target={target} coordinator={cname} group={members} "
            f"transport={transport_state} queue_before={queue_before} queue_after={queue_after} "
            f"pos_requested={pos_requested} pos_landed={pos_landed}")
        return {"coordinator": cname, "group_members": members,
                "transport_state": transport_state, "queue_before": queue_before,
                "queue_after": queue_after, "pos_requested": pos_requested,
                "pos_landed": pos_landed}
    except Exception as _qol_err:
        log(f"[queue-op] transparency logging did not succeed (benign no-op): {_qol_err}")
        return {}


def _load_then_trim(coordinator, add_fn, label="", num_tracks=1):
    """v2.58: shared LOAD-THEN-TRIM queue replace -- the exact pattern proven in
    v2.57 replace_queue (append new content at the END first, so an add failure
    leaves the old queue untouched; then remove the old rows; then play the new
    content). Used by the Phase B stale-queue conversion paths so they reuse the
    proven internals instead of reimplementing.
    Returns (first_new_pos, placement, trim_failed). Raises if the add fails
    (old queue untouched in that case -- caller's outer except reports it)."""
    old_len = coordinator.queue_size
    with _queue_mutation_timeout():
        pos, placement = _verified_queue_add(coordinator, add_fn, None,
                                             label=label, num_tracks=num_tracks)
    first_new = pos or (old_len + 1)
    log(f"[queue-op]{label} load-then-trim: appended at {first_new} (old queue {old_len} rows), trimming old rows")
    trim_failed = False
    if old_len > 0:
        try:
            coordinator.avTransport.RemoveTrackRangeFromQueue([
                ("InstanceID", 0), ("UpdateID", 0),
                ("StartingIndex", 1), ("NumberOfTracks", old_len)])
        except Exception as _tr_err:
            # E12 pattern: add landed, trim failed -- play the new content from
            # where it actually lives; stale rows linger above and the next
            # replace/clear sweeps them. Honest WARN, never a green lie.
            trim_failed = True
            log(f"[queue-op]{label} WARNING -- trim of {old_len} old rows failed ({_tr_err}); playing new content at {first_new}")
    coordinator.play_from_queue(0 if not trim_failed else first_new - 1)
    return first_new, placement, trim_failed


# v2.54 B2: Spotify Web API client-credentials app token.
# Used ONLY for catalog resolution (album/track name -> spotify:...:ID). It has
# no user scope, never touches user data, and self-refreshes — so it cannot
# expire the way Mind's per-device SoCo search token did on 2026-07-29 (three
# independent Spotify credentials exist: household Sonos link, browser PKCE
# token, per-device SMAPI search token — this is a FOURTH, and the most stable).
SPOTIFY_CC_ID     = "af7ca85939e74a2a961a6a44d199af4e"
SPOTIFY_CC_SECRET = "1f464a2283404a209752501b31af715e"
_spotify_cc_token = {"token": None, "expires": 0.0}

def _spotify_app_token():
    """Return a cached client-credentials bearer token, refreshing if <60s left."""
    now_t = time.time()
    if _spotify_cc_token["token"] and now_t < _spotify_cc_token["expires"] - 60:
        return _spotify_cc_token["token"]
    resp = requests.post("https://accounts.spotify.com/api/token",
                         data={"grant_type": "client_credentials"},
                         auth=(SPOTIFY_CC_ID, SPOTIFY_CC_SECRET), timeout=10)
    resp.raise_for_status()
    d = resp.json()
    _spotify_cc_token["token"] = d["access_token"]
    _spotify_cc_token["expires"] = now_t + int(d.get("expires_in", 3600))
    log("[spotify-cc] app token refreshed")
    return _spotify_cc_token["token"]


def _spotify_pick_album(items, want_title):
    """v2.54 B2: choose the best album from a Spotify Web API search result.
    Prefer full albums (total_tracks>1); among those prefer exact normalized
    title match, then substring, then API relevance order (mirrors the v2.47.1
    iTunes matcher — max-trackCount wrongly picked 'Decade' over 'Harvest')."""
    def _norm(s):
        return "".join(ch for ch in str(s).casefold() if ch.isalnum() or ch == " ").strip()
    full = [a for a in items if a.get("total_tracks", 0) > 1] or items
    want = _norm(want_title)
    exact = [a for a in full if _norm(a.get("name", "")) == want]
    sub   = [a for a in full if want and want in _norm(a.get("name", ""))]
    chosen = (exact or sub or full)[0]
    kind = "exact" if exact else ("substring" if sub else "first-full")
    log(f"play_album: Spotify Web-API title match={kind} for '{want_title}'")
    return chosen


# --- v2.59 G8: CASE-INSENSITIVE MUSIC-SERVICE NAME RESOLUTION ------------------
# SoCo's MusicService(name) lookup is case-SENSITIVE against the subscribed
# service list — a caller's 'qobuz' matches nothing and fails with a confusing
# error (G8 gate finding, design_v258_release_plan.md T2). Resolve every
# caller-supplied service name to its canonical casing BEFORE use; WARN when
# normalization was needed so sloppy callers are visible in the logs.
_MUSIC_SERVICE_CANONICAL = {"qobuz": "Qobuz", "spotify": "Spotify",
                            "apple music": "Apple Music", "applemusic": "Apple Music",
                            "tunein": "TuneIn", "sonos radio": "Sonos Radio"}

def _resolve_music_service_name(name):
    """v2.59 G8: return the canonically-cased music service name for `name`.
    Order: exact match against the device's live service list wins; else
    case-insensitive match against that list; else the static canonical map;
    else return the input unchanged (fail-open — MusicService() then raises its
    own descriptive error, which is more useful than us guessing)."""
    if not name or not isinstance(name, str):
        return name
    raw = name.strip()
    # Fast path: already-canonical names skip the live SoCo service-list call
    # (one SMAPI/registry read per command otherwise — needless on every send).
    if raw in _MUSIC_SERVICE_CANONICAL.values():
        return raw
    try:
        available = []
        try:
            from soco.music_services import MusicService
            available = list(MusicService.get_all_music_services_names())
        except Exception as _ms_err:
            log(f"[svc-name] live service list unavailable ({_ms_err}); using static map")
        if raw in available:
            return raw
        low = raw.lower()
        for _cand in available:
            if _cand.lower() == low:
                log(f"[svc-name] WARN: normalized service name '{raw}' -> '{_cand}' (case-insensitive live match)")
                return _cand
        _canon = _MUSIC_SERVICE_CANONICAL.get(low)
        if _canon and _canon != raw:
            log(f"[svc-name] WARN: normalized service name '{raw}' -> '{_canon}' (static map)")
            return _canon
    except Exception as _rs_err:
        log(f"[svc-name] resolution failed for '{raw}' (fail-open): {_rs_err}")
    return raw

# v2.54 B3: nightly auth canary — one cheap SMAPI search per music service.
# Catches per-device service-token expiry while idle so expired credentials
# surface as an error-ring badge instead of a mystery toast days later.
# Alert once per expiry episode; reset on first success (Rule 11). State is
# in-memory: a mid-episode restart re-alerts once, which is acceptable.
# Apple Music is NOT canaried — its SMAPI rejects soco search() entirely
# (SOAP-ENV:Server faults, see play_album v2.47.1 notes), so a failure there
# tells us nothing about auth.
_canary_expired = {}

def _run_auth_canary():
    if not sonos_commander:
        return
    from soco.music_services import MusicService
    for svc in ("Spotify", "Qobuz"):
        try:
            list(MusicService(svc).search("albums", "canary", 0, 1))
            if _canary_expired.get(svc):
                log(f"[canary] {svc} search token RECOVERED — clearing expiry episode")
            else:
                log(f"[canary] {svc} search token OK")
            _canary_expired[svc] = False
        except Exception as e:
            msg = str(e)
            if "auth" in msg.lower() or "token" in msg.lower():
                if not _canary_expired.get(svc):
                    _canary_expired[svc] = True
                    post_error(f"Auth canary: {svc} search token expired on {computer}: {msg[:150]}",
                               context=f"service={svc}", module="canary")
                else:
                    log(f"[canary] {svc} still expired (already alerted this episode)")
            else:
                log(f"[canary] {svc} search failed (non-auth, not alerting): {msg[:120]}")


# --- SONOS: DISCOVERY -------------------------------------------------------
def get_coordinators():
    """Single discovery + device map build (v2.24).
    One soco.discover(timeout=3) per cycle. Builds both:
    - coordinators list (returned)
    - current_devices_by_name (updated as side effect)
    Replaces the old pattern of discover(8) + separate discover(5) for device map."""
    global current_devices_by_name
    try:
        import soco
        devices = soco.discover(timeout=3)
        if not devices: return []
        coordinators = {}
        all_devices = {}
        now_t = time.time()
        for dev in devices:
            ip = dev.ip_address
            # Skip IPs that timed out recently (avoid hang per offline speaker)
            if ip in _offline_ips:
                if now_t - _offline_ips[ip] < OFFLINE_RECHECK_SECS:
                    continue
                else:
                    del _offline_ips[ip]
                    log(f"Retrying previously offline speaker at {ip}")
            try:
                # Build device map (replaces second soco.discover)
                all_devices[dev.player_name] = dev
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
                        all_devices[dev.player_name] = dev
                        coordinators[dev.player_name] = dev
                    except Exception:
                        pass
        # Atomic swap of device map
        current_devices_by_name = all_devices
        return list(coordinators.values())
    except Exception as e:
        log(f"Discovery error: {e}")
        return []


def _jit_discover(room_name):
    """JIT discovery fallback for commands targeting unknown rooms (v2.24).
    If a command targets a room not in current_devices_by_name, do a quick
    one-shot discover to find it. Returns the device or None."""
    global current_devices_by_name
    try:
        import soco
        log(f"[JIT] Room '{room_name}' not in cache -- running one-shot discovery")
        devices = soco.discover(timeout=3)
        if not devices:
            log(f"[JIT] Discovery found no devices")
            return None
        found = None
        for dev in devices:
            try:
                name = dev.player_name
                current_devices_by_name[name] = dev
                if name == room_name:
                    found = dev
                    # Clear offline status if it was quarantined
                    ip = dev.ip_address
                    if ip in _offline_ips:
                        del _offline_ips[ip]
                    if name in speaker_offline_since:
                        del speaker_offline_since[name]
                        speaker_failures[name] = 0
            except Exception:
                pass
        if found:
            log(f"[JIT] Found '{room_name}' via one-shot discovery")
        else:
            log(f"[JIT] Room '{room_name}' not found even after discovery")
        return found
    except Exception as e:
        log(f"[JIT] Discovery error: {e}")
        return None

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
    except Exception as _cc_err:
        # v2.44: was a silent `return None` -- it hid a NameError (missing import re)
        # for months, nulling container context on every track. Never swallow silently.
        log(f"[container] get_container_context failed: {type(_cc_err).__name__}: {_cc_err}")
        return None


# --- v2.59 C3: CAPTURE-SIDE CONTAINER SANITIZE (L1/L2/L3) ----------------------
# Sanitize ONCE, at the source (get_track_info, right after get_container_context)
# so every downstream consumer — history buffer, SSE pending rows, state ring
# (F9), room_state — receives the same cleaned container. Design:
# review_impl_v1.md §C3(c). Every layer FAILS OPEN: when it cannot judge
# (missing DIDL album — Qobuz; no marker; no cu) it keeps the container.
# Suppression nulls the whole ctx (including spotify_context — S2) and stamps
# archaeology fields so the first week of v2.59 is auditable via one SQL query
# over raw_metadata instead of a shadow-mode release.

def _norm_ctx_str(s):
    """Normalize a name for album/container comparison (mirrors the sessionizer
    read-guard's normStr semantics: lowercase, alnum runs only)."""
    return re.sub(r'[^a-z0-9]+', ' ', (s or '').lower()).strip()

def _ctx_names_match(container_name, track_album):
    """v2.61 FUZZY ctx-name match (Rule 10 class fix, 2026-08-08 Bettye LaVette
    incident): senders decorate display titles ('🎛️ Album (2005) — Artist') and
    that decorated string flows into queue provenance / stale-Enqueued markers /
    captured DIDL. A decorated-but-CORRECT name must never read as a MISMATCH
    in a staleness guard (the old strict equality skipped the overlay AND
    self-heal-cleared legit provenance -> organic album plays lost context).
    Match when: equal after normalization, OR either normalized name contains
    the other (>=4 chars on the contained side, so year fragments like '1989'
    inside '(1989)' decoration can't false-match short album titles).
    Missing either name -> True (fail open: absence is not proof of staleness;
    every call site separately requires both names present before suppressing).
    Used by: _overlay_prov_guard, sanitize_container L1 + L2."""
    a = _norm_ctx_str(container_name)
    b = _norm_ctx_str(track_album)
    if not a or not b:
        return True
    if a == b:
        return True
    if len(b) >= 4 and b in a:
        return True
    if len(a) >= 4 and a in b:
        return True
    return False

def _spotify_track_id(u):
    """Extract a spotify track id from either native (spotify:track:ID) or
    sonos-encoded (x-sonos-spotify:spotify%3atrack%3aID?...) URI forms."""
    if not u: return ""
    ul = u.replace("%3a", ":").replace("%3A", ":").lower()
    m = re.search(r'spotify:track:([a-z0-9]+)', ul)
    return m.group(1) if m else ""

def _same_track_uri(u1, u2):
    """True when two URIs name the same track (exact match, or same spotify id
    across native/sonos encodings)."""
    if not u1 or not u2: return False
    if u1 == u2: return True
    t1, t2 = _spotify_track_id(u1), _spotify_track_id(u2)
    return bool(t1) and t1 == t2

def _is_album_container(cu, ctype):
    """Album-typed container: upnp class mentions album, or the URI carries a
    spotify/encoded album id (x-rincon-cpcontainer:...album%3a... / spotify:album:)."""
    cul = (cu or "").lower()
    return ("album" in (ctype or "").lower()) or ("album%3a" in cul) or ("album:" in cul)

def _is_station_container(cu, ctype):
    """Station/broadcast container, or a container URI that is itself a stream
    URI (self-describing) — L3 must never strip these (T-C3.5 radio)."""
    ctl = (ctype or "").lower()
    if "audiobroadcast" in ctl or "radio" in ctl:
        return True
    cul = (cu or "").lower()
    return any(cul.startswith(p) for p in STREAM_URI_PREFIXES)

def _suppression_fields(ctx, reason):
    """Archaeology fields stamped onto track_info (and thus buffer rows /
    raw_metadata) whenever a layer suppresses a container."""
    return {"suppressed_container_name": (ctx or {}).get("container_name", ""),
            "suppressed_container_uri":  (ctx or {}).get("container_uri", ""),
            "suppressed_container_type": (ctx or {}).get("container_type", ""),
            "context_suppressed_reason": reason}

def sanitize_container(ctx, track_uri, track_album, coord_name):
    """v2.59 C3: decide whether the captured container context is STALE and must
    be suppressed. Returns (ctx_or_None, extra_track_fields).
    L1 — insert-marker: at insert-play time the command path recorded the exact
         Enqueued URI it knew had just gone stale (see _set_stale_enqueued).
         Album kind fires only while the observed track album MATCHES the
         inserted album — when the album ends and the old queue resumes, the
         old container is again correct and L1 releases (T-C3.4). Track kind
         (Q6) fires on the injected track itself and blocks the provenance
         overlay too (honest no-context).
    L2 — album-typed container whose name mismatches the track's own DIDL album
         (capture-side mirror of the shipped sessionizer read guard).
    L3 — stream playback (STREAM_URI_PREFIXES incl. x-sonos-vli) carrying a
         non-station container that isn't the stream itself: the container
         describes the WRONG thing (a leftover queue).
    Level-triggered (Rule 27): the marker is compared against observed state on
    EVERY poll, never consumed as a one-shot event. NEVER raises — any internal
    error fails open and keeps the container."""
    fields = {}
    try:
        cu    = (ctx or {}).get("container_uri", "") or ""
        cname = (ctx or {}).get("container_name", "") or ""
        ctype = (ctx or {}).get("container_type", "") or ""
        m = _get_stale_enqueued(coord_name)
        # Marker lifecycle: a genuinely NEW non-queue Enqueued URI means a real
        # load happened (ours or native-app) — the marker's staleness claim no
        # longer describes reality, so release it (Rule 11).
        if (m and cu and not cu.startswith("x-rincon-queue")
                and cu != m.get("uri", "") and not _same_track_uri(cu, m.get("uri", ""))):
            _clear_stale_enqueued(coord_name, f"new Enqueued observed ({cu[:60]})")
            m = None
        # L1 (Q6 kind) — injected one-off track: suppress with NO overlay
        # fallback. context_source='inserted_track' is the overlay-blocking flag
        # (post_history and _retire_to_state_ring both honor it). This check
        # runs even when ctx is None so the flag still blocks the overlay.
        if (m and m.get("kind") == "inserted_track"
                and _same_track_uri(track_uri, m.get("expected_uri", ""))):
            fields["context_source"] = "inserted_track"
            if ctx:
                fields.update(_suppression_fields(ctx, "inserted_track"))
                log(f"[sanitize] {coord_name}: SUPPRESSED container '{cname}' ({ctype}) "
                    f"reason=inserted_track (injected one-off, honest no-context)")
                return None, fields
            log(f"[sanitize] {coord_name}: inserted_track flag set (no container present) — overlay blocked")
            return ctx, fields
        if not ctx:
            return ctx, fields
        # L1 (album kind) — the exact stale URI we predicted, while the track's
        # album matches the album we insert-played: suppress; the v2.55
        # provenance overlay then stamps the CORRECT context (the album).
        if (m and m.get("kind") == "insert_album" and cu and cu == m.get("uri", "")
                and track_album and m.get("expected_album")
                # v2.61: fuzzy match — a decorated expected_album must still
                # fire the suppression while the inserted album plays (Rule 10).
                and _ctx_names_match(m.get("expected_album", ""), track_album)):
            fields.update(_suppression_fields(ctx, "stale_enqueued_after_insert"))
            log(f"[sanitize] {coord_name}: SUPPRESSED container '{cname}' ({ctype}) "
                f"reason=stale_enqueued_after_insert (insert-play marker, track_album='{track_album}')")
            return None, fields
        # L2 — album-typed container, name mismatch vs the track's own DIDL
        # album. Fails open when either name is missing (Qobuz empty DIDL).
        if (_is_album_container(cu, ctype) and track_album and cname
                # v2.61: fuzzy match (was strict) — decorated container names
                # must not suppress their own legit context (Rule 10).
                and not _ctx_names_match(cname, track_album)):
            fields.update(_suppression_fields(ctx, "album_name_mismatch"))
            log(f"[sanitize] {coord_name}: SUPPRESSED container '{cname}' ({ctype}) "
                f"reason=album_name_mismatch (track_album='{track_album}')")
            return None, fields
        # L3 — stream playback carrying a non-station container that isn't the
        # stream itself (e.g. leftover queue container during Spotify Connect /
        # AirPlay / line-in). Station containers and self-describing URIs pass.
        if (track_uri and any(track_uri.lower().startswith(p) for p in STREAM_URI_PREFIXES)
                and not _is_station_container(cu, ctype) and cu != track_uri):
            fields.update(_suppression_fields(ctx, "stream_playback_queue_container"))
            log(f"[sanitize] {coord_name}: SUPPRESSED container '{cname}' ({ctype}) "
                f"reason=stream_playback_queue_container (stream uri='{track_uri[:60]}')")
            return None, fields
        return ctx, fields
    except Exception as _san_err:
        # Fail open: a sanitize bug must never strip legitimate context.
        log(f"[sanitize] ERROR (fail-open, keeping container): {type(_san_err).__name__}: {_san_err}")
        return ctx, fields


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
        # v2.24: Use snapshot transport state (already queried by _build_poll_snapshot)
        # to avoid race condition where snapshot and get_track_info disagree on state.
        cached_state = _last_transport_states.get(name)
        if cached_state:
            state = cached_state
        else:
            state = device.get_current_transport_info().get("current_transport_state", "STOPPED")
            log(f"[diag] get_track_info: cache miss for '{name}', live query returned '{state}'")
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
        # v2.37: Enhanced DIDL metadata extraction with raw AVTransport fallback
        artist_raw = info.get("artist", "").strip()
        album_raw  = info.get("album", "").strip()
        import re as _re
        from html import unescape as _html_unescape
        # v2.39: Capture album identifiers from DIDL for album-level replay
        didl_parent_id = ""
        didl_album_art_uri = ""

        # --- Phase 1: Try SoCo's metadata field (TrackMetaData from GetPositionInfo) ---
        if metadata and (not artist_raw or not album_raw or not title):
            try:
                if not title or not artist_raw:
                    log(f"[DIDL-debug] SoCo metadata for {name} ({len(metadata)}b): {metadata[:500]}")
                # Try with and without HTML entity decoding
                for _src in [metadata, _html_unescape(metadata)]:
                    if not title:
                        _dc = _re.search(r'<dc:title>([^<]+)</dc:title>', _src)
                        if _dc:
                            title = _dc.group(1).strip()
                            log(f"[DIDL-fallback] Recovered title from DIDL XML: '{title}' for {name}")
                    if not artist_raw:
                        _cr = _re.search(r'<dc:creator>([^<]+)</dc:creator>', _src)
                        if _cr:
                            artist_raw = _cr.group(1).strip()
                            log(f"[DIDL-fallback] Recovered artist from DIDL XML: '{artist_raw}' for {name}")
                    if not album_raw:
                        _al = _re.search(r'<upnp:album>([^<]+)</upnp:album>', _src)
                        if _al:
                            album_raw = _al.group(1).strip()
                    # v2.39: Extract parentID (album container) and albumArtURI
                    if not didl_parent_id:
                        _pid = _re.search(r'parentID="([^"]*)"', _src)
                        if _pid:
                            didl_parent_id = _pid.group(1).strip()
                    if not didl_album_art_uri:
                        _aau = _re.search(r'<upnp:albumArtURI>([^<]+)</upnp:albumArtURI>', _src)
                        if _aau:
                            didl_album_art_uri = _aau.group(1).strip()
                    if title and artist_raw:
                        break  # Got what we need
            except Exception as _e:
                log(f"[DIDL-fallback] Parse error: {_e}")

        # --- Phase 2: If still missing, try raw AVTransport GetPositionInfo ---
        if not title or not artist_raw:
            try:
                _raw = device.avTransport.GetPositionInfo(InstanceID=0)
                _raw_meta = _raw.get("TrackMetaData", "")
                _enq_meta = _raw.get("EnqueuedTransportURIMetaData", "")
                # Log raw fields for diagnostics
                log(f"[DIDL-raw] GetPositionInfo for {name}:")
                log(f"[DIDL-raw]   TrackURI: {_raw.get('TrackURI', '')[:120]}")
                log(f"[DIDL-raw]   TrackMetaData type={type(_raw_meta).__name__} len={len(str(_raw_meta))}")
                if _raw_meta and isinstance(_raw_meta, str) and len(_raw_meta) > 10:
                    log(f"[DIDL-raw]   TrackMetaData: {str(_raw_meta)[:500]}")
                elif not isinstance(_raw_meta, str) and hasattr(_raw_meta, 'title'):
                    # SoCo may parse it into a DidlObject (but not a plain str — str.title is a method!)
                    log(f"[DIDL-raw]   TrackMetaData is DidlObject: title='{getattr(_raw_meta, 'title', '')}' creator='{getattr(_raw_meta, 'creator', '')}'")
                    if not title and getattr(_raw_meta, 'title', ''):
                        _t = _raw_meta.title
                        if isinstance(_t, str):
                            title = _t
                        log(f"[DIDL-raw] Recovered title from DidlObject: '{title}'")
                    if not artist_raw and getattr(_raw_meta, 'creator', ''):
                        artist_raw = _raw_meta.creator
                        log(f"[DIDL-raw] Recovered artist from DidlObject: '{artist_raw}'")
                    if not album_raw and getattr(_raw_meta, 'album', ''):
                        album_raw = getattr(_raw_meta, 'album', '')
                    # v2.39: Extract album identifiers from DidlObject
                    if not didl_parent_id and getattr(_raw_meta, 'parent_id', ''):
                        didl_parent_id = str(getattr(_raw_meta, 'parent_id', ''))
                    if not didl_album_art_uri and getattr(_raw_meta, 'album_art_uri', ''):
                        didl_album_art_uri = str(getattr(_raw_meta, 'album_art_uri', ''))
                else:
                    log(f"[DIDL-raw]   TrackMetaData: {repr(str(_raw_meta))[:200]}")
                    # v2.39: Try regex on raw string for parentID/albumArtURI
                    if not didl_parent_id:
                        _pid = _re.search(r'parentID="([^"]*)"', str(_raw_meta))
                        if _pid:
                            didl_parent_id = _pid.group(1).strip()
                    if not didl_album_art_uri:
                        _aau = _re.search(r'<upnp:albumArtURI>([^<]+)</upnp:albumArtURI>', str(_raw_meta))
                        if _aau:
                            didl_album_art_uri = _aau.group(1).strip()
                # Also try EnqueuedTransportURIMetaData
                if (_enq_meta and isinstance(_enq_meta, str) and len(_enq_meta) > 10
                        and (not title or not artist_raw)):
                    log(f"[DIDL-raw]   EnqueuedMeta: {str(_enq_meta)[:500]}")
                    for _src in [_enq_meta, _html_unescape(_enq_meta)]:
                        if not title:
                            _dc = _re.search(r'<dc:title>([^<]+)</dc:title>', _src)
                            if _dc:
                                title = _dc.group(1).strip()
                                log(f"[DIDL-raw] Recovered title from EnqueuedMeta: '{title}'")
                        if not artist_raw:
                            _cr = _re.search(r'<dc:creator>([^<]+)</dc:creator>', _src)
                            if _cr:
                                artist_raw = _cr.group(1).strip()
                                log(f"[DIDL-raw] Recovered artist from EnqueuedMeta: '{artist_raw}'")
                elif not isinstance(_enq_meta, str) and hasattr(_enq_meta, 'title'):
                    log(f"[DIDL-raw]   EnqueuedMeta is DidlObject: title='{getattr(_enq_meta, 'title', '')}'")
                    if not title and getattr(_enq_meta, 'title', ''):
                        _t = _enq_meta.title
                        if isinstance(_t, str):
                            title = _t
            except Exception as _e:
                log(f"[DIDL-raw] GetPositionInfo fallback error: {_e}")

        # v2.39: Extract album identifiers unconditionally from metadata string
        # (Phase 1/2 are gated on missing title/artist, but we need parentID/albumArtURI
        #  even when SoCo already gives us title/artist/album)
        if metadata and not didl_parent_id:
            try:
                _pid = _re.search(r'parentID="([^"]*)"', metadata)
                if _pid:
                    didl_parent_id = _pid.group(1).strip()
                if not didl_parent_id:
                    _pid = _re.search(r'parentID="([^"]*)"', _html_unescape(metadata))
                    if _pid:
                        didl_parent_id = _pid.group(1).strip()
            except Exception as _e:
                log(f"[DIDL-album] parentID parse error: {_e}")
        if metadata and not didl_album_art_uri:
            try:
                _aau = _re.search(r'<upnp:albumArtURI>([^<]+)</upnp:albumArtURI>', metadata)
                if _aau:
                    didl_album_art_uri = _aau.group(1).strip()
                if not didl_album_art_uri:
                    _aau = _re.search(r'<upnp:albumArtURI>([^<]+)</upnp:albumArtURI>', _html_unescape(metadata))
                    if _aau:
                        didl_album_art_uri = _aau.group(1).strip()
            except Exception as _e:
                log(f"[DIDL-album] albumArtURI parse error: {_e}")
        # Also try the raw DidlObject attributes if metadata is a parsed object
        if metadata and (not didl_parent_id or not didl_album_art_uri):
            try:
                if not isinstance(metadata, str) and not didl_parent_id and getattr(metadata, 'parent_id', ''):
                    didl_parent_id = str(getattr(metadata, 'parent_id', ''))
                if not isinstance(metadata, str) and not didl_album_art_uri and getattr(metadata, 'album_art_uri', ''):
                    didl_album_art_uri = str(getattr(metadata, 'album_art_uri', ''))
            except Exception:
                pass

        # v2.41: DIDL fields (parentID, albumArtURI) flow silently into ring buffer.
        # Per-poll logging removed — parentID is -1 for all services (Spotify/Qobuz/Apple Music).

        # --- Phase 3 (v2.37): URI metadata cache from play_next commands ---
        # Qobuz/Apple Music DIDL from Sonos is often empty. If we played the track
        # via play_next, we cached the metadata then. Look it up now.
        _URI_CACHE_TTL = 4 * 3600  # 4 hours
        if uri and (not title or not artist_raw) and uri in _uri_metadata_cache:
            _cached = _uri_metadata_cache[uri]
            if time.time() - _cached["ts"] < _URI_CACHE_TTL:
                if not title and _cached["title"]:
                    title = _cached["title"]
                    log(f"[DIDL-cache] Recovered title from play_next cache: '{title}' for {name}")
                if not artist_raw and _cached["artist"]:
                    artist_raw = _cached["artist"]
                    log(f"[DIDL-cache] Recovered artist from play_next cache: '{artist_raw}' for {name}")
                if not album_raw and _cached["album"]:
                    album_raw = _cached["album"]
                    log(f"[DIDL-cache] Recovered album from play_next cache: '{album_raw}' for {name}")
            else:
                del _uri_metadata_cache[uri]  # expired

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
                # v2.35: Last resort — use service name so Now Playing still fires
                _svc = detect_service(uri, metadata)
                if _svc and _svc not in ("unknown",):
                    _svc_label = _svc.replace("sonos_", "").replace("_", " ").title()
                    title = f"{_svc_label} Track"
                    log(f"[DIDL-fallback] Using synthetic title '{title}' for {uri[:80]} on {name}")
                    log(f"[DIDL-fallback] Raw info keys: title='{info.get('title','')}' artist='{info.get('artist','')}' uri='{uri[:80]}' meta_len={len(metadata)}")
                else:
                    log(f"[now-playing] Dropping empty-title track on {name}: uri={uri[:80]} meta_len={len(metadata)}")
                    speaker_failures[name] = 0
                    return None
        dur_str  = info.get("duration", "0:00:00")
        dur_secs = 0
        try:
            p = dur_str.split(":")
            if len(p) == 3:
                dur_secs = int(p[0])*3600 + int(p[1])*60 + int(p[2])
        except Exception: pass
        try:    members = list(dict.fromkeys(m.player_name for m in device.group.members if getattr(m, "is_visible", True)))  # v2.36: deduplicate (SoCo sometimes returns coordinator twice); v2.54: skip invisible bonded units
        except: members = [device.player_name]
        speaker_failures[name] = 0
        # v2.38: Safety net — ensure title/artist/album are plain strings (never method refs)
        for _field_name, _field_val in [("title", title), ("artist", artist_raw), ("album", album_raw)]:
            if _field_val and not isinstance(_field_val, str):
                log(f"[DIDL-safety] Non-string {_field_name} detected: {type(_field_val).__name__} = {repr(_field_val)[:100]} -- clearing")
                if _field_name == "title": title = None
                elif _field_name == "artist": artist_raw = None
                elif _field_name == "album": album_raw = None
        if not title:
            _svc = detect_service(uri, metadata)
            if _svc and _svc not in ("unknown",):
                _svc_label = _svc.replace("sonos_", "").replace("_", " ").title()
                title = f"{_svc_label} Track"
                log(f"[DIDL-safety] Used synthetic title '{title}' after clearing non-string")
            else:
                log(f"[DIDL-safety] No valid title after clearing non-string on {name}")
                return None
        ctx = get_container_context(device)
        _ctx_pre = dict(ctx) if isinstance(ctx, dict) else None
        # v2.59 C3: sanitize ONCE at the source — history buffer, SSE rows,
        # state ring (F9) and room_state all receive the same cleaned container.
        # _san_fields carries suppression archaeology (+ Q6 inserted_track flag)
        # onto track_info so buffer rows land them in raw_metadata.
        ctx, _san_fields = sanitize_container(ctx, uri, album_raw, device.player_name)
        # v2.62 DIAG [ctx-diag] (field study, 2026-08-15): one line per track
        # change showing what the speaker REPORTED (EnqueuedTransportURI harvest,
        # pre-sanitize) vs what SURVIVED sanitize — measures how often native
        # Sonos-app / Spotify-cast plays name their container, before we build
        # insert-attribution on that signal. Never raises; remove after study.
        try:
            def _cd(c):
                if not c: return "none"
                cu = c.get("container_uri", "") or ""
                kind = cu.split(":", 1)[0] if cu else "-"
                nm = (c.get("container_name", "") or "")[:60]
                sp = c.get("spotify_context", "") or "-"
                return f"kind={kind} name='{nm}' spotify={sp}"
            _sup = ",".join(sorted(_san_fields.keys())) if _san_fields else "-"
            log(f"[ctx-diag] {device.player_name}: pre[{_cd(_ctx_pre)}] post[{_cd(ctx)}] san_fields={_sup}")
        except Exception as _cd_err:
            log(f"[ctx-diag] diag line failed (non-fatal): {_cd_err}")
        # v2.62 INSERT-RANGE ATTRIBUTION: sanitize left this row honest-blank
        # (typical for our own play_next/add_to_queue container inserts — the
        # speaker still reports the PREVIOUS load's Enqueued container, which
        # the stale-markers correctly suppress). If the row's queue position
        # falls inside exactly one verified insert range, we KNOW which
        # container put it there — receipt-based, not inferred. Stamp it.
        # Never overrides a real container that survived sanitize.
        try:
            # v2.62.1 FIX: honest-blank rows are NOT ctx=None — sanitize leaves
            # the bare queue container (x-rincon-queue:RINCON...#0, empty name,
            # no spotify_context). Field-verified 2026-08-15 bench: Ella row at
            # pos 4 showed post[kind=x-rincon-queue name=''] and the stamp never
            # fired. Treat that shape as blank too.
            _ctx_blankish = (ctx is None) or (
                (ctx.get("container_uri", "") or "").startswith("x-rincon-queue")
                and not (ctx.get("container_name", "") or "").strip()
                and not (ctx.get("spotify_context", "") or "")
            )
            if _ctx_blankish:
                _qpos = int(info.get("playlist_position", 0) or 0)
                _rng = _range_context(device.player_name, _qpos)
                if _rng:
                    _rng_uri = _rng.get("uri", "") or ""
                    ctx = {
                        "container_uri": _rng_uri,
                        "container_name": _rng.get("name", "") or "",
                        "container_type": ("object.container.playlistContainer"
                                           if _rng.get("type") == "playlist"
                                           else "object.container.album.musicAlbum"),
                        "spotify_context": _rng_uri if _rng_uri.startswith("spotify:") else "",
                    }
                    _san_fields = dict(_san_fields or {})
                    _san_fields["context_source"] = "insert_range"
                    log(f"[insert-range] {device.player_name}: STAMPED pos {_qpos} -> "
                        f"'{ctx['container_name']}' ({_rng.get('type')}) "
                        f"range [{_rng['pos_start']},{_rng['pos_end']}] (receipt-based)")
        except Exception as _ir_err:
            log(f"[insert-range] capture stamp failed (benign, row stays blank): {_ir_err}")
        _ti = {"title": title, "artist": artist_raw,
               "album": album_raw, "uri": uri,
               "service": detect_service(uri, metadata),
               "duration_seconds": dur_secs, "rooms": members,
               "coordinator": device.player_name,
               "container": ctx,
               "didl_parent_id": didl_parent_id,
               "didl_album_art_uri": didl_album_art_uri,
               # v2.54 rider: raw DIDL travels to history so per-service metadata
               # archaeology (containers, art, full titles) is possible later.
               # Capped at 4 KB — classical DIDL can be huge.
               "didl_raw": (metadata[:4096] if isinstance(metadata, str) else "")}
        if _san_fields:
            _ti.update(_san_fields)
        return _ti
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
    # v2.50: state-ring retire moved BELOW the coalesce check — grouped rooms now
    # produce ONE ring entry whose rooms string grows as rooms coalesce, instead
    # of N duplicate entries (one per room) as in <=2.49.
    uri_or_title = track["uri"] or f"{track['title']}|{track['artist']}"
    fp           = hashlib.md5(uri_or_title.encode()).hexdigest()[:12]
    bucket       = int(started_at.timestamp() // 60)
    started_str  = started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    ended_epoch  = ended_at.timestamp()
    # v2.50 room factoring (format 2): grouped rooms retire in the SAME event-loop
    # pass with an IDENTICAL started_at/ended_at (single `now` per pass), so instead
    # of buffering one near-identical ~700B item per room, coalesce them into one
    # item carrying a rooms LIST. Match = same content fingerprint + exact
    # started_at + ended_at within 5s. A room leaving the group mid-track retires
    # in a LATER pass (different ended_at) and correctly stays a separate item.
    # The server ingest already merges by content identity, so this is a pure
    # wire-format optimization — DB rows are unchanged (T1 golden parity proven).
    _matched_rooms = None
    with pending_buffer_lock:
        for _it in pending_buffer:
            if (_it.get("_fp") == fp and _it.get("started_at") == started_str
                    and abs(_it.get("_ended_epoch", 0) - ended_epoch) <= 5):
                if room not in _it["rooms"]:
                    _it["rooms"].append(room)
                _matched_rooms = list(_it["rooms"])
                _buf_n = len(pending_buffer)
                try:
                    PENDING_PATH.write_text(json.dumps(list(pending_buffer)), encoding="utf-8")
                    _persist_note = ""
                except Exception as _pe:
                    _persist_note = f" (persist FAILED: {_pe})"
                break
    if _matched_rooms is not None:
        # Keep the state ring in step: grow the existing entry's rooms string
        # instead of inserting a duplicate entry (one ring entry per play).
        _update_state_ring_rooms(track, started_str, _matched_rooms)
        log(f'+ Coalesced: "{track["title"]}" - {track["artist"]} | +{room} '
            f'(rooms: {len(_matched_rooms)}) [buffer: {_buf_n}]{_persist_note}')
        return
    # New play — one ring entry, rooms list grows via coalesce above
    _retire_to_state_ring(track, [room], started_at)
    # No coalesce match — build a fresh format-2 item (rooms list; dedup key has
    # NO room segment — the server DB key is content-based and room-agnostic).
    dedup_key    = f"sonos_{house}_{fp}_{bucket}"
    item = {
        "type": "sonos_history", "house": house,
        "rooms": [room],          # format 2: list of rooms that played this together
        "room": room,             # legacy alias (= rooms[0]) for transition safety
        "title": track["title"], "artist": track["artist"], "album": track["album"],
        "uri": track["uri"], "service": track["service"],
        "started_at":  started_str,
        "ended_at":    ended_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_played_seconds": duration_played,
        "track_duration_seconds":  track.get("duration_seconds", 0),
        "dedup_key": dedup_key,
        "didl_parent_id": track.get("didl_parent_id", ""),
        "didl_album_art_uri": track.get("didl_album_art_uri", ""),
        "didl_raw": track.get("didl_raw", ""),  # v2.54 rider: raw DIDL for metadata archaeology

        "_fp": fp,                # coalesce match key (internal; server ignores)
        "_ended_epoch": ended_epoch,  # coalesce tolerance check (internal)
    }
    # v2.59 C3: suppression archaeology rides the buffer row into raw_metadata
    # (one SQL query audits the first week of v2.59 — no shadow-mode release).
    for _sk in ("suppressed_container_name", "suppressed_container_uri",
                "suppressed_container_type", "context_suppressed_reason"):
        if track.get(_sk):
            item[_sk] = track[_sk]
    # Add container context (playlist/album/station) if available
    # (v2.59: track["container"] arrives pre-sanitized by sanitize_container)
    container = track.get("container")
    if container:
        item["container_uri"] = container.get("container_uri", "")
        item["container_name"] = container.get("container_name", "")
        item["container_type"] = container.get("container_type", "")
        if container.get("spotify_context"):
            item["spotify_context"] = container["spotify_context"]
    # v2.55: QUEUE PROVENANCE OVERLAY — if Sonos gave us no real container
    # (queue playback reports x-rincon-queue / nothing), stamp the remembered
    # load context so sessions can be named after the playlist/album. A real
    # EnqueuedTransportURI from Sonos always WINS over the overlay.
    # v2.59 Q6 (signed off): an injected one-off (play_next single track) gets
    # NO container and must NOT inherit the surrounding queue's provenance —
    # stamping the playlist on a one-off is the small lie v2.55 accepted and
    # C3 stops telling. context_source='inserted_track' blocks the overlay.
    if track.get("context_source") == "inserted_track":
        item["context_source"] = "inserted_track"
    _cur_container = item.get("container_uri", "")
    if (((not _cur_container) or _cur_container.startswith("x-rincon-queue"))
            and item.get("context_source") != "inserted_track"):
        _pg_coord = track.get("coordinator") or room
        _prov = _get_queue_provenance(_pg_coord)
        # v2.59.2: validate the pointer against the observed track BEFORE
        # stamping (overlay guard). Stale album-typed pointer -> honest
        # no-context + archaeology fields, pointer self-heals (cleared).
        if (_prov and _prov.get("uri")
                and _overlay_prov_guard(_prov, track.get("album", ""), _pg_coord)):
            item["suppressed_container_name"] = _prov.get("name", "")
            item["suppressed_container_uri"]  = _prov.get("uri", "")
            item["suppressed_container_type"] = _prov.get("type", "")
            item["context_suppressed_reason"] = "overlay_album_mismatch"
        elif _prov and _prov.get("uri"):
            item["container_uri"]  = _prov["uri"]
            item["container_name"] = _prov["name"]
            item["container_type"] = _prov["type"]
            item["context_source"] = "queue_provenance"  # honesty marker for archaeology
            if _prov["uri"].startswith("spotify:"):
                item["spotify_context"] = _prov["uri"]
    item["_buffered_at"] = time.time()  # for max-age relay flush
    _persist_err = None
    with pending_buffer_lock:
        pending_buffer.append(item)
        last_track_added_ts = time.time()
        count = len(pending_buffer)
        # v2.48: persist buffer on EVERY add (crash safety between flushes).
        # Previously items only hit disk at flush time -- a crash mid-session
        # lost everything buffered since the last flush.
        try:
            PENDING_PATH.write_text(json.dumps(list(pending_buffer)), encoding="utf-8")
        except Exception as _pe:
            _persist_err = str(_pe)
    if _persist_err:
        log(f"[buffer] WARNING: persist-on-add failed: {_persist_err}")
    log(f'+ Buffered: "{track["title"]}" - {track["artist"]} | {room} ({duration_played}s) [buffer: {count}]')
    if count >= BATCH_SIZE:
        flush_buffer(reason="count")

# --- PLAY MODE: HOUSE RULE (v2.48.5) ------------------------------------------
def _enforce_repeat_default(dev, cmd, room_label=""):
    """House rule (Andrew, 2026-07-15): REPEAT OFF unless explicitly requested.
    Called after every play-starting command so leftover REPEAT_ALL/REPEAT_ONE
    from a previous session never silently loops the new playback.
    - Preserves the speaker's current shuffle state unless cmd specifies 'shuffle'.
    - Honors cmd['repeat'] (bool) if the sender explicitly wants repeat.
    - Never raises: play succeeded already; a mode error must not fail the command."""
    try:
        repeat  = bool(cmd.get("repeat", False))
        cur     = dev.play_mode
        shuffle = cmd.get("shuffle")
        if shuffle is None:  # not specified -> preserve current shuffle state
            shuffle = cur in ("SHUFFLE", "SHUFFLE_NOREPEAT", "SHUFFLE_REPEAT_ONE")
        if shuffle and repeat:   new_mode = "SHUFFLE"            # shuffle + repeat all
        elif shuffle:            new_mode = "SHUFFLE_NOREPEAT"
        elif repeat:             new_mode = "REPEAT_ALL"
        else:                    new_mode = "NORMAL"
        if new_mode != cur:
            dev.play_mode = new_mode
            log(f"[play-mode] {room_label or dev.player_name}: {cur} -> {new_mode} (house rule: repeat off unless requested)")
    except Exception as e:
        log(f"[play-mode] enforce failed ({room_label}): {e}")

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
    # v2.44: write-through to disk so a restart + ntfy since=5m replay
    # can't re-execute commands this process already ran.
    try:
        EXECUTED_CMDS_PATH.write_text(json.dumps(executed_cmd_hashes), encoding="utf-8")
    except Exception as _we:
        log(f"[dedup] WARNING: failed to persist executed_cmds.json: {_we}")

def _already_executed(cmd):
    h = _cmd_hash(cmd)
    ts = executed_cmd_hashes.get(h)
    if ts is None:
        return False
    if time.time() - ts > CMD_DEDUP_TTL_SECONDS:
        del executed_cmd_hashes[h]
        return False
    # v2.44: verbose dedup decision -- silent rejections are undebuggable remotely
    log(f"[dedup] REJECTED replayed command (hash={h[:12]}, age={int(time.time()-ts)}s, action={cmd.get('action','?')})")
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

def _verify_group_settle(dev, intended_coord, timeout=5.0, interval=0.5):
    """v2.58 A8: replace the old fixed 1s settle sleep + single coordinator read.
    SoCo group state can lag a join/unjoin, so a fast follow-up verb could act
    on the OLD coordinator's queue (finding #9). Poll until the observed
    coordinator matches the intended one; if it never converges, WARN loudly
    and proceed anyway (the caller re-reads the coordinator after this)."""
    deadline = time.time() + timeout
    observed = "?"
    while time.time() < deadline:
        try:
            observed = (dev.group.coordinator.player_name
                        if dev.group and dev.group.coordinator else dev.player_name)
            if observed == intended_coord:
                log(f"_setup_rooms: settle verified -- coordinator={observed}")
                return True
        except Exception as _sv_err:
            observed = f"read-issue:{_sv_err}"
        time.sleep(interval)
    log(f"_setup_rooms: WARNING -- group settle never converged after {timeout:.0f}s "
        f"(observed coordinator={observed}, intended={intended_coord}); proceeding anyway")
    return False

def _setup_rooms(cmd, devices):
    """Incremental room grouping. Returns (coordinator, rooms_list, was_grouped_with).
    Compares current group state vs desired rooms — only unjoins/joins deltas.
    No-op fast path when group already matches desired state."""
    rooms = cmd.get("rooms", [])
    if isinstance(rooms, str): rooms = [rooms]
    room = cmd.get("room")
    if room and not rooms: rooms = [room]
    if not rooms: return None, [], []

    # v2.55: COORDINATOR-PRESERVING REGROUP (bug fix, live incident 2026-07-30).
    # rooms[0] used to become the group leader UNCONDITIONALLY. When another
    # *requested* room was the coordinator of a currently-PLAYING group, the
    # delta logic below rebuilt the group around rooms[0]'s silent queue and
    # killed playback (Kokoroko died when Group Speakers sent Kitchen first
    # while Living Room Maury was the playing coordinator). Fix: if a requested
    # room coordinates a PLAYING group, move it to the front so it stays leader
    # and playback never blinks. rooms[0] is honored when nothing is playing.
    if len(rooms) > 1:
        try:
            for _r in rooms:
                _d = devices.get(_r)
                if not _d: continue
                _coord = _d.group.coordinator if _d.group and _d.group.coordinator else _d
                _cname = _coord.player_name
                if _cname in rooms:
                    _tstate = _coord.get_current_transport_info().get("current_transport_state", "")
                    if _tstate == "PLAYING":
                        if _cname != rooms[0]:
                            log(f"_setup_rooms: preferring playing coordinator '{_cname}' as primary (was '{rooms[0]}')")
                            rooms = [_cname] + [x for x in rooms if x != _cname]
                        # rooms[0] already the playing coordinator -> keep it; either
                        # way stop scanning so a later group can't steal leadership.
                        break
        except Exception as _cp_err:
            log(f"_setup_rooms: coordinator-preference check failed (falling back to rooms[0]): {_cp_err}")

    primary = rooms[0]
    dev = devices.get(primary)
    if not dev: return None, rooms, []

    was_grouped = []
    _topo_changed = False  # v2.58 A7c: any join/unjoin performed this call

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
                _topo_changed = True
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
                    _topo_changed = True
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
                    _topo_changed = True
                except Exception as e:
                    log(f"_setup_rooms: failed to join {r} to {primary}: {e}")
        if joined or to_remove:
            # v2.58 A8: verify-poll (up to 5s) until the observed coordinator is
            # the intended one, instead of a blind 1s sleep + single read -- a
            # fast follow-up verb could land on the OLD coordinator's queue.
            _verify_group_settle(dev, primary)
        if joined:
            log(f"_setup_rooms: incremental group update -- {primary} + {joined} (removed: {list(to_remove)})")
        else:
            log(f"_setup_rooms: group adjusted -- removed {list(to_remove)}")
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
                _topo_changed = True
                # v2.58 A8: verify-poll instead of blind 1s sleep (see above)
                _verify_group_settle(dev, primary)
            except Exception:
                pass
            log(f"_setup_rooms: isolated {primary} from {was_grouped}")

    # Return the coordinator device
    try:
        coordinator = dev.group.coordinator if dev.group and dev.group.coordinator else dev
    except Exception:
        coordinator = dev
    # v2.58 A7c: topology delta logging -- whenever this call joined/unjoined
    # anything, log the before/after group maps, and ALWAYS call out a
    # coordinator switch explicitly (the event that made the 2026-08-03
    # Kaytranada interruption hard to reconstruct). Never raises.
    try:
        _new_coord = getattr(coordinator, "player_name", primary)
        if _topo_changed:
            try:
                _after_members = sorted(m.player_name for m in dev.group.members
                                        if getattr(m, "is_visible", True)) if dev.group else [primary]
            except Exception:
                _after_members = [primary]
            log(f"[topology-delta] _setup_rooms: before coordinator={current_coordinator} "
                f"members={sorted(current_members)} -> after coordinator={_new_coord} "
                f"members={_after_members}")
        if _new_coord != current_coordinator:
            log(f"[topology-delta] coordinator switched: {current_coordinator} -> {_new_coord}")
    except Exception as _td_err:
        log(f"[topology-delta] logging did not succeed (benign no-op): {_td_err}")
    return coordinator, rooms, was_grouped


# Actions that execute locally without any webhook POST (ack or result).
# Avoids unnecessary agent invocations for high-frequency, low-value commands.
# v2.52: queue mutations (add playlist/track, clear) can legitimately take >5s on
# large queues — Sonos re-indexes every entry behind the insert point. Root cause of
# the 2026-07-20 Garage silent failure: playlist insert at pos 2 of a 1,670-track
# queue took >5s, SoCo timed out, play step never ran. Mutations get 30s; the 5s
# global stays for polling reads so a dead speaker can't stall the poll loop.
QUEUE_MUTATION_TIMEOUT_S = 30

from contextlib import contextmanager

@contextmanager
def _queue_mutation_timeout(seconds=QUEUE_MUTATION_TIMEOUT_S):
    """Temporarily raise SoCo's global request timeout for queue-mutation calls.
    NOTE: soco.config.REQUEST_TIMEOUT is global — a concurrent poll inside this
    window inherits the longer timeout. Acceptable: worst case one slow poll."""
    import soco as _soco_cm
    prev = _soco_cm.config.REQUEST_TIMEOUT
    _soco_cm.config.REQUEST_TIMEOUT = seconds
    try:
        yield
    finally:
        _soco_cm.config.REQUEST_TIMEOUT = prev

# v2.54: sync_rooms is now silent — its result travels over SSE only (see the
# sync_rooms branch). It stays in NON_STATE_ACTIONS (no debounced heartbeat).
SILENT_ACTIONS = {"volume_up", "volume_down", "set_volume", "volume", "resume", "play_resume", "next", "previous", "pause", "update_check", "get_logs", "flush", "toggle_mute", "cycle_repeat", "set_shuffle", "play_next", "add_to_queue", "play_radio", "play_album", "sync_rooms", "replace_queue", "truncate_queue"}
# v2.48: reads/meta actions that do NOT change playback state -> no debounced heartbeat
NON_STATE_ACTIONS = {"update_check", "get_logs", "get_volume", "get_status", "flush", "restart", "sync_rooms"}

def execute_command(cmd, source="unknown"):
    action = cmd.get("action", "")
    cmd_id = cmd.get("cmd_id", "")
    if action in ("none", "", "idle") or cmd_id == "idle":
        return

    # Track command for diagnostics
    global _last_command_at, _last_command_action, _last_command_source, _commands_received_count, last_post_ts, current_devices_by_name
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

        # v2.24: JIT discovery — if command targets rooms not in cache, try to find them
        cmd_rooms = cmd.get("rooms", [])
        if isinstance(cmd_rooms, str): cmd_rooms = [cmd_rooms]
        if cmd.get("room") and not cmd_rooms: cmd_rooms = [cmd.get("room")]
        if cmd_rooms and action not in ("update_check", "flush", "get_logs"):
            missing = [r for r in cmd_rooms if r not in devices]
            if missing:
                for r in missing:
                    _jit_discover(r)
                devices = current_devices_by_name  # re-read after JIT update

        if action == "update_check":
            # [ROLLBACK-UNSAFE] This code path triggers self_update_check() from the
            # old version. The 2s delay + thread spawn all run in currently deployed code.
            result["success"] = True
            result["message"] = f"Running update check (v{SERVICE_VERSION})"
            def _do(): time.sleep(2); self_update_check()
            threading.Thread(target=_do, daemon=True).start()

        elif action == "restart":
            # v2.45: real remote restart. Post result first (2s delay in thread),
            # flush history buffer, then respawn a fresh process and exit.
            result["success"] = True
            result["message"] = f"Restarting service (v{SERVICE_VERSION})"
            def _do_restart():
                global _mutex_handle
                time.sleep(2)
                try:
                    flush_buffer(reason="pre-restart")
                except Exception as e:
                    log(f"[restart] pre-restart flush failed: {e}")
                try:
                    _persist_state_ring_buffer()
                except Exception as e:
                    log(f"[restart] ring buffer persist failed: {e}")
                log("[restart] Respawning new process...")
                if _mutex_handle is not None:
                    try:
                        import ctypes as _ct
                        _ct.windll.kernel32.CloseHandle(_mutex_handle)
                        _mutex_handle = None
                    except Exception as e:
                        log(f"[restart] mutex release failed: {e}")
                try:
                    _rst_script = Path(sys.argv[0]).resolve()
                    subprocess.Popen(
                        [sys.executable, str(_rst_script)] + sys.argv[1:],
                        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                    )
                except Exception as e:
                    log(f"[restart] RESPAWN FAILED: {e} -- NOT exiting (service stays up)")
                    return
                os._exit(0)
            threading.Thread(target=_do_restart, daemon=True).start()

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
            _fl_snap = result["heartbeat"].pop("_delta_snap", None)  # v2.49
            result["t_result_sent"] = now_iso()
            try:
                r = requests.post(WEBHOOK, json=result, timeout=15)
                log(f"Flush result -> HTTP {r.status_code}: {result['message']}")
                if 200 <= r.status_code < 300:
                    _delta_commit(_fl_snap)  # v2.49: mark piggyback delivered
                last_post_ts = time.time()
            except Exception as e:
                log(f"Failed to post flush result: {e}")
            return  # early return -- skip normal silent/non-silent POST logic

        elif action == "push_now":
            # v2.56 D3: SSE nudge — page requests an immediate state push (fires at
            # page load only when no snapshot rode the ?since=16m SSE replay).
            # Best-effort BY DESIGN: rides the normal bundler (debounce / min-gap /
            # 429 backoff / quiet-hours gate all apply); nothing may depend on it.
            # Cheap: no speaker I/O — just queues a bundled status_update push.
            log("Command: push_now (SSE nudge) — queueing status_update push")
            publish_ui_event("status_update", {})
            result["success"] = True
            result["message"] = "SSE push queued"

        elif action == "get_state":
            state = []
            for dev in get_coordinators():
                info = get_track_info(dev)
                try:    members = [m.player_name for m in dev.group.members if getattr(m, "is_visible", True)]  # v2.54: skip invisible bonded units
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

        elif action == "sync_rooms":
            rooms = cmd.get("rooms", [])
            if isinstance(rooms, str): rooms = [rooms]
            if not rooms:
                result["message"] = "No rooms specified"
            else:
                log(f"sync_rooms: requested rooms={rooms}")
                # Re-discover devices for a fresh view of the topology
                try:
                    import soco as _soco_mod
                    fresh = {d.player_name: d for d in _soco_mod.discover(timeout=3) or []}
                    if fresh:
                        devices.update(fresh)
                        log(f"sync_rooms: refreshed topology, {len(fresh)} devices")
                except Exception as e:
                    log(f"sync_rooms: topology refresh failed (using cached): {e}")
                dev, final_rooms, was_grouped = _setup_rooms(cmd, devices)
                if dev:
                    result["success"] = True
                    result["message"] = f"Synced: {', '.join(final_rooms)}"
                    if was_grouped:
                        result["message"] += f" (removed: {', '.join(was_grouped)})"
                else:
                    result["message"] = f"Room '{rooms[0]}' not found"
                # v2.54 rider: sync_rooms results travel over SSE ONLY (added to
                # SILENT_ACTIONS -> no Tasklet webhook POST). The page is the only
                # consumer; the agent never needed these posts. Failures still land
                # in the error ring via post_error (surfaces as badge/watchdog row).
                try:
                    publish_ui_event("sync_rooms_result", {
                        "success": result["success"],
                        "message": result["message"],
                        "cmd_ts": cmd.get("cmd_ts", ""),
                    })
                    publish_ui_event("status_update", {})
                except Exception as _sr_err:
                    log(f"sync_rooms: SSE publish failed: {_sr_err}")
                if not result["success"]:
                    post_error(f"sync_rooms failed: {result['message']}", context=f"rooms={rooms}", module="sonos")

        elif action == "search":
            from soco.music_services import MusicService
            svc_name    = _resolve_music_service_name(cmd.get("service", "Qobuz"))  # v2.59 G8
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
            svc_name    = _resolve_music_service_name(cmd.get("service", "Qobuz"))  # v2.59 G8
            query       = cmd.get("query", "")
            search_type = cmd.get("search_type", "albums")
            # v2.57 P0-F4: queue_mode "next"|"end"|"play" (default "play" = legacy
            # behavior). The sheet's Play-next/Add-to-end on a track with no
            # resolvable URI falls back to THIS action — without queue_mode it
            # would clear the queue and PLAY instead of queueing (Phase 0 finding).
            queue_mode  = str(cmd.get("queue_mode", "play")).lower()
            dev = devices.get(room)
            if not dev:
                result["message"] = f"Room '{room}' not found"
            elif not query:
                result["message"] = "No query provided"
            elif "spotify" in svc_name.lower():
                # === v2.54 B2: Spotify resolution via Web API, NOT SoCo SMAPI search ===
                # The per-device SMAPI search token expires independently of every
                # other Spotify credential (2026-07-29 Mind incident) — SoCo search
                # is demoted to never-auto-selected for Spotify. Resolve via the
                # self-refreshing client-credentials app token, then play the
                # resulting spotify: URI through the proven ShareLink path.
                from soco.plugins.sharelink import ShareLinkPlugin
                import urllib.parse as _up
                sp_type = "album" if str(search_type).startswith("album") else "track"
                try:
                    resp = requests.get(
                        "https://api.spotify.com/v1/search?q=" + _up.quote(query) + f"&type={sp_type}&limit=5",
                        headers={"Authorization": f"Bearer {_spotify_app_token()}"}, timeout=10)
                    resp.raise_for_status()
                    sp_items = resp.json().get(sp_type + "s", {}).get("items", [])
                except Exception as sp_err:
                    log(f"search_and_play: Spotify Web API failed: {sp_err}")
                    sp_items = []
                if not sp_items:
                    result["message"] = f"No {sp_type}s found on Spotify (Web API) for '{query}'"
                else:
                    first = sp_items[0]
                    title = first.get("name", "?")
                    _sp_artist = (first.get("artists") or [{}])[0].get("name", "")
                    share_url = f"https://open.spotify.com/{sp_type}/{first.get('id','')}"
                    log(f"search_and_play: Spotify Web API resolved '{query}' -> '{title}' by {_sp_artist} ({share_url}) [queue_mode={queue_mode}]")
                    if queue_mode in ("next", "end"):
                        # v2.57 P0-F4: queue WITHOUT clearing or starting playback.
                        # Coordinator-resolve, never regroup (sheet contract, D6).
                        _sap_coord = dev.group.coordinator if dev.group and dev.group.coordinator else dev
                        _sap_pos, _sap_plc = None, "direct"
                        if queue_mode == "next":
                            try:
                                _sap_cur = int(_sap_coord.get_current_track_info().get("playlist_position", 0))
                                _sap_ins = _sap_cur + 1
                                _sap_pos, _sap_plc = _verified_queue_add(
                                    _sap_coord,
                                    lambda: ShareLinkPlugin(_sap_coord).add_share_link_to_queue(share_url, position=_sap_ins),
                                    _sap_ins, label=" search_and_play(next)")
                            except Exception as _sap_err:
                                log(f"search_and_play: next-position add failed ({_sap_err}), appending instead")
                                _sap_pos, _sap_plc = _verified_queue_add(
                                    _sap_coord,
                                    lambda: ShareLinkPlugin(_sap_coord).add_share_link_to_queue(share_url),
                                    None, label=" search_and_play(end)")
                        else:
                            _sap_pos, _sap_plc = _verified_queue_add(
                                _sap_coord,
                                lambda: ShareLinkPlugin(_sap_coord).add_share_link_to_queue(share_url),
                                None, label=" search_and_play(end)")
                        result["success"] = True
                        _sap_note = f" [WARNING: landed at queue slot {_sap_pos}]" if _sap_plc == "degraded" else ""
                        result["message"] = f"Queued ({queue_mode}) '{title}'{' by ' + _sp_artist if _sp_artist else ''} (Spotify, Web-API resolved) in {room}{_sap_note}"
                        result["data"]    = {"title": title, "artist": _sp_artist, "uri": first.get("uri", ""), "share_url": share_url, "service": "Spotify", "queue_mode": queue_mode, "queued_at": _sap_pos, "placement": _sap_plc}
                        try:
                            schedule_state_push()  # v2.57: refresh queue_summary
                        except Exception:
                            pass
                    else:
                        with _queue_mutation_timeout():
                            dev.clear_queue()
                        ShareLinkPlugin(dev).add_share_link_to_queue(share_url)
                        dev.play_from_queue(0)
                        _enforce_repeat_default(dev, cmd, room)  # v2.48.5 house rule
                        result["success"] = True
                        result["message"] = f"Playing '{title}'{' by ' + _sp_artist if _sp_artist else ''} (Spotify, Web-API resolved) in {room}"
                        result["data"]    = {"title": title, "artist": _sp_artist, "uri": first.get("uri", ""), "share_url": share_url, "service": "Spotify"}
            else:
                items = list(MusicService(svc_name).search(search_type, query, 0, 5))
                if not items:
                    result["message"] = f"No {search_type} for '{query}' on {svc_name}"
                else:
                    first = items[0]
                    title = getattr(first, "title", str(first))
                    uri   = getattr(first, "uri", None)
                    meta  = getattr(first, "to_didl_string", lambda: "")()
                    if uri and queue_mode in ("next", "end"):
                        # v2.57 P0-F4 (sibling sweep): native-service queue-add without
                        # starting playback. Coordinator-resolve, never regroup.
                        _sap_coord = dev.group.coordinator if dev.group and dev.group.coordinator else dev
                        try:
                            _sap_pos = _sap_coord.add_to_queue(first, as_next=(queue_mode == "next"))
                            result["success"] = True
                            result["message"] = f"Queued ({queue_mode}) '{title}' ({svc_name}) in {room} at pos {_sap_pos}"
                            result["data"]    = {"title": title, "uri": uri, "service": svc_name, "queue_mode": queue_mode, "queued_at": _sap_pos}
                            try:
                                schedule_state_push()  # v2.57: refresh queue_summary
                            except Exception:
                                pass
                        except Exception as _sap_nq_err:
                            result["message"] = f"Queue add failed for '{title}' ({svc_name}): {_sap_nq_err}"
                    elif uri:
                        dev.play_uri(uri, meta=meta, title=title)
                        _enforce_repeat_default(dev, cmd, room)  # v2.48.5 house rule
                        result["success"] = True
                        result["message"] = f"Playing '{title}' ({svc_name}) in {room}"
                        result["data"]    = {"title":title,"uri":uri,"service":svc_name}
                    else:
                        result["message"] = f"Found '{title}' but no URI"

        elif action == "search_and_queue":
            # Like search_and_play but uses add_to_queue(DidlObject) instead of play_uri
            from soco.music_services import MusicService
            room        = cmd.get("room") or (cmd.get("rooms") or [None])[0]
            svc_name    = _resolve_music_service_name(cmd.get("service", "Qobuz"))  # v2.59 G8
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
                    item_class = type(first).__name__
                    log(f"search_and_queue: item type={item_class} title='{title}' uri={uri}")
                    log(f"search_and_queue: item attrs: {[a for a in dir(first) if not a.startswith('_')]}")
                    try:
                        pos = dev.add_to_queue(first)
                        log(f"search_and_queue: add_to_queue returned position={pos}")
                        dev.play_from_queue(pos - 1)  # 0-indexed
                        _enforce_repeat_default(dev, cmd, room)  # v2.48.5 house rule
                        # v2.60 queue sources: append (keyed by coordinator, like all hooks)
                        try:
                            _saq_coord = (dev.group.coordinator.player_name
                                          if dev.group and dev.group.coordinator else dev.player_name)
                        except Exception:
                            _saq_coord = dev.player_name
                        if search_type.startswith("album"):
                            _append_queue_source(_saq_coord, "album", title, uri or "")
                        else:
                            _append_queue_source(_saq_coord, "track")
                        result["success"] = True
                        result["message"] = f"Queued+playing '{title}' ({svc_name}) in {room} at pos {pos}"
                        result["data"] = {"title": title, "uri": uri, "service": svc_name, "item_class": item_class, "position": pos}
                    except Exception as eq:
                        log(f"search_and_queue: add_to_queue failed: {eq}")
                        # Fallback: log the DIDL for debugging
                        meta = getattr(first, "to_didl_string", lambda: "n/a")()
                        log(f"search_and_queue: DIDL metadata: {meta[:500]}")
                        result["message"] = f"add_to_queue failed: {eq}"

        elif action == "play_album":
            # [v2.43] Play a full album natively (Qobuz). [v2.47] Apple Music branch.
            # Shared design: resolve the album to a native MusicService DidlObject
            # (per-service resolution below), then add_to_queue() -- Sonos expands
            # the container into individual tracks. queue_only inserts after the
            # current track without interrupting playback; otherwise we insert
            # after current and skip to it (preserves the rest of the queue).
            from soco.music_services import MusicService
            import urllib.parse
            QOBUZ_APP_ID = "712109809"
            album_title = cmd.get("title", "")
            album_artist = cmd.get("artist", "")
            svc_name    = _resolve_music_service_name(cmd.get("service", "Qobuz"))  # v2.59 G8
            queue_only  = cmd.get("queue_only", False)
            # v2.57 (§3.1): replace mode — load-then-trim atomic queue replace for
            # Qobuz/Apple/Spotify albums through this action's resolution pipeline.
            # replace wins over queue_only if both are (wrongly) set.
            replace_q   = bool(cmd.get("replace", False) or cmd.get("replace_queue", False))
            # v2.54 B2: direct Spotify album URI skips all resolution (Tier-0 path).
            # Accept either "album_uri" or "uri" carrying spotify:album:ID.
            spotify_album_uri = ""
            for _cand in (cmd.get("album_uri", ""), cmd.get("uri", "")):
                if isinstance(_cand, str) and _cand.startswith("spotify:album:"):
                    spotify_album_uri = _cand
                    break
            # v2.57 P0-F1 FIX: queue mutations must NEVER regroup speakers.
            # _setup_rooms regrouped even for queue_only=true adds (latent bug —
            # "+ Queue" on a Qobuz/Apple album silently rebuilt the group around
            # the selection). Sheet verbs (D6) operate on the CURRENT group of the
            # selected room, so queue_only and replace resolve the device directly;
            # only "play now" (neither flag) keeps the legacy regroup-to-selection.
            if queue_only or replace_q:
                _rooms_raw = cmd.get("rooms", [])
                if isinstance(_rooms_raw, str):
                    _rooms_raw = [_rooms_raw]
                _room0 = cmd.get("room") or (_rooms_raw[0] if _rooms_raw else None)
                dev = devices.get(_room0) if _room0 else None
                rooms = _rooms_raw or ([_room0] if _room0 else [])
                was_grouped = []
            else:
                dev, rooms, was_grouped = _setup_rooms(cmd, devices)
            if not dev:
                result["message"] = f"Room '{(cmd.get('rooms') or ['?'])[0]}' not found. Available: {list(devices.keys())}"
            elif not album_title and not spotify_album_uri:
                result["message"] = "No album title provided"
            else:
                coordinator = dev.group.coordinator if dev.group and dev.group.coordinator else dev
                # v2.58 Phase B: STALE-QUEUE GUARD. A non-replace play_album on a
                # STOPPED coordinator whose queue is stale (>24h untouched, or
                # unknown age) silently converts to REPLACE -- the album must
                # never land inside forgotten leftovers (2026-08-03: El Bueno
                # inserted into a day-old 9-row queue leaked Sister Sledge/MJQ).
                # Playing or stopped-but-fresh queues keep literal insert.
                # D2: queue_only ("+ Queue" = add-to-end flavor) is EXEMPT --
                # it never converts and never starts playback on its own.
                _b_converted_from, _b_queue_age = None, ""
                if not replace_q and not queue_only:
                    try:
                        _sg_state = coordinator.get_current_transport_info().get("current_transport_state", "")
                        if _sg_state in ("STOPPED", "NO_MEDIA_PRESENT") and int(coordinator.queue_size) > 0:
                            _sg_stale, _sg_age = _queue_is_stale(coordinator.player_name)
                            if _sg_stale:
                                log(f"[stale-guard] play_album: {coordinator.player_name} stopped + queue stale ({_sg_age}) -- converting insert to REPLACE")
                                _b_converted_from = "play_album"
                                _b_queue_age = _sg_age
                                replace_q, queue_only = True, False
                            else:
                                log(f"[stale-guard] play_album: {coordinator.player_name} stopped, queue fresh ({_sg_age}) -- literal insert")
                    except Exception as _sg_err:
                        log(f"[stale-guard] play_album: staleness check skipped ({_sg_err}); literal insert")
                query_str = f"{album_title} {album_artist}".strip()

                # Per-service resolution fills these. album_item stays None on
                # failure -- the failing branch MUST set result["message"].
                # [v2.47.1] Apple resolves to apple_share_url (ShareLink) instead
                # of a DidlObject; the shared queue step dispatches on it.
                album_item = None
                apple_share_url = None
                spotify_share_url = None  # v2.54 B2
                chosen_title, chosen_artist = album_title, album_artist
                chosen_tracks, chosen_id = 0, ""

                if spotify_album_uri or "spotify" in svc_name.lower():
                    # === SPOTIFY (v2.54 B2) ===
                    # Resolution via Spotify Web API client-credentials (app token,
                    # self-refreshing, no user auth) — then queue the album container
                    # via the proven ShareLink path. SoCo MusicService('Spotify')
                    # search is deliberately NOT used here: its per-device SMAPI token
                    # expires independently of the household link and the browser
                    # token (2026-07-29: Mind's search token died while both others
                    # were fine; reauth in the Sonos app could not fix it). Search
                    # has left the critical play path.
                    if spotify_album_uri:
                        chosen_id = spotify_album_uri.split(":")[-1]
                        spotify_share_url = f"https://open.spotify.com/album/{chosen_id}"
                        if not chosen_title:
                            chosen_title = "Album"
                        log(f"play_album: direct Spotify album URI {spotify_album_uri} (no resolution needed)")
                    else:
                        log(f"play_album: resolving '{query_str}' via Spotify Web API (client credentials)")
                        try:
                            _q = f"album:{album_title}"
                            if album_artist:
                                _q += f" artist:{album_artist}"
                            resp = requests.get(
                                "https://api.spotify.com/v1/search?q=" + urllib.parse.quote(_q) + "&type=album&limit=10",
                                headers={"Authorization": f"Bearer {_spotify_app_token()}"}, timeout=10)
                            resp.raise_for_status()
                            sp_albums = resp.json().get("albums", {}).get("items", [])
                        except Exception as sp_err:
                            log(f"play_album: Spotify Web API search failed: {sp_err}")
                            sp_albums = []
                        if sp_albums:
                            log(f"play_album: Spotify Web API returned {len(sp_albums)} albums:")
                            for i, a in enumerate(sp_albums):
                                _art = (a.get("artists") or [{}])[0].get("name", "?")
                                log(f"  [{i}] id={a.get('id','?')} tracks={a.get('total_tracks',0)} '{a.get('name','?')}' by {_art} ({a.get('release_date','?')[:4]})")
                            chosen = _spotify_pick_album(sp_albums, album_title)
                            chosen_title  = chosen.get("name", album_title)
                            chosen_artist = (chosen.get("artists") or [{}])[0].get("name", album_artist)
                            chosen_tracks = chosen.get("total_tracks", 0)
                            chosen_id     = str(chosen.get("id", ""))
                            spotify_share_url = f"https://open.spotify.com/album/{chosen_id}"
                            log(f"play_album: Spotify selected id={chosen_id} tracks={chosen_tracks} '{chosen_title}' by {chosen_artist}")
                        else:
                            result["message"] = f"No album found on Spotify for '{query_str}'"

                elif "apple" in svc_name.lower():
                    # === APPLE MUSIC (v2.47) ===
                    # Step 1: iTunes Search API (public, keyless) to disambiguate
                    # singles vs full albums via trackCount + get canonical naming.
                    log(f"play_album: searching iTunes API for '{query_str}'")
                    try:
                        api_url = f"https://itunes.apple.com/search?term={urllib.parse.quote(query_str)}&entity=album&limit=10"
                        resp = requests.get(api_url, timeout=10)
                        resp.raise_for_status()
                        api_albums = resp.json().get("results", [])
                    except Exception as api_err:
                        log(f"play_album: iTunes API search failed: {api_err}")
                        api_albums = []

                    if api_albums:
                        log(f"play_album: iTunes API returned {len(api_albums)} albums:")
                        for i, a in enumerate(api_albums):
                            log(f"  [{i}] id={a.get('collectionId','?')} tracks={a.get('trackCount',0)} '{a.get('collectionName','?')}' by {a.get('artistName','?')}")
                        def _norm(s):
                            return "".join(ch for ch in str(s).casefold() if ch.isalnum() or ch == " ").strip()
                        full_albums = [a for a in api_albums if a.get("trackCount", 0) > 1]
                        if not full_albums:
                            log(f"play_album: all iTunes results are singles, using first result")
                            chosen = api_albums[0]
                        else:
                            # [v2.47.1] Prefer exact normalized title match, then substring,
                            # then iTunes relevance order. (max-trackCount wrongly picked
                            # 35-track 'Decade' over the requested 'Harvest'.)
                            want = _norm(album_title)
                            exact = [a for a in full_albums if _norm(a.get("collectionName", "")) == want]
                            sub   = [a for a in full_albums if want and want in _norm(a.get("collectionName", ""))]
                            chosen = (exact or sub or full_albums)[0]
                            match_kind = "exact" if exact else ("substring" if sub else "first-full")
                            log(f"play_album: iTunes title match={match_kind} for '{album_title}'")
                        chosen_title  = chosen.get("collectionName", album_title)
                        chosen_artist = chosen.get("artistName", album_artist)
                        chosen_tracks = chosen.get("trackCount", 0)
                        chosen_id     = str(chosen.get("collectionId", ""))
                        log(f"play_album: iTunes selected id={chosen_id} tracks={chosen_tracks} '{chosen_title}' by {chosen_artist}")
                    else:
                        log(f"play_album: iTunes API gave no results for '{query_str}'")

                    # Step 2 [v2.47.1]: Build an Apple Music share link from the iTunes
                    # collectionId and let ShareLinkPlugin queue the album container.
                    # Apple's SMAPI search endpoint rejects soco search() calls with
                    # SOAP-ENV:Server faults, so we bypass SMAPI entirely -- the iTunes
                    # collectionId IS the native Apple Music album ID.
                    if chosen_id:
                        apple_share_url = f"https://music.apple.com/us/album/a/{chosen_id}"
                        log(f"play_album: Apple share link: {apple_share_url} ('{chosen_title}' by {chosen_artist}, {chosen_tracks} tracks)")
                    else:
                        result["message"] = f"No album found via iTunes search for '{query_str}'"

                else:
                    # === QOBUZ (v2.43) ===
                    # Two-step: Qobuz API search to find the right album (disambiguate
                    # singles vs full albums via tracks_count), then MusicService search
                    # by album ID to get the native DidlObject.
                    log(f"play_album: searching Qobuz API for '{query_str}'")
                    try:
                        api_url = f"https://www.qobuz.com/api.json/0.2/catalog/search?query={urllib.parse.quote(query_str)}&app_id={QOBUZ_APP_ID}&limit=10&type=albums"
                        resp = requests.get(api_url, timeout=10)
                        resp.raise_for_status()
                        api_data = resp.json()
                        api_albums = api_data.get("albums", {}).get("items", [])
                    except Exception as api_err:
                        log(f"play_album: Qobuz API search failed: {api_err}")
                        api_albums = []

                    if not api_albums:
                        log(f"play_album: no albums found via Qobuz API for '{query_str}'")
                        result["message"] = f"No albums found on Qobuz for '{query_str}'"
                    else:
                        # Log ALL results for visibility
                        log(f"play_album: Qobuz API returned {len(api_albums)} albums:")
                        for i, a in enumerate(api_albums):
                            a_title = a.get("title", "?")
                            a_artist = a.get("artist", {}).get("name", "?")
                            a_tracks = a.get("tracks_count", 0)
                            a_id = a.get("id", "?")
                            a_qid = a.get("qobuz_id", "?")
                            log(f"  [{i}] id={a_id} qobuz_id={a_qid} tracks={a_tracks} '{a_title}' by {a_artist}")

                        # Pick the album with the most tracks (skip singles)
                        full_albums = [a for a in api_albums if a.get("tracks_count", 0) > 1]
                        if not full_albums:
                            log(f"play_album: all results are singles, using first result")
                            chosen = api_albums[0]
                        else:
                            chosen = max(full_albums, key=lambda a: a.get("tracks_count", 0))

                        chosen_id = chosen.get("id", "")
                        chosen_title = chosen.get("title", "?")
                        chosen_artist = chosen.get("artist", {}).get("name", "?")
                        chosen_tracks = chosen.get("tracks_count", 0)
                        log(f"play_album: selected id={chosen_id} tracks={chosen_tracks} '{chosen_title}' by {chosen_artist}")

                        # MusicService search by album ID to get native DidlObject
                        try:
                            ms_items = list(MusicService(svc_name).search("albums", str(chosen_id), 0, 5))
                            log(f"play_album: MusicService search for '{chosen_id}' returned {len(ms_items)} items")
                            for j, msi in enumerate(ms_items):
                                log(f"  MS[{j}] type={type(msi).__name__} title='{getattr(msi, 'title', '?')}' uri={getattr(msi, 'uri', '?')}")
                        except Exception as ms_err:
                            log(f"play_album: MusicService search failed: {ms_err}")
                            ms_items = []

                        if not ms_items:
                            result["message"] = f"Found album '{chosen_title}' ({chosen_tracks} tracks) on Qobuz API but MusicService search failed"
                        else:
                            album_item = ms_items[0]

                # === SHARED QUEUE STEP (all services) ===
                # [v2.54 B2] share-link dispatch now covers Apple AND Spotify.
                share_link_url = apple_share_url or spotify_share_url
                if album_item is not None or share_link_url:
                    from soco.plugins.sharelink import ShareLinkPlugin

                    def _enqueue(position=None):
                        # [v2.47.1/v2.54] Dispatch: Apple/Spotify via ShareLinkPlugin
                        # (share URL), everything else via native DidlObject add_to_queue.
                        # Returns FirstTrackNumberEnqueued (int) on all paths.
                        if share_link_url:
                            sl = ShareLinkPlugin(coordinator)
                            if position:
                                return sl.add_share_link_to_queue(share_link_url, position=position, dc_title=chosen_title)
                            return sl.add_share_link_to_queue(share_link_url, dc_title=chosen_title)
                        if position:
                            return coordinator.add_to_queue(album_item, position=position)
                        return coordinator.add_to_queue(album_item)

                    album_item_title = chosen_title if share_link_url else getattr(album_item, "title", str(album_item))
                    log(f"play_album: queueing '{album_item_title}' via {'ShareLink' if share_link_url else type(album_item).__name__}")
                    try:
                        # v2.54 B1: container adds are verified too — num_tracks tells the
                        # reorder how many rows the album occupies (0/unknown -> no blind
                        # reorder, degraded instead).
                        _qa_placement = "direct"
                        _rq_trim_failed = False  # v2.57 E12 tracking
                        # v2.58 A7: capture queue shape before the mutation
                        try:
                            _qo_before = int(coordinator.queue_size)
                        except Exception:
                            _qo_before = None
                        _qo_pos_req = None
                        if replace_q:
                            # v2.57 (§3.1) LOAD-THEN-TRIM (F1): append new album at the
                            # end FIRST; only after the add is verified do we remove the
                            # old rows. If the add fails, the outer except fires and the
                            # old queue is untouched — old music keeps playing (E1).
                            old_len = coordinator.queue_size  # raises -> outer except, queue untouched
                            pos, _qa_placement = _verified_queue_add(
                                coordinator, lambda: _enqueue(), None,
                                label=" play_album(replace)", num_tracks=chosen_tracks or 0)
                            first_new = pos or (old_len + 1)
                            log(f"play_album: replace — appended at {first_new} (old queue {old_len} rows), trimming old rows")
                            if old_len > 0:
                                try:
                                    coordinator.avTransport.RemoveTrackRangeFromQueue([
                                        ("InstanceID", 0), ("UpdateID", 0),
                                        ("StartingIndex", 1), ("NumberOfTracks", old_len)])
                                except Exception as _tr_err:
                                    # E12: add landed, trim failed — play the new content
                                    # from where it actually lives; stale rows linger above
                                    # and the next replace/clear sweeps them. Honest WARN.
                                    _rq_trim_failed = True
                                    log(f"play_album: replace WARNING — trim of {old_len} old rows failed ({_tr_err}); playing new content at {first_new}")
                            if _rq_trim_failed:
                                coordinator.play_from_queue(first_new - 1)
                            else:
                                coordinator.play_from_queue(0)
                        elif queue_only:
                            # Insert after current track without interrupting playback
                            try:
                                info = coordinator.get_current_track_info()
                                current_pos = int(info.get('playlist_position', 0))
                                insert_pos = current_pos + 1
                                _qo_pos_req = insert_pos
                                pos, _qa_placement = _verified_queue_add(
                                    coordinator, lambda: _enqueue(insert_pos), insert_pos,
                                    label=" play_album", num_tracks=chosen_tracks or 0)
                                log(f"play_album: queued (queue_only) at position {pos} [{_qa_placement}]")
                            except Exception:
                                pos = _enqueue()
                                log(f"play_album: queued (queue_only, append) at position {pos}")
                        else:
                            # Play now: insert after current, then skip to it.
                            # NOTE: play_from_queue uses the RETURNED pos, so playback is
                            # correct even when placement degrades — the queue layout is
                            # what suffers, and the placement field reports it honestly.
                            try:
                                info = coordinator.get_current_track_info()
                                current_pos = int(info.get('playlist_position', 0))
                                insert_pos = current_pos + 1
                                _qo_pos_req = insert_pos
                                pos, _qa_placement = _verified_queue_add(
                                    coordinator, lambda: _enqueue(insert_pos), insert_pos,
                                    label=" play_album", num_tracks=chosen_tracks or 0)
                                log(f"play_album: inserted at position {pos} [{_qa_placement}], skipping to it")
                                coordinator.play_from_queue(pos - 1)  # 0-indexed
                            except Exception:
                                pos = _enqueue()
                                log(f"play_album: appended at position {pos}, playing from there")
                                coordinator.play_from_queue(pos - 1)

                        # v2.60 queue sources (preview enrichment): record what this verb
                        # did to the queue — replace RESETS the chain, inserts APPEND.
                        # Runs for all three modes; provenance below stays play-now-only.
                        _qsrc_uri = spotify_album_uri or share_link_url or getattr(album_item, "uri", "") or ""
                        if replace_q:
                            _reset_queue_sources(coordinator.player_name,
                                [{"type": "album", "name": chosen_title or album_title, "uri": _qsrc_uri}],
                                "play_album replace")
                        else:
                            # v2.62 insert-range: play_album knows its exact receipt —
                            # verified landing slot (pos) + chosen_tracks count.
                            try:
                                _pa_pos = int(pos) if pos else None
                            except (TypeError, ValueError):
                                _pa_pos = None
                            _append_queue_source(coordinator.player_name, "album",
                                                 chosen_title or album_title, _qsrc_uri,
                                                 pos_start=_pa_pos, num_tracks=chosen_tracks or None)

                        if not queue_only:
                            _enforce_repeat_default(coordinator, cmd, rooms[0] if rooms else "")  # v2.48.5 house rule
                            # v2.55: queue provenance — "play now" makes this album the
                            # active listening context (queue_only append deliberately
                            # does NOT clobber existing provenance: the old context still
                            # dominates the queue until the album actually plays).
                            _prov_uri = spotify_album_uri or share_link_url or getattr(album_item, "uri", "") or ""
                            _set_queue_provenance(coordinator.player_name, _prov_uri,
                                                  chosen_title or album_title, "album")
                            # v2.59 C3 L1: our add went in via AddURIToQueue, which does
                            # NOT rewrite AVTransport's EnqueuedTransportURI — Sonos will
                            # keep reporting the PREVIOUS load's container while this
                            # album plays. Record that now-stale URI so capture suppresses
                            # it and the provenance overlay (just set above) stamps the
                            # truth. expected_album is the false-positive killer: when the
                            # album ends and the old queue resumes, the album no longer
                            # matches and L1 releases (T-C3.4). NOTE: must run AFTER
                            # _set_queue_provenance (which clears markers for this coord).
                            try:
                                _stale_enq = _read_enqueued_uri(coordinator)
                                if (_stale_enq and not _stale_enq.startswith("x-rincon-queue:")
                                        and _stale_enq != _prov_uri):
                                    _set_stale_enqueued(coordinator.player_name, _stale_enq,
                                                        "insert_album",
                                                        expected_album=chosen_title or album_title)
                                else:
                                    log(f"play_album: no stale Enqueued to mark (enq='{_stale_enq[:60]}')")
                            except Exception as _se_err:
                                log(f"play_album: stale-Enqueued marker set failed (benign): {_se_err}")

                        room_label = " + ".join(rooms) if len(rooms) > 1 else rooms[0]
                        grp_note = f" (unlinked from {', '.join(was_grouped)})" if was_grouped else ""
                        mode = "Replaced queue with" if replace_q else ("Queued" if queue_only else "Playing")
                        tracks_note = f" ({chosen_tracks} tracks)" if chosen_tracks else ""
                        # v2.54 B1: honest placement reporting
                        _pl_note = ""
                        if _qa_placement == "reordered":
                            _pl_note = " [misfiled, recovered by reorder]"
                        elif _qa_placement == "degraded":
                            _pl_note = f" [WARNING: landed at queue slot {pos}]"
                        if _rq_trim_failed:
                            _pl_note += " [WARNING: old queue rows not removed — will be swept by next replace/clear]"
                        result["success"] = True
                        result["message"] = f"{mode} album '{chosen_title}' by {chosen_artist}{tracks_note} in {room_label}{grp_note}{_pl_note}"
                        result["data"] = {
                            "title": chosen_title,
                            "artist": chosen_artist,
                            "tracks_count": chosen_tracks,
                            "album_id": chosen_id,
                            "service": "Spotify" if spotify_share_url else svc_name,
                            "room": rooms[0],
                            "rooms": rooms,
                            "queue_only": queue_only,
                            "replace": replace_q,           # v2.57
                            "trim_failed": _rq_trim_failed, # v2.57 E12
                            "position": pos,
                            "queued_at": pos,
                            "placement": _qa_placement,
                        }
                        # v2.58 Phase B: honesty line + result fields on conversion
                        if _b_converted_from:
                            result["data"]["converted_from"] = _b_converted_from
                            result["data"]["queue_age"] = _b_queue_age
                            result["message"] += f" [queue was stale ({_b_queue_age}) -- replaced instead of inserted]"
                        # v2.58 A7: one [queue-op] line + coordinator/group/queue
                        # shape fields in the result payload (never raises)
                        try:
                            _qo_after = int(coordinator.queue_size)
                        except Exception:
                            _qo_after = None
                        _qo_verb = ("play_album->replace_queue" if _b_converted_from else
                                    ("play_album(replace)" if replace_q else
                                     ("play_album(queue_only)" if queue_only else "play_album")))
                        result["data"].update(_queue_op_log(
                            _qo_verb, rooms[0] if rooms else "?", coordinator,
                            queue_before=_qo_before, queue_after=_qo_after,
                            pos_requested=_qo_pos_req, pos_landed=pos))
                        # v2.57: refresh queue_summary in the state file after any
                        # queue mutation (level-triggered; queue_only adds don't
                        # change the track, so the poll loop won't push otherwise).
                        try:
                            schedule_state_push()
                        except Exception:
                            pass
                    except Exception as q_err:
                        log(f"play_album: add_to_queue failed: {q_err}")
                        meta = getattr(album_item, "to_didl_string", lambda: "n/a")()
                        log(f"play_album: DIDL: {meta[:500]}")
                        result["message"] = f"add_to_queue failed for '{chosen_title}': {q_err}"

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
                # v2.52.2: this heavy container insert needs the same armor as play_next:
                # 30s mutation timeout + verify-after-timeout (Shed Arc timed out at 5s).
                with _queue_mutation_timeout():
                    dev.clear_queue()
                plugin    = ShareLinkPlugin(dev)
                try:
                    with _queue_mutation_timeout():
                        plugin.add_share_link_to_queue(share_url)
                except Exception as add_err:
                    log(f"play_spotify_uri: add raised ({add_err}); verifying whether insert landed...")
                    time.sleep(2.0)
                    try:
                        q_after = int(dev.queue_size)
                    except Exception:
                        q_after = 0
                    if q_after > 0:
                        log(f"play_spotify_uri: verify OK -- queue has {q_after} items after timeout; continuing")
                    else:
                        raise
                with _queue_mutation_timeout():
                    dev.play_from_queue(0)
                # v2.58 Phase B: fresh queue load -> freshen the stale-guard stamp
                _touch_queue(dev.player_name, "play_spotify_uri queue load")
                # v2.55: queue provenance — this queue now came from this container.
                # Playlists/albums get remembered (session naming); a bare track
                # replaces the queue with no container, so clear instead.
                if uri_type in ("playlist", "album"):
                    _set_queue_provenance(dev.player_name, spotify_uri, title, uri_type)
                    # v2.60 queue sources: fresh load -> chain restarts at this container
                    _reset_queue_sources(dev.player_name,
                        [{"type": uri_type, "name": title, "uri": spotify_uri}],
                        "play_spotify_uri load")
                else:
                    _clear_queue_provenance(dev.player_name, "queue replaced by single track")
                    _reset_queue_sources(dev.player_name,
                        [{"type": "tracks", "count": 1}], "play_spotify_uri single track")
                # Set play mode: shuffle + repeat controlled independently
                shuffle = cmd.get("shuffle", False)
                repeat = cmd.get("repeat", False)  # v2.45: default False (house rule: repeat off unless requested)
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
            q_artist    = cmd.get("artist", "")
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
                    # DESIGN NOTE: + button default is insert at next position.
                    # User expectation: "play this next" -- never silently append.
                    # v2.54 rider: explicit mode param — "next" (default) | "end".
                    # Page sends mode explicitly; absent mode keeps old behavior.
                    as_next = (str(cmd.get("mode", "next")).lower() != "end")
                    # v2.57 A1: log the mode on EVERY add, flagging defaulted mode.
                    # P0-F2: no page callsite passed mode before the queue sheet —
                    # every legacy "+" was silently insert-next. Explicit mode from
                    # the sheet ends the ambiguity; this log line proves which.
                    log(f"add_to_queue: mode={'next' if as_next else 'end'}"
                        f"{' (DEFAULTED — caller sent no mode)' if 'mode' not in cmd else ' (explicit)'}")

                    def _do_queue(coord, spotify, pos=None, next_flag=False):
                        """Queue a track -- Spotify via ShareLinkPlugin, others via add_uri_to_queue with DIDL.
                        v2.54 B1: RETURNS the SoCo return value (FirstTrackNumberEnqueued)
                        so _verified_queue_add can check actual placement."""
                        if spotify:
                            plugin = ShareLinkPlugin(coord)
                            if pos is not None:
                                return plugin.add_share_link_to_queue(share_url, position=pos)
                            elif next_flag:
                                return plugin.add_share_link_to_queue(share_url, as_next=True)
                            else:
                                return plugin.add_share_link_to_queue(share_url)
                        else:
                            # [DESIGN NOTE] Raw Sonos URI (Qobuz, Apple Music, etc.)
                            # Same DIDL-Lite approach as play_next -- proper item IDs for title display
                            from xml.sax.saxutils import escape as xml_escape
                            safe_title = xml_escape(title or "Unknown Track")
                            safe_uri = xml_escape(track_uri)
                            safe_artist = xml_escape(q_artist) if q_artist else ""
                            creator_tag = ('<dc:creator>' + safe_artist + '</dc:creator>') if safe_artist else ''
                            meta = (
                                '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
                                'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                                'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
                                'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
                                '<item id="R:0/0/0" parentID="R:0/0" restricted="true">'
                                '<dc:title>' + safe_title + '</dc:title>'
                                + creator_tag +
                                '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
                                '<res protocolInfo="*:*:*:*">' + safe_uri + '</res>'
                                '</item></DIDL-Lite>'
                            )
                            log(f"add_to_queue: DIDL meta for non-Spotify URI: {track_uri[:80]} artist={q_artist}")
                            if pos is not None:
                                return coord.add_uri_to_queue(uri=track_uri, didl_resource_meta_data=meta, position=pos)
                            elif next_flag:
                                return coord.add_uri_to_queue(uri=track_uri, didl_resource_meta_data=meta, as_next=True)
                            else:
                                return coord.add_uri_to_queue(uri=track_uri, didl_resource_meta_data=meta)

                    # v2.54 B1: every add goes through _verified_queue_add — this exact
                    # branch produced the 2026-07-29 Gravity's Angel misfile (appended to
                    # a stale queue tail while reporting success).
                    _qa_actual, _qa_placement = None, "direct"
                    _qo_pos_req = None
                    _b_converted_from, _b_queue_age, _b_trim_failed = None, "", False
                    # v2.58: read transport + queue shape BEFORE mutating — the
                    # stale-guard and the A1 auto-play fix both need the pre-state.
                    try:
                        _pre_state = coordinator.get_current_transport_info().get('current_transport_state', '')
                    except Exception as _ts_err:
                        log(f"add_to_queue: pre-add transport read did not succeed ({_ts_err}); treating as not-stopped")
                        _pre_state = ""
                    try:
                        _qo_before = int(coordinator.queue_size)
                    except Exception:
                        _qo_before = None
                    # v2.58 Phase B: STALE-QUEUE GUARD — insert-next mode on a
                    # STOPPED coordinator with a stale (>24h or unknown-age) queue
                    # converts to a full REPLACE via the proven load-then-trim.
                    # End/append mode is EXEMPT per D2 (locked): stays literal.
                    if as_next and _pre_state in ('STOPPED', 'NO_MEDIA_PRESENT') and (_qo_before or 0) > 0:
                        _sg_stale, _sg_age = _queue_is_stale(coordinator.player_name)
                        if _sg_stale:
                            log(f"[stale-guard] add_to_queue: {coordinator.player_name} stopped + queue stale ({_sg_age}, {_qo_before} rows) -- converting insert to REPLACE")
                            _b_converted_from, _b_queue_age = "add_to_queue", _sg_age
                        else:
                            log(f"[stale-guard] add_to_queue: {coordinator.player_name} stopped, queue fresh ({_sg_age}) -- literal insert")
                    if _b_converted_from:
                        # Conversion path: reuse replace_queue's proven internals
                        # (_load_then_trim appends, trims old rows, and PLAYS the
                        # new content — G2: auto-play starts the INSERTED track).
                        _qa_actual, _qa_placement, _b_trim_failed = _load_then_trim(
                            coordinator, lambda: _do_queue(coordinator, is_spotify),
                            label=" add_to_queue(stale->replace)")
                        if is_spotify and uri_type in ("album", "playlist"):
                            _set_queue_provenance(coordinator.player_name, track_uri, title, uri_type)
                            # v2.60 queue sources: conversion = replace -> chain restarts
                            _reset_queue_sources(coordinator.player_name,
                                [{"type": uri_type, "name": title, "uri": track_uri}],
                                "add_to_queue stale->replace")
                        else:
                            _clear_queue_provenance(coordinator.player_name, "stale queue replaced by add_to_queue")
                            _reset_queue_sources(coordinator.player_name,
                                [{"type": "tracks", "count": 1}], "add_to_queue stale->replace")
                        verb = "Replaced stale queue; playing"
                    elif as_next:
                        try:
                            info = coordinator.get_current_track_info()
                            current_pos = int(info.get('playlist_position', 0))
                            insert_pos = current_pos + 1
                            _qo_pos_req = insert_pos
                            log(f"Queueing as NEXT at position {insert_pos} (current={current_pos})")
                            _qa_actual, _qa_placement = _verified_queue_add(
                                coordinator, lambda: _do_queue(coordinator, is_spotify, pos=insert_pos),
                                insert_pos, label=" add_to_queue")
                        except Exception as pos_err:
                            log(f"Position-based queue failed ({pos_err}), falling back to as_next flag")
                            _qa_actual, _qa_placement = _verified_queue_add(
                                coordinator, lambda: _do_queue(coordinator, is_spotify, next_flag=True),
                                None, label=" add_to_queue")
                    else:
                        _qa_actual, _qa_placement = _verified_queue_add(
                            coordinator, lambda: _do_queue(coordinator, is_spotify),
                            None, label=" add_to_queue")
                    if not _b_converted_from:
                        # Auto-play if nothing was playing before the add
                        if _pre_state in ('STOPPED', 'NO_MEDIA_PRESENT'):
                            queue_size = coordinator.queue_size
                            if queue_size > 0:
                                # v2.58 A1 FIX (P1 finding #1): play the track we JUST
                                # ADDED (verified landed slot _qa_actual), NOT
                                # queue_size-1 — the old code started the TAIL of
                                # whatever stale queue existed.
                                _ap_slot = int(_qa_actual) if _qa_actual else queue_size
                                log(f"add_to_queue: transport {_pre_state} -> auto-playing landed slot {_ap_slot} of {queue_size} (A1: inserted track, not tail)")
                                coordinator.play_from_queue(_ap_slot - 1)
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
                    # v2.54 B1: surface actual placement — degraded must never look green
                    result["data"]["queued_at"] = _qa_actual
                    result["data"]["placement"] = _qa_placement
                    result["success"] = True
                    # v2.60 queue sources: non-conversion adds APPEND to the chain
                    # (the stale->replace branch already RESET it at replace time).
                    if not _b_converted_from:
                        if is_spotify and uri_type in ("album", "playlist"):
                            # v2.62 insert-range: playback (if any) already started above,
                            # so the expansion wait never delays the audible verb. Growth
                            # vs _qo_before = exactly how many rows this container owns.
                            _aq_n = (_expansion_count(coordinator, _qo_before, label=" add_to_queue")
                                     if isinstance(_qo_before, int) else None)
                            _append_queue_source(coordinator.player_name, uri_type, title, track_uri,
                                                 pos_start=_qa_actual, num_tracks=_aq_n)
                        else:
                            _append_queue_source(coordinator.player_name, "track")
                    _pl_note = ""
                    if _qa_placement == "reordered":
                        _pl_note = " [misfiled, recovered by reorder]"
                    elif _qa_placement == "degraded":
                        _pl_note = f" [WARNING: landed at queue slot {_qa_actual}, reorder failed]"
                    # v2.58 Phase B: honesty line + result fields on conversion
                    if _b_converted_from:
                        result["data"]["converted_from"] = _b_converted_from
                        result["data"]["queue_age"] = _b_queue_age
                        result["data"]["trim_failed"] = _b_trim_failed
                        _pl_note += f" [queue was stale ({_b_queue_age}) -- replaced instead of inserted]"
                        if _b_trim_failed:
                            _pl_note += " [WARNING: old queue rows not removed -- will be swept by next replace/clear]"
                    # v2.58 A7: [queue-op] line + coordinator/group fields (never raises)
                    result["data"].update(_queue_op_log(
                        "add_to_queue->replace_queue" if _b_converted_from else
                        ("add_to_queue(next)" if as_next else "add_to_queue(end)"),
                        room, coordinator, transport_state=_pre_state,
                        queue_before=_qo_before,
                        queue_after=result["data"].get("queue_size"),
                        pos_requested=_qo_pos_req, pos_landed=_qa_actual))
                    result["message"] = f"{verb} '{title}' in {room} (queue: {result['data'].get('queue_size', '?')} items){_pl_note}"
                    # v2.57: queue mutated without a track change — refresh queue_summary
                    try:
                        schedule_state_push()
                    except Exception:
                        pass
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
            cmd_artist  = cmd.get("artist", "")
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

                def _build_didl_meta(t, u, a=""):
                    """Build DIDL-Lite XML metadata for non-Spotify URIs.
                    Uses proper item IDs (R:0/0/0) so Sonos displays title/artist correctly."""
                    creator = ('<dc:creator>' + xml_escape(a) + '</dc:creator>') if a else ''
                    return (
                        '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
                        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                        'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
                        'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
                        '<item id="R:0/0/0" parentID="R:0/0" restricted="true">'
                        '<dc:title>' + xml_escape(t or "Unknown Track") + '</dc:title>'
                        + creator +
                        '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
                        '<res protocolInfo="*:*:*:*">' + xml_escape(u) + '</res>'
                        '</item></DIDL-Lite>'
                    )

                try:
                    coordinator = dev.group.coordinator if dev.group and dev.group.coordinator else dev
                    # Check if current source is a stream (no queue to insert into)
                    # v2.52.1: x-sonos-vli = live session source (Spotify Connect / AirPlay).
                    # Queue inserts are invisible on it and next() skips the PHONE's session
                    # instead of our queue (2026-07-20: played Walking On The Moon instead of
                    # 2 Klaxons). Treat it as a stream -> full play takes the transport back.
                    # v2.57: prefix list hoisted to module-level STREAM_URI_PREFIXES
                    # (shared with _build_queue_summary).
                    is_stream = False
                    try:
                        media_info = coordinator.avTransport.GetMediaInfo([('InstanceID', 0)])
                        current_uri = media_info.get('CurrentURI', '') or ''
                        is_stream = any(current_uri.lower().startswith(p) for p in STREAM_URI_PREFIXES)
                        if is_stream:
                            log(f"play_next: stream detected ({current_uri[:60]}), using full play instead of queue insert")
                    except Exception as mi_err:
                        log(f"play_next: GetMediaInfo failed ({mi_err}), assuming queue-based")

                    def _add_to_queue(coord, pos=None):
                        """Add track to queue -- Spotify via ShareLinkPlugin, others via add_uri_to_queue.
                        v2.54 B1: RETURNS FirstTrackNumberEnqueued for placement verification."""
                        if is_spotify:
                            plugin = ShareLinkPlugin(coord)
                            if pos is not None:
                                return plugin.add_share_link_to_queue(share_url, position=pos)
                            else:
                                return plugin.add_share_link_to_queue(share_url)
                        else:
                            meta = _build_didl_meta(title, track_uri, cmd_artist)
                            log(f"play_next: DIDL meta for non-Spotify URI: {track_uri[:80]} artist={cmd_artist}")
                            if pos is not None:
                                return coord.add_uri_to_queue(uri=track_uri, didl_resource_meta_data=meta, position=pos)
                            else:
                                return coord.add_uri_to_queue(uri=track_uri, didl_resource_meta_data=meta, as_next=True)

                    def _add_with_verify(coord, pos=None, before_size=None):
                        """v2.52: run the queue add under the 30s mutation timeout; if it
                        still raises, VERIFY whether the insert actually landed before
                        declaring failure (2026-07-20 Garage postmortem: the insert
                        succeeded server-side after our 5s hangup — silence + green toast).
                        v2.54 B1: also verifies PLACEMENT via _verified_queue_add.
                        Returns (actual_pos, placement) if the add landed; re-raises otherwise.
                        placement "unverified" = timeout-recovery path (size grew but slot unknown)."""
                        try:
                            with _queue_mutation_timeout():
                                return _verified_queue_add(coord, lambda: _add_to_queue(coord, pos),
                                                           pos, label=" play_next")
                        except Exception as add_err:
                            log(f"play_next: add raised ({add_err}); verifying whether insert landed...")
                            time.sleep(2)
                            try:
                                after = coord.queue_size
                            except Exception as qs_err:
                                log(f"play_next: queue_size verify failed ({qs_err}); re-raising original error")
                                raise add_err
                            if before_size is not None and after > before_size:
                                log(f"play_next: insert VERIFIED landed (queue {before_size} -> {after}); continuing to play")
                                return None, "unverified"
                            log(f"play_next: insert NOT found (queue {before_size} -> {after}); re-raising")
                            raise add_err

                    # v2.52: whole-playlist play REPLACES the queue (user decision 2026-07-20).
                    # Avoids multi-second re-index inserts into huge queues and endless queue bloat.
                    is_playlist_container = is_spotify and uri_type == "playlist"

                    _qa_actual, _qa_placement = None, "direct"  # v2.54 B1
                    # v2.58: Phase B conversion + A7 transparency tracking
                    _b_converted_from, _b_queue_age, _b_trim_failed = None, "", False
                    _qo_before, _qo_pos_req, _pre_state = None, None, ""
                    _pn_src_pending = None  # v2.62 insert-range: deferred container bookkeeping
                    if is_playlist_container:
                        try:
                            _q_before = coordinator.queue_size
                        except Exception:
                            _q_before = "?"
                        log(f"play_next: playlist container -> REPLACE queue (was {_q_before} tracks)")
                        with _queue_mutation_timeout():
                            coordinator.clear_queue()
                        _qa_actual, _qa_placement = _add_with_verify(coordinator, before_size=0)
                        coordinator.play_from_queue(0)
                        # v2.59.1: queue provenance — the playlist is now the active
                        # listening context. This branch predates v2.55 and never set
                        # it (2026-08-06 wes-alumni incident: a 2.5h-old play_album
                        # provenance pointer survived the replace and the overlay
                        # stamped a stale Apple Music album onto all 5 playlist plays).
                        # Mirrors play_spotify_uri / replace_queue container semantics;
                        # _set_queue_provenance also clears stale-Enqueued markers.
                        _set_queue_provenance(coordinator.player_name, track_uri, title, uri_type)
                        # v2.60 queue sources: playlist replace -> chain restarts
                        _reset_queue_sources(coordinator.player_name,
                            [{"type": uri_type, "name": title, "uri": track_uri}],
                            "play_next playlist replace")
                    elif is_stream:
                        # Stream active -- can't insert into queue; replace the stream
                        if is_spotify:
                            # v2.58 A2 FIX (finding #5): the old path cleared the ENTIRE
                            # queue to take the transport back from a Spotify Connect /
                            # AirPlay session (x-sonos-vli) -- destructive to a queue the
                            # user may want back. Now: LOAD-THEN-TRIM (the proven
                            # replace_queue pattern): append, play, then trim -- an add
                            # failure leaves the old queue fully intact.
                            _qa_actual, _qa_placement, _pn_trim_failed = _load_then_trim(
                                coordinator, lambda: _add_to_queue(coordinator),
                                label=" play_next(stream)")
                            if _pn_trim_failed:
                                log("play_next: stream takeover trim failed -- old rows remain above the new track (honest WARN, will be swept by next replace/clear)")
                            # v2.59.1 sibling fix (Rule 10 sweep with the playlist-REPLACE
                            # branch above): stream takeover replaces the queue content,
                            # so the old container is no longer the context. Albums keep
                            # provenance; a single track wipes it (play_spotify_uri
                            # semantics, v2.55). Playlists can't reach here (handled by
                            # is_playlist_container above).
                            if uri_type in ("album", "playlist"):
                                _set_queue_provenance(coordinator.player_name, track_uri, title, uri_type)
                                # v2.60 queue sources: stream takeover replaced the queue
                                _reset_queue_sources(coordinator.player_name,
                                    [{"type": uri_type, "name": title, "uri": track_uri}],
                                    "play_next stream takeover")
                            else:
                                _clear_queue_provenance(coordinator.player_name, "stream takeover by single track")
                                _reset_queue_sources(coordinator.player_name,
                                    [{"type": "tracks", "count": 1}], "play_next stream takeover")
                        else:
                            # Non-Spotify (Qobuz, Apple Music, etc.): play_uri() is more reliable
                            # than clear_queue + DIDL + play_from_queue which can silently fail
                            # (v1.87 fix: DIDL queue approach showed "Song [1/1]" with no audio)
                            meta = _build_didl_meta(title, track_uri, cmd_artist)
                            log(f"play_next: using play_uri() for non-Spotify stream replacement artist={cmd_artist}")
                            coordinator.play_uri(track_uri, meta, title=title or '')
                    else:
                        # Queue-based source -- insert at next position and skip
                        try:
                            _q_before = coordinator.queue_size
                        except Exception:
                            _q_before = None
                        _qo_before = _q_before
                        # v2.58 Phase B: STALE-QUEUE GUARD -- play_next on a STOPPED
                        # coordinator with a stale (>24h or unknown-age) queue converts
                        # to a full REPLACE (load-then-trim), so the track can never
                        # land inside forgotten leftovers (the exact geometry of the
                        # 2026-08-03 El Bueno -> Sister Sledge incident). Playing or
                        # stopped-but-fresh queues keep literal insert semantics.
                        try:
                            _pre_state = coordinator.get_current_transport_info().get('current_transport_state', '')
                        except Exception as _ts_err:
                            log(f"play_next: pre-insert transport read did not succeed ({_ts_err}); treating as not-stopped")
                            _pre_state = ""
                        if _pre_state in ('STOPPED', 'NO_MEDIA_PRESENT') and (_q_before or 0) > 0:
                            _sg_stale, _sg_age = _queue_is_stale(coordinator.player_name)
                            if _sg_stale:
                                log(f"[stale-guard] play_next: {coordinator.player_name} stopped + queue stale ({_sg_age}, {_q_before} rows) -- converting insert to REPLACE")
                                _b_converted_from, _b_queue_age = "play_next", _sg_age
                            else:
                                log(f"[stale-guard] play_next: {coordinator.player_name} stopped, queue fresh ({_sg_age}) -- literal insert")
                        if _b_converted_from:
                            # Conversion: reuse replace_queue's proven internals
                            # (append -> trim old rows -> play the new content).
                            # NOTE: pos is passed EXPLICITLY (queue_size+1 = append)
                            # because _add_to_queue(pos=None) uses as_next=True for
                            # non-Spotify URIs -- a mid-queue insert would fall
                            # inside the trim range and be removed with the old rows.
                            _qa_actual, _qa_placement, _b_trim_failed = _load_then_trim(
                                coordinator, lambda: _add_to_queue(coordinator, pos=coordinator.queue_size + 1),
                                label=" play_next(stale->replace)")
                            if is_spotify and uri_type in ("album", "playlist"):
                                _set_queue_provenance(coordinator.player_name, track_uri, title, uri_type)
                                # v2.60 queue sources: conversion = replace -> chain restarts
                                _reset_queue_sources(coordinator.player_name,
                                    [{"type": uri_type, "name": title, "uri": track_uri}],
                                    "play_next stale->replace")
                            else:
                                _clear_queue_provenance(coordinator.player_name, "stale queue replaced by play_next")
                                _reset_queue_sources(coordinator.player_name,
                                    [{"type": "tracks", "count": 1}], "play_next stale->replace")
                        else:
                            info = coordinator.get_current_track_info()
                            current_pos = int(info.get('playlist_position', 0))
                            insert_pos = current_pos + 1
                            _qo_pos_req = insert_pos
                            log(f"play_next: inserting at position {insert_pos} (current={current_pos}, queue_size={_q_before})")
                            _qa_actual, _qa_placement = _add_with_verify(coordinator, pos=insert_pos, before_size=_q_before)
                            # v2.59 C3 L1/Q6: queue inserts don't rewrite EnqueuedTransportURI,
                            # so capture will keep seeing the previous load's container.
                            # - Spotify ALBUM insert-play -> "insert_album" marker (same
                            #   geometry as play_album play-now; expected_album releases
                            #   L1 when the old queue resumes, T-C3.4).
                            # - Single track (spotify track or raw Sonos URI) ->
                            #   "inserted_track" marker (Q6, signed off): the injected
                            #   one-off gets NO container and NO provenance overlay —
                            #   honest no-context instead of the surrounding playlist.
                            #   Marker set even when Enqueued is empty/queue-typed: the
                            #   flag must still block the overlay for the injected track.
                            try:
                                _stale_enq = _read_enqueued_uri(coordinator)
                                if is_spotify and uri_type == "album":
                                    if (_stale_enq and not _stale_enq.startswith("x-rincon-queue:")
                                            and _stale_enq != track_uri):
                                        _set_stale_enqueued(coordinator.player_name, _stale_enq,
                                                            "insert_album",
                                                            expected_album=cmd.get("album") or title)
                                    else:
                                        log(f"play_next: no stale Enqueued to mark (enq='{_stale_enq[:60]}')")
                                else:
                                    _set_stale_enqueued(coordinator.player_name, _stale_enq,
                                                        "inserted_track",
                                                        expected_album=cmd.get("album", ""),
                                                        expected_uri=track_uri)
                            except Exception as _se_err:
                                log(f"play_next: stale-Enqueued marker set failed (benign): {_se_err}")
                            # v2.60 queue sources: literal insert APPENDS to the chain.
                            # v2.62: container appends DEFER to after playback start so
                            # the expansion wait (range receipt) never delays audio.
                            if is_spotify and uri_type in ("album", "playlist"):
                                _pn_src_pending = (uri_type, title, track_uri,
                                                   _q_before if isinstance(_q_before, int) else None)
                            else:
                                _append_queue_source(coordinator.player_name, "track")
                        if _b_converted_from:
                            pass  # playback already started by _load_then_trim
                        elif _qa_placement == "degraded" and _qa_actual:
                            # v2.54 B1: misfiled AND reorder failed — next() would play
                            # whatever occupies insert_pos, not our track. Play from the
                            # slot where the track ACTUALLY landed.
                            log(f"play_next: degraded placement -> play_from_queue({_qa_actual - 1})")
                            coordinator.play_from_queue(_qa_actual - 1)
                        else:
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
                                # Any next() failure -- play the track we just inserted.
                                # v2.46 fix: was play_from_queue(0), which played the HEAD of
                                # the existing (possibly stale/foreign) queue instead of the
                                # inserted track. Cold speakers reject next() with UPnP 701,
                                # so this path fired and played leftover queue content.
                                log(f"play_next: next() failed ({skip_err}), falling back to play_from_queue({insert_pos - 1})")
                                coordinator.play_from_queue(insert_pos - 1)
                        # v2.62 insert-range: playback is rolling — now settle the
                        # deferred container bookkeeping with its verified receipt.
                        if _pn_src_pending:
                            _pn_type, _pn_title, _pn_uri, _pn_before = _pn_src_pending
                            _pn_n = (_expansion_count(coordinator, _pn_before, label=" play_next")
                                     if _pn_before is not None else None)
                            _append_queue_source(coordinator.player_name, _pn_type, _pn_title, _pn_uri,
                                                 pos_start=_qa_actual, num_tracks=_pn_n)
                    _enforce_repeat_default(coordinator, cmd, rooms[0] if rooms else "")  # v2.48.5 house rule

                    # v2.37: Cache metadata for non-Spotify URIs so get_track_info()
                    # can recover title/artist when Sonos DIDL comes back empty.
                    if not is_spotify and (title or cmd_artist):
                        _uri_metadata_cache[track_uri] = {
                            "title": title or "",
                            "artist": cmd_artist or "",
                            "album": cmd.get("album", "") or "",
                            "ts": time.time(),
                        }
                        log(f"play_next: cached metadata for {track_uri[:80]}: '{title}' - {cmd_artist}")

                    room_label = " + ".join(rooms) if len(rooms) > 1 else rooms[0]
                    grp_note = f" (unlinked from {', '.join(was_grouped)})" if was_grouped else ""
                    result["success"] = True
                    mode_note = "queue replace (playlist)" if is_playlist_container else ("full play (was stream)" if is_stream else ("queue replace (stale)" if _b_converted_from else "queue insert"))
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

                    # v2.54 B1: surface placement — degraded must never look green
                    _pl_note = ""
                    if _qa_placement == "reordered":
                        _pl_note = " [misfiled, recovered by reorder]"
                    elif _qa_placement == "degraded":
                        _pl_note = f" [WARNING: landed at queue slot {_qa_actual}]"
                    # v2.58 Phase B: honesty line on stale-queue conversion
                    if _b_converted_from:
                        _pl_note += f" [queue was stale ({_b_queue_age}) -- replaced instead of inserted]"
                        if _b_trim_failed:
                            _pl_note += " [WARNING: old queue rows not removed -- will be swept by next replace/clear]"
                    result["message"] = f"Playing next: '{title}' in {room_label} [{mode_note}, {svc_note}]{grp_note}{_pl_note}"
                    result["data"] = {"title": title, "uri": track_uri, "share_url": share_url or track_uri,
                                      "was_grouped_with": was_grouped, "room": rooms[0], "rooms": rooms,
                                      "queued_at": _qa_actual, "placement": _qa_placement}
                    if _b_converted_from:
                        result["data"]["converted_from"] = _b_converted_from
                        result["data"]["queue_age"] = _b_queue_age
                        result["data"]["trim_failed"] = _b_trim_failed
                    # v2.58 A7: [queue-op] line + coordinator/group fields (never raises)
                    try:
                        _qo_after = int(coordinator.queue_size)
                    except Exception:
                        _qo_after = None
                    result["data"].update(_queue_op_log(
                        "play_next->replace_queue" if _b_converted_from else
                        ("play_next(playlist-replace)" if is_playlist_container else
                         ("play_next(stream)" if is_stream else "play_next")),
                        rooms[0] if rooms else "?", coordinator, transport_state=_pre_state,
                        queue_before=_qo_before, queue_after=_qo_after,
                        pos_requested=_qo_pos_req, pos_landed=_qa_actual))
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
                        # v2.44 CRITICAL FIX: retire the previously-playing track to history
                        # BEFORE overwriting room_state. Previously every commanded track
                        # change silently dropped one history row.
                        try:
                            _prev = room_state.get(coord_name)
                            if _prev and _prev.get("track_key") and _prev.get("started_at") and _prev["track_key"] != coord_key:
                                log(f"[history] play_next retiring previous track on {coord_name}: {_prev['track_key'][:80]}")
                                post_history(_prev["track_info"], coord_name, _prev["started_at"], datetime.now(timezone.utc))
                        except Exception as _re_err:
                            log(f"[history] WARNING: play_next retire failed: {_re_err}")
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
                    # v2.48.5: was hardcoded NORMAL; helper also honors explicit
                    # cmd['shuffle']/cmd['repeat'] (radio default: no shuffle, no repeat)
                    if cmd.get("shuffle") is None:
                        cmd["shuffle"] = False  # radio queues play in built order by default
                    _enforce_repeat_default(dev, cmd, rooms[0] if rooms else "")
                    dev.play_from_queue(0)
                    result["success"] = True
                    # v2.44: was f"...in {room}" -- NameError ('room' undefined) corrupted
                    # every play_radio result message and fired post_error despite success.
                    radio_room_label = " + ".join(rooms) if rooms else dev.player_name
                    result["message"] = f"Playing radio ({added} tracks) in {radio_room_label}: {title}"
                    result["data"] = {"title": title, "queued": added, "room": rooms[0] if rooms else dev.player_name, "rooms": rooms}
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
                _enforce_repeat_default(dev, cmd, rooms[0] if rooms else "")  # v2.48.5 house rule
                # v2.37: Cache metadata so polling can recover it when DIDL is empty
                if not uri.startswith("x-sonos-spotify:") and not uri.startswith("spotify:"):
                    _uri_metadata_cache[uri] = {
                        "title": title or "",
                        "artist": cmd.get("artist", "") or "",
                        "album": cmd.get("album", "") or "",
                        "ts": time.time(),
                    }
                    log(f"play_uri: cached metadata for {uri[:80]}: '{title}'")
                result["success"] = True
                room_label = " + ".join(rooms) if len(rooms) > 1 else rooms[0]
                result["message"] = f"Playing '{title}' in {room_label}"
                result["data"] = {"title": title, "uri": uri, "room": rooms[0], "rooms": rooms}

        elif action == "stop":
            # v2.45: accept "room" (singular) alias
            rooms = cmd.get("rooms") or ([cmd.get("room")] if cmd.get("room") else list(devices.keys()))
            if isinstance(rooms, str): rooms = [rooms]
            stopped = []
            for r in rooms:
                dev = devices.get(r)
                if dev:
                    try: dev.stop(); stopped.append(r)
                    except Exception as e: log(f"stop failed in {r}: {e}")
            if stopped:
                result["success"] = True
                result["message"] = f"Stopped: {', '.join(stopped)}"
            else:
                result["success"] = False
                result["message"] = f"stop: no rooms matched (requested {rooms}, known: {sorted(devices.keys())})"

        elif action == "pause":
            # v2.45: accept "room" (singular) alias
            rooms = cmd.get("rooms") or ([cmd.get("room")] if cmd.get("room") else [])
            if isinstance(rooms, str): rooms = [rooms]
            # Deduplicate by coordinator — one call per group, not per room
            seen_coords = set()
            paused = []
            idle_skips = []  # v2.48.1: rooms where pause() raised (nothing playing)
            for r in rooms:
                dev = devices.get(r)
                if dev:
                    coord = dev.group.coordinator if dev.group else dev
                    if coord.player_name not in seen_coords:
                        seen_coords.add(coord.player_name)
                        try: coord.pause(); paused.append(r)
                        except Exception as e:
                            # UPnP "transition not available" = nothing playing; benign
                            idle_skips.append(r)
                            log(f"pause: {r} had nothing to pause (benign no-op): {e}")
                    else:
                        paused.append(r)
            if paused:
                result["success"] = True
                result["message"] = f"Paused: {', '.join(paused)}"
            elif idle_skips:
                # v2.48.1: pausing an idle room is a successful no-op, not an error.
                # The old "no rooms matched" message was misleading (the room WAS known)
                # and tripped watchdog failed-command audits for a non-problem.
                result["success"] = True
                result["message"] = f"pause: nothing was playing in {', '.join(idle_skips)} — already idle (no-op)"
            else:
                result["success"] = False
                result["message"] = f"pause: no rooms matched (requested {rooms}, known: {sorted(devices.keys())})"

        elif action in ("resume", "play_resume"):
            # v2.45: accept "room" (singular) alias
            rooms = cmd.get("rooms") or ([cmd.get("room")] if cmd.get("room") else [])
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
            if resumed:
                result["success"] = True
                result["message"] = f"Resumed: {', '.join(resumed)}"
            else:
                result["success"] = False
                result["message"] = f"resume: no rooms matched (requested {rooms}, known: {sorted(devices.keys())})"

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

        elif action == "set_repeat":
            # v2.48.5: DETERMINISTIC repeat control (cycle_repeat is a blind 3-way
            # cycle — unusable for sweeps since you can't know the outcome without
            # reading state first). set_repeat is idempotent and fleet-sweep safe.
            # cmd: {"action":"set_repeat", "repeat": false|true|"one",
            #       "rooms": ["Room A", ...] | "all"}   (default: all rooms)
            # Preserves each group's shuffle state. Play mode is a GROUP property,
            # so targets are resolved to coordinators and deduped.
            rep = cmd.get("repeat", False)
            rooms_arg = cmd.get("rooms", "all")
            if isinstance(rooms_arg, str):
                target_rooms = list(devices.keys()) if rooms_arg.lower() == "all" else [rooms_arg]
            else:
                target_rooms = list(rooms_arg) if rooms_arg else list(devices.keys())
            coords, errors = {}, []
            for rname in target_rooms:
                d = devices.get(rname)
                if not d:
                    errors.append(f"{rname}: not found")
                    continue
                try:
                    c = d.group.coordinator if d.group and d.group.coordinator else d
                except Exception:
                    c = d
                coords[c.player_name] = c
            changed, unchanged = [], []
            for cname, c in coords.items():
                try:
                    cur = c.play_mode
                    shuffled = cur in ("SHUFFLE", "SHUFFLE_NOREPEAT", "SHUFFLE_REPEAT_ONE")
                    if rep == "one":
                        new_mode = "SHUFFLE_REPEAT_ONE" if shuffled else "REPEAT_ONE"
                    elif rep:
                        new_mode = "SHUFFLE" if shuffled else "REPEAT_ALL"
                    else:
                        new_mode = "SHUFFLE_NOREPEAT" if shuffled else "NORMAL"
                    if new_mode != cur:
                        c.play_mode = new_mode
                        changed.append(f"{cname}: {cur} -> {new_mode}")
                    else:
                        unchanged.append(cname)
                except Exception as e:
                    errors.append(f"{cname}: {e}")
            result["success"] = not errors
            result["message"] = (f"set_repeat({rep}): {len(changed)} changed, "
                                 f"{len(unchanged)} already correct"
                                 + (f", {len(errors)} errors" if errors else ""))
            result["data"] = {"changed": changed, "unchanged": unchanged, "errors": errors}
            log(f"set_repeat: {result['message']}" + (f" | {changed}" if changed else ""))

        elif action == "set_shuffle":
            # v2.55: DETERMINISTIC shuffle control — idempotent mirror of set_repeat
            # (user request 2026-07-30: shuffle button next to play/pause).
            # cmd: {"action":"set_shuffle", "shuffle": true|false,
            #       "rooms": ["Room A", ...] | "all"}   (default: all rooms)
            # Preserves each group's repeat state. Play mode is a GROUP property,
            # so targets are resolved to coordinators and deduped.
            shuf = bool(cmd.get("shuffle", False))
            rooms_arg = cmd.get("rooms", "all")
            if isinstance(rooms_arg, str):
                target_rooms = list(devices.keys()) if rooms_arg.lower() == "all" else [rooms_arg]
            else:
                target_rooms = list(rooms_arg) if rooms_arg else list(devices.keys())
            coords, errors = {}, []
            for rname in target_rooms:
                d = devices.get(rname)
                if not d:
                    errors.append(f"{rname}: not found")
                    continue
                try:
                    c = d.group.coordinator if d.group and d.group.coordinator else d
                except Exception:
                    c = d
                coords[c.player_name] = c
            changed, unchanged = [], []
            for cname, c in coords.items():
                try:
                    cur = c.play_mode
                    rep_one = cur in ("REPEAT_ONE", "SHUFFLE_REPEAT_ONE")
                    rep_all = cur in ("REPEAT_ALL", "SHUFFLE")
                    if shuf:
                        new_mode = "SHUFFLE_REPEAT_ONE" if rep_one else ("SHUFFLE" if rep_all else "SHUFFLE_NOREPEAT")
                    else:
                        new_mode = "REPEAT_ONE" if rep_one else ("REPEAT_ALL" if rep_all else "NORMAL")
                    if new_mode != cur:
                        c.play_mode = new_mode
                        changed.append(f"{cname}: {cur} -> {new_mode}")
                    else:
                        unchanged.append(cname)
                except Exception as e:
                    errors.append(f"{cname}: {e}")
            result["success"] = not errors
            result["message"] = (f"set_shuffle({shuf}): {len(changed)} changed, "
                                 f"{len(unchanged)} already correct"
                                 + (f", {len(errors)} errors" if errors else ""))
            result["data"] = {"changed": changed, "unchanged": unchanged, "errors": errors}
            log(f"set_shuffle: {result['message']}" + (f" | {changed}" if changed else ""))

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
            # Typically sent by the UI refresh button.
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
                # v2.53: rebuild the poll snapshot NOW from fresh coordinators so the
                # state push below carries current group topology — before this, the
                # snapshot (incl. groups) stayed stale until the next poll cycle and
                # the 🔄 button confirmed with old chip state.
                try:
                    _fresh_coords = []
                    for _d in fresh.values():
                        try:
                            _g = _d.group
                            if _g and _d == _g.coordinator:
                                _fresh_coords.append(_d)
                        except Exception:
                            pass
                    _build_poll_snapshot(_fresh_coords)
                    log(f"[refresh] Snapshot rebuilt: {len(_fresh_coords)} coordinator(s), "
                        f"{len(_poll_snapshot.get('groups', []))} group(s)")
                except Exception as e_snap:
                    log(f"[refresh] Snapshot rebuild failed (will heal next poll): {e_snap}")
                # Immediately push fresh state so UI reload gets accurate rooms_playing
                try:
                    _do_state_push()
                    log(f"[refresh] State pushed after re-discovery")
                except Exception as e2:
                    log(f"[refresh] State push failed: {e2}")
                # v2.48.3: unconditional SSE confirmation. The page's 🔄 pending UX
                # (p2.44 U-M12) waits for a status_update; the change-driven gate
                # stays silent when a refresh finds nothing new (the usual case),
                # which made the page falsely warn "client may be offline".
                # A user-initiated refresh always deserves an explicit reply.
                try:
                    publish_ui_event("status_update", {})
                    log("[refresh] status_update SSE pushed (UI confirmation)")
                except Exception as e3:
                    log(f"[refresh] SSE confirm push failed: {e3}")
                result["success"] = True
                result["message"] = f"Refreshed: {len(fresh)} speakers"
            except Exception as e:
                result["message"] = f"refresh error: {e}"

        elif action == "replace_queue":
            # v2.57 queue management (§3.1): ATOMIC queue replace via LOAD-THEN-TRIM
            # (F1). URI-carrying content only (spotify: track/album/playlist via
            # ShareLink; raw Sonos URIs via DIDL). Qobuz/Apple named albums route
            # through play_album with replace:true (same trim pattern, shared
            # resolution). Sequence: append new content at END -> verify the add ->
            # RemoveTrackRangeFromQueue(1, old_len) -> play position 1. On add
            # failure the old queue is UNTOUCHED and old music keeps playing (E1).
            # Sheet contract (D6): coordinator-resolve, NEVER regroup.
            from soco.plugins.sharelink import ShareLinkPlugin
            from xml.sax.saxutils import escape as _rq_xml_escape
            room       = cmd.get("room") or (cmd.get("rooms") or [None])[0]
            track_uri  = cmd.get("uri", "")
            title      = cmd.get("title", track_uri)
            rq_artist  = cmd.get("artist", "")
            dev = devices.get(room)
            if not dev:
                result["message"] = f"Room '{room}' not found. Available: {list(devices.keys())}"
            elif not track_uri:
                result["message"] = "No URI provided (named-album replace goes through play_album with replace:true)"
            else:
                track_uri = _decode_sonos_spotify_uri(track_uri)
                is_spotify = track_uri.startswith("spotify:") or "open.spotify.com" in track_uri
                if is_spotify:
                    uri_type = "track" if ":track:" in track_uri else "album" if ":album:" in track_uri else "playlist"
                    share_url = f"https://open.spotify.com/{uri_type}/{track_uri.split(':')[-1]}"
                else:
                    uri_type, share_url = "track", None
                try:
                    coordinator = dev.group.coordinator if dev.group and dev.group.coordinator else dev
                    old_len = coordinator.queue_size  # must be readable — trim depends on it
                    def _rq_add():
                        if is_spotify:
                            return ShareLinkPlugin(coordinator).add_share_link_to_queue(share_url)
                        creator = ('<dc:creator>' + _rq_xml_escape(rq_artist) + '</dc:creator>') if rq_artist else ''
                        meta = ('<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
                                'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                                'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
                                'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
                                '<item id="R:0/0/0" parentID="R:0/0" restricted="true">'
                                '<dc:title>' + _rq_xml_escape(title or "Unknown Track") + '</dc:title>'
                                + creator +
                                '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
                                '<res protocolInfo="*:*:*:*">' + _rq_xml_escape(track_uri) + '</res>'
                                '</item></DIDL-Lite>')
                        return coordinator.add_uri_to_queue(uri=track_uri, didl_resource_meta_data=meta)
                    with _queue_mutation_timeout():
                        _rq_pos, _rq_plc = _verified_queue_add(coordinator, _rq_add, None, label=" replace_queue")
                    first_new = _rq_pos or (old_len + 1)
                    log(f"replace_queue: appended at {first_new} (old queue {old_len} rows) on {coordinator.player_name}")
                    _rq_trim_failed = False
                    if old_len > 0:
                        try:
                            coordinator.avTransport.RemoveTrackRangeFromQueue([
                                ("InstanceID", 0), ("UpdateID", 0),
                                ("StartingIndex", 1), ("NumberOfTracks", old_len)])
                        except Exception as _tr_err:
                            _rq_trim_failed = True  # E12: play new content where it lives
                            log(f"replace_queue: WARNING — trim of {old_len} old rows failed ({_tr_err})")
                    coordinator.play_from_queue(0 if not _rq_trim_failed else first_new - 1)
                    _enforce_repeat_default(coordinator, cmd, room)  # house rule
                    # Provenance: containers become the active context; a single
                    # track wipes it (mirrors play_spotify_uri semantics, v2.55).
                    if is_spotify and uri_type in ("album", "playlist"):
                        _set_queue_provenance(coordinator.player_name, track_uri, title, uri_type)
                        # v2.60 queue sources: replace -> chain restarts
                        _reset_queue_sources(coordinator.player_name,
                            [{"type": uri_type, "name": title, "uri": track_uri}],
                            "replace_queue")
                    else:
                        _clear_queue_provenance(coordinator.player_name, "queue replaced by single track")
                        _reset_queue_sources(coordinator.player_name,
                            [{"type": "tracks", "count": 1}], "replace_queue single track")
                    result["success"] = True
                    _rq_note = " [WARNING: old queue rows not removed — will be swept by next replace/clear]" if _rq_trim_failed else ""
                    result["message"] = f"Replaced queue with '{title}' in {room} ({old_len} old rows removed){_rq_note}"
                    result["data"] = {"title": title, "uri": track_uri, "room": room,
                                      "coordinator": coordinator.player_name,
                                      "old_len": old_len, "queued_at": _rq_pos,
                                      "placement": _rq_plc, "trim_failed": _rq_trim_failed}
                    # v2.58 A7: [queue-op] line + coordinator/group fields (never raises)
                    try:
                        _qo_after = int(coordinator.queue_size)
                    except Exception:
                        _qo_after = None
                    result["data"].update(_queue_op_log(
                        "replace_queue", room, coordinator,
                        queue_before=old_len, queue_after=_qo_after,
                        pos_requested=1, pos_landed=_rq_pos))
                    try:
                        publish_ui_event("status_update", {})
                        schedule_state_push()  # refresh queue_summary
                    except Exception as _rq_sse:
                        log(f"replace_queue: SSE/state push failed: {_rq_sse}")
                except Exception as e:
                    # E1: add failed (or queue unreadable) — old queue untouched,
                    # old music keeps playing. Honest error, no partial state.
                    result["message"] = f"replace_queue error (queue untouched): {e}"

        elif action == "truncate_queue":
            # v2.57 queue management (§3.2): remove everything AFTER the current
            # track. Idempotent (E3): nothing after current -> success, removed:0.
            # Does NOT touch provenance (the queue head is unchanged).
            room = cmd.get("room") or (cmd.get("rooms") or [None])[0]
            dev  = devices.get(room)
            if not dev:
                result["message"] = f"Room '{room}' not found. Available: {list(devices.keys())}"
            else:
                try:
                    coordinator = dev.group.coordinator if dev.group and dev.group.coordinator else dev
                    qsize = coordinator.queue_size
                    cur_pos = 0
                    try:
                        cur_pos = int(coordinator.get_current_track_info().get("playlist_position", 0))
                    except Exception:
                        pass
                    if cur_pos <= 0:
                        # Queue not the active source (stream/idle) — "after current"
                        # is undefined; refuse to guess (idempotent no-op).
                        result["success"] = True
                        result["message"] = f"No current track in {room}'s queue — nothing truncated"
                        result["data"] = {"room": room, "removed": 0, "queue_size": qsize}
                    elif qsize > cur_pos:
                        removed = qsize - cur_pos
                        with _queue_mutation_timeout():
                            coordinator.avTransport.RemoveTrackRangeFromQueue([
                                ("InstanceID", 0), ("UpdateID", 0),
                                ("StartingIndex", cur_pos + 1), ("NumberOfTracks", removed)])
                        log(f"truncate_queue: {coordinator.player_name} removed {removed} rows after pos {cur_pos}")
                        result["success"] = True
                        result["message"] = f"Truncated queue in {room}: {removed} tracks after current removed"
                        result["data"] = {"room": room, "coordinator": coordinator.player_name,
                                          "removed": removed, "queue_size": cur_pos, "current_pos": cur_pos}
                    else:
                        result["success"] = True
                        result["message"] = "Nothing after current track"
                        result["data"] = {"room": room, "removed": 0, "queue_size": qsize, "current_pos": cur_pos}
                    # v2.58 Phase B: an actual truncation is a queue mutation ->
                    # freshen the stale-guard stamp (no-op branches don't touch)
                    if result["data"].get("removed"):
                        _touch_queue(coordinator.player_name, "truncate_queue")
                        # v2.60 queue sources: everything AFTER current was dropped —
                        # later adds are gone; only the head (loaded) entry stays honest.
                        _reset_queue_sources(coordinator.player_name,
                            _get_queue_sources(coordinator.player_name)[:1],
                            "truncate_queue (adds after current dropped)")
                    # v2.58 A7: [queue-op] line + coordinator/group fields (never raises)
                    result["data"].update(_queue_op_log(
                        "truncate_queue", room, coordinator,
                        queue_before=qsize,
                        queue_after=qsize - result["data"].get("removed", 0)))
                    try:
                        publish_ui_event("status_update", {})
                        schedule_state_push()  # refresh queue_summary
                    except Exception as _tq_sse:
                        log(f"truncate_queue: SSE/state push failed: {_tq_sse}")
                except Exception as e:
                    result["message"] = f"truncate_queue error: {e}"

        elif action == "clear_queue":
            # v2.52: UI settings button — wipe the target room's Sonos queue.
            # If the queue is the active source, Sonos stops playback (expected).
            room = cmd.get("room") or (cmd.get("rooms") or [None])[0]
            dev  = devices.get(room)
            if not dev:
                result["message"] = f"Room '{room}' not found. Available: {list(devices.keys())}"
            else:
                try:
                    coordinator = dev.group.coordinator if dev.group and dev.group.coordinator else dev
                    try:
                        _q_before = coordinator.queue_size
                    except Exception:
                        _q_before = "?"
                    with _queue_mutation_timeout():
                        coordinator.clear_queue()
                    _clear_queue_provenance(coordinator.player_name, "clear_queue command")  # v2.55
                    _reset_queue_sources(coordinator.player_name, [], "clear_queue command")  # v2.60
                    _touch_queue(coordinator.player_name, "clear_queue")  # v2.58 Phase B: mutation -> touch
                    log(f"clear_queue: {coordinator.player_name} queue cleared ({_q_before} tracks removed)")
                    result["success"] = True
                    result["message"] = f"Queue cleared in {room} ({_q_before} tracks removed)"
                    result["data"] = {"room": room, "coordinator": coordinator.player_name, "tracks_removed": _q_before}
                    # v2.58 A7: [queue-op] line + coordinator/group fields (never raises)
                    result["data"].update(_queue_op_log(
                        "clear_queue", room, coordinator,
                        queue_before=_q_before if isinstance(_q_before, int) else None,
                        queue_after=0))
                    try:
                        publish_ui_event("status_update", {})
                        schedule_state_push()  # v2.57: refresh queue_summary
                    except Exception as _cq_sse:
                        log(f"clear_queue: SSE push failed: {_cq_sse}")
                except Exception as e:
                    result["message"] = f"clear_queue error: {e}"

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
                                             "play_next", "play_uri", "queue_next", "queue", "add_to_queue", "search_and_play",
                                             "replace_queue"):  # v2.57
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
    result["t_result_sent"] = now_iso()

    # Record structured command outcome (ALL commands, silent or not)
    # v2.58 A7: queue-affecting verbs stamp coordinator/group/queue-shape fields
    # into result["data"]; lift them into the ring entry so heartbeats carry them
    # (queue verbs are silent -- the ring is their only delivery channel).
    _qo_ring = None
    if isinstance(result.get("data"), dict):
        _qo_keys = ("coordinator", "group_members", "transport_state", "queue_before",
                    "queue_after", "pos_requested", "pos_landed", "converted_from", "queue_age")
        _qo_ring = {k: result["data"][k] for k in _qo_keys if k in result["data"]} or None
    record_command_result(
        action=action,
        success=result.get("success", False),
        message=result.get("message", ""),
        cmd_ts=cmd.get("cmd_ts"),
        detail=result.get("data", {}).get("room") if isinstance(result.get("data"), dict) else None,
        queue_op=_qo_ring,
    )

    # v2.52: push command FAILURES to the UI over SSE so the page can show a red
    # toast. Root cause of "green never lies" work: silent play_next failures left
    # the user with a success toast and no music (2026-07-20 Garage incident).
    # Meta/read actions excluded — their failures don't affect playback UX.
    _ERR_TOAST_EXCLUDE = ("update_check", "get_logs", "get_state", "flush", "get_volume", "get_queue")
    if not result.get("success") and action not in _ERR_TOAST_EXCLUDE:
        try:
            publish_ui_event("command_error", {
                "cmd_action": action,
                "cmd_message": (result.get("message") or "unknown error")[:200],
                "cmd_err_ts": cmd.get("cmd_ts") or now_iso(),
            })
        except Exception as _ce_err:
            log(f"[sse] command_error publish failed: {_ce_err}")

    # v2.48: debounced state heartbeat -- ANY state-changing command (silent or
    # not) schedules one heartbeat ~75s out; new commands reset the timer.
    if action not in NON_STATE_ACTIONS:
        try:
            _schedule_debounced_heartbeat(action)
        except Exception as _de:
            log(f"[debounce-hb] scheduling failed: {_de}")

    # Silent actions (volume, etc.) -- log locally, skip webhook POST entirely
    # v2.44 CRITICAL FIX: buffer drain moved BELOW this early-return. Previously
    # silent commands drained pending_buffer into a result that was never POSTed,
    # then the next flush_buffer() overwrote PENDING_PATH -- permanent history loss.
    if is_silent:
        log(f"Command (silent): {result['message']}")
        return

    # Piggyback buffered history on this (non-silent) command result
    # v2.50: wire copy strips internal coalesce fields (_fp etc.); _pig_originals
    # keeps them for buffer-restore on POST failure so crash-coalesce still works.
    _pig_originals = None
    with pending_buffer_lock:
        if pending_buffer:
            log(f"[buffer] piggyback drain: {len(pending_buffer)} pending item(s) attached to '{action}' result")
            _pig_originals = list(pending_buffer)
            result["pending_history"] = [{k: v for k, v in it.items() if not k.startswith("_")} for it in _pig_originals]
            result["format"] = 2  # v2.50 room-factored items
            pending_buffer.clear()
    if _pig_originals:
        try: PENDING_PATH.write_text(json.dumps(_pig_originals), encoding="utf-8")
        except Exception as pe: log(f"[buffer] WARNING: PENDING_PATH write failed: {pe}")

    # Include SSE relay data for server-side relay to ntfy
    sse_relay = build_sse_relay_payload()
    if sse_relay:
        result["sse_relay"] = sse_relay
    result["heartbeat"] = heartbeat_fields()
    _cr_snap = result["heartbeat"].pop("_delta_snap", None)  # v2.49

    try:
        r = requests.post(WEBHOOK, json=result, timeout=15)
        log(f"Command result -> HTTP {r.status_code}: {result['message']}")
        if 200 <= r.status_code < 300:
            _delta_commit(_cr_snap)  # v2.49: mark piggyback delivered
        last_post_ts = time.time()
        if result.get("pending_history"):
            try: PENDING_PATH.unlink(missing_ok=True)
            except: pass
    except Exception as e:
        log(f"Failed to post command result: {e}")
        # Restore piggybacked history to buffer on failure (v2.50: restore the
        # ORIGINALS with internal coalesce fields, not the stripped wire copies)
        if _pig_originals:
            with pending_buffer_lock:
                pending_buffer[:0] = _pig_originals

# --- SONOS: GITHUB CMD FALLBACK (removed in v2.24) --------------------------
# poll_commands() and _clear_github_cmd() removed. ntfy is sole command transport.
# ntfy's since=5m replay on reconnect covers brief outages.

# --- NTFY LISTENER THREAD ---------------------------------------------------
# [ROLLBACK-UNSAFE] Receives update_check commands from ntfy and dispatches to
# execute_command() -> self_update_check(). The old version's parsing + dispatch runs here.
def ntfy_listener_thread():
    global _ntfy_connected, _ntfy_reconnects, _ntfy_last_event_ts
    log(f"ntfy listener: topic={ntfy_topic}")
    while True:
        # Use since=5m so commands sent during restart/reconnect gaps are caught.
        # In-memory dedup (_already_executed) prevents double-execution within same process.
        url   = f"https://ntfy.sh/{ntfy_topic}/json?since=5m"
        ntfy_headers = {"Authorization": f"Bearer {NTFY_TOKEN}"} if NTFY_TOKEN else {}
        log(f"ntfy connecting: {url}")
        try:
            with requests.get(url, stream=True, headers=ntfy_headers, timeout=90) as r:
                _ntfy_connected = True
                _ntfy_last_event_ts = time.time()
                for line in r.iter_lines():
                    # Track every stream event (keepalive or message) for health monitoring
                    _ntfy_last_event_ts = time.time()
                    if not line: continue
                    try:    msg = json.loads(line)
                    except: continue
                    if msg.get("event") != "message": continue
                    raw = msg.get("message", "")
                    log(f"[!] ntfy: {raw[:120]}")
                    try:
                        try:
                            cmd = json.loads(raw)
                        except (json.JSONDecodeError, ValueError):
                            # v2.38: Support plain-text commands like "update_check" or "get_logs"
                            _plain = raw.strip()
                            if _plain and _plain.isidentifier():
                                cmd = {"action": _plain}
                                log(f"ntfy: parsed plain-text command as action='{_plain}'")
                            else:
                                raise
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
    # v2.24: Set 5-second socket timeout (was 20s default — offline speakers blocked for 20s)
    try:
        soco.config.REQUEST_TIMEOUT = 5
        log(f"SoCo socket timeout set to 5s")
    except Exception as e:
        log(f"Warning: could not set SoCo timeout: {e}")

    log(f"Sonos polling every {POLL_INTERVAL}s (v2.24: single-discovery, snapshot-based)")
    log("Scanning for Sonos speakers...")

    first_run   = True

    while True:
        try:
            # v2.24: Single discovery per cycle — get_coordinators() also builds current_devices_by_name
            coordinators = get_coordinators()

            # v2.24: Build poll snapshot once per cycle (replaces 3x get_rooms_playing)
            _build_poll_snapshot(coordinators)

            if first_run:
                names = [d.player_name for d in coordinators]
                log(f"Found {len(names)} coordinator(s): {', '.join(names)}" if names
                    else "No speakers found -- retrying...")
                first_run = False
                # -- Startup "ready" heartbeat -- Sonos discovered, full state available
                try:
                    log("Sending startup ready heartbeat...")
                    rdy_payload = {"type": "heartbeat", "startup_phase": "ready"}
                    # v2.49: boot=True — ready heartbeats always carry the last 50
                    # raw console lines (standing rule), exempt from delta trimming
                    rdy_payload.update(heartbeat_fields(boot=True))
                    _rdy_snap = rdy_payload.pop("_delta_snap", None)
                    _rdy_r = requests.post(WEBHOOK, json=rdy_payload, timeout=10)
                    if 200 <= _rdy_r.status_code < 300:
                        _delta_commit(_rdy_snap)
                    log(f"Ready heartbeat sent (HTTP {_rdy_r.status_code}, v2.49 delta baseline committed)")
                    # Send SSE status_update so browser status bar goes green immediately
                    publish_ui_event("status_update", {})
                    log("Startup SSE status_update sent")
                except Exception as e:
                    log(f"Ready heartbeat failed: {e}")
                # v2.51: ONE unconditional state push at boot (standing rule 25
                # companion). Normally state-{house}.json only pushes on track
                # changes, so an idle machine's file could be days stale -- useless
                # for verifying an update mid-session. This boot push stamps the
                # fresh version + boot_time into the agent's webhook-free pull
                # channel within seconds of startup. Deliberately OUTSIDE the
                # heartbeat try-block so a heartbeat failure can't skip it.
                try:
                    if gh_token:
                        log(f"[state] Boot state push (v{SERVICE_VERSION}, rule-25 verify channel)...")
                        _do_state_push()
                    else:
                        log("[state] Boot state push SKIPPED: no GitHub token configured")
                except Exception as e:
                    log(f"[state] Boot state push failed: {e}")

            now = datetime.now(timezone.utc)

            seen_rooms = set()
            for dev in coordinators:
                info = get_track_info(dev)
                try:    rooms_in_group = [m.player_name for m in dev.group.members if getattr(m, "is_visible", True)]  # v2.54: skip invisible bonded units
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

            # v2.24: GitHub command polling removed — ntfy is sole command transport

            # -- Change-driven status_update SSE + 15-min keepalive (v1.70) --
            # v2.24: Reads from _poll_snapshot (already computed) instead of querying speakers
            global _sse_status_counter, _last_sse_rooms_playing, _last_sse_mute_states
            _sse_status_counter += 1
            rp = list(_poll_snapshot.get("rooms_playing", []))
            ms = dict(_poll_snapshot.get("mute_states", {}))
            rooms_changed = (rp != _last_sse_rooms_playing)
            # v2.48.1: topology-churn gate. Discovery timeouts make speakers vanish
            # from / reappear in the mute dict every few minutes; plain dict
            # inequality treated that KEY churn as a mute event and pushed SSE.
            # Only genuine value flips on keys present in BOTH snapshots count now.
            _m_added   = sorted(k for k in ms if k not in _last_sse_mute_states)
            _m_removed = sorted(k for k in _last_sse_mute_states if k not in ms)
            _m_flipped = sorted(k for k in ms if k in _last_sse_mute_states and ms[k] != _last_sse_mute_states[k])
            mutes_changed = bool(_m_flipped)
            if (_m_added or _m_removed) and not _m_flipped:
                log(f"SSE suppressed: topology churn added={_m_added} removed={_m_removed} (no mute flip, no push)")
            # Baseline updates every cycle so churn never accumulates into a false flip
            _last_sse_mute_states = ms
            keepalive_due = (_sse_status_counter >= 60)  # 60 x 15s = 15 min (~96/day)
            if rooms_changed or mutes_changed or keepalive_due:
                # v2.47.2: log WHY we're pushing — silent triggers made the
                # idle-chatter bug (mute-key flap) invisible for weeks.
                if rooms_changed:
                    log(f"SSE trigger: rooms_changed {_last_sse_rooms_playing} -> {rp}")
                if mutes_changed:
                    log(f"SSE trigger: mutes_changed flipped={_m_flipped} (added={_m_added} removed={_m_removed})")
                if keepalive_due and not (rooms_changed or mutes_changed):
                    log("SSE trigger: 15-min keepalive")
                _sse_status_counter = 0
                publish_ui_event("status_update", {})
                _last_sse_rooms_playing = rp

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
    # v2.54 marker line — Rule 24 requires a version-unique log line so a reboot
    # onto relabeled old bytes is detectable. Do not remove; update per release.
    log("[v2.54] Playback-trust core active: verified queue adds + Spotify Web-API resolution + auth canary")

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

    # -- Startup "boot" phase: LOG ONLY (v2.45) --------------------------------
    # The ready heartbeat (sent right after Sonos discovery, ~3s later) is the
    # single startup heartbeat. Boot+standalone+ready used to send 3 webhook
    # POSTs within 3 seconds, each spawning a redundant agent run.
    log(f"Boot phase reached (v{SERVICE_VERSION}) -- heartbeat deferred to ready phase")

    log(f"=== v{SERVICE_VERSION} init complete -- entering main loop ===")

    # Sonos runs on main thread (visible activity in console)
    if "sonos" in modules:
        try:
            sonos_main_loop()
        except Exception as e:
            crash_msg = f"Sonos crashed: {e}"
            crash_tb = traceback.format_exc()
            print(f"[FATAL] {crash_msg}")
            print(crash_tb)
            try: log(f"[FATAL] {crash_msg}"); log(crash_tb)
            except: pass
            try: post_error(f"Sonos module fatal crash: {e}",
                           context=crash_tb[:500], module="sonos")
            except: pass

    # No Sonos -- keep alive
    log("Service running (no Sonos). Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log("Stopping.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[LifeLog] Stopped by Ctrl+C.")
    except Exception as e:
        msg = f"[FATAL] main() crashed: {e}"
        tb = traceback.format_exc()
        print(msg)
        print(tb)
        try: log(msg); log(tb)
        except: pass
        try: post_error(msg, context=tb[:500], module="main")
        except: pass
        # --- Crash heartbeat: send traceback so Tasklet can see what happened ---
        try:
            import requests as _rq
            _crash_payload = {
                "type": "heartbeat",
                "startup_phase": "crash",
                "client_id": globals().get("client_id", "unknown"),
                "client_type": "lifelog_service",
                "house": globals().get("house", "unknown"),
                "version": SERVICE_VERSION,
                "computer": globals().get("computer", "unknown"),
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "crash_error": str(e),
                "crash_traceback": tb[-2000:],  # last 2000 chars of traceback
                # v2.45: use the real ring buffers (were undefined globals -> always empty)
                "recent_logs": list(globals().get("_log_ring", []))[-30:],
                "recent_errors": list(globals().get("_error_ring", []))[-10:],
            }
            _wh = globals().get("WEBHOOK")
            if _wh:
                _rq.post(_wh, json=_crash_payload, timeout=10)
                print("[LifeLog] Crash heartbeat sent to webhook")
        except Exception as _ce:
            print(f"[LifeLog] Could not send crash heartbeat: {_ce}")
        input("Press Enter to exit...")
