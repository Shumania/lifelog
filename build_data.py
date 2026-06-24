#!/usr/bin/env python3
"""Shared data processing module for lifelog publishers.

Used by both build_head.py (head publisher) and lifelog-data-publisher.md (full archive).
Single source of truth for: dedup, room consolidation, cross-source matching, session inference.

Usage:
    from build_data import process_all

    result = process_all(data, limit=75)  # head publisher
    result = process_all(data, limit=None)  # full archive
"""
import json, re, urllib.parse
from datetime import datetime, timezone
from collections import defaultdict


# ═══════════════════════════════════════════════════════════════════════════════
# TIMESTAMP HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def parse_ts(s):
    """Parse ISO timestamp string to naive UTC datetime. Returns datetime.min on failure."""
    if not s:
        return datetime.min
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        pass
    for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]:
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return datetime.min


def ts_key(s):
    """Return float timestamp for sorting/comparison. 0.0 on failure."""
    dt = parse_ts(s)
    if dt == datetime.min:
        return 0.0
    return dt.replace(tzinfo=timezone.utc).timestamp()


# ═══════════════════════════════════════════════════════════════════════════════
# NORMALIZATION (shared with frontend JS — keep in sync!)
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_album(name):
    """Normalize album name for cross-service matching.

    Strips remaster/deluxe/anniversary/edition suffixes while preserving
    meaningful parentheticals like '(What's the Story) Morning Glory?'.

    IMPORTANT: Keep in sync with normalizeAlbum() in index.html.
    """
    if not name:
        return ''
    s = name.lower().strip()
    # Strip parenthetical suffixes (remaster, deluxe, anniversary, etc.)
    s = re.sub(
        r'\s*\((?:remaster|deluxe|expanded|anniversary|bonus|special|limited|'
        r'super deluxe|legacy|collector|platinum|gold|diamond|'
        r'30th|40th|50th|20th|10th|\d+th anniversary)[^)]*\)',
        '', s, flags=re.IGNORECASE
    )
    # Strip bracket suffixes
    s = re.sub(
        r'\s*\[(?:remaster|deluxe|expanded|bonus|special|limited)[^\]]*\]',
        '', s, flags=re.IGNORECASE
    )
    # Strip trailing markers after dash
    s = re.sub(
        r'\s*[-–—]\s*(?:remastered|deluxe edition|expanded edition|special edition|anniversary edition).*$',
        '', s, flags=re.IGNORECASE
    )
    return s.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# SPOTIFY URI EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def extract_spotify_uri(*candidates):
    """Extract a spotify:track: or spotify:album: URI from Sonos transport URIs.

    Handles formats:
      - Direct: spotify:track:ABC123
      - Sonos-Spotify: x-sonos-spotify:spotify%3atrack%3aABC123?sid=...
      - VLI: x-sonos-vli:...,spotify:{hash}
    """
    for candidate in candidates:
        if not candidate:
            continue
        if candidate.startswith('spotify:'):
            return candidate
        if 'spotify' not in candidate:
            continue
        decoded = urllib.parse.unquote(candidate)
        if 'spotify:track:' in decoded:
            tid = decoded.split('spotify:track:')[1].split('?')[0]
            return f'spotify:track:{tid}'
        elif 'spotify:album:' in decoded:
            aid = decoded.split('spotify:album:')[1].split('?')[0]
            return f'spotify:album:{aid}'
    return ''


# ═══════════════════════════════════════════════════════════════════════════════
# PROCESSING PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def process_spotify(music):
    """Dedup Spotify tracks within 5-min buckets. Returns list of item dicts."""
    music_seen = {}
    for t in music:
        bucket = int(ts_key(t.get('played_at', '')) // 300)
        key = (t.get('track_name', ''), t.get('artist_name', ''), bucket)
        if key not in music_seen or (t.get('played_at', '') > music_seen[key].get('played_at', '')):
            music_seen[key] = t

    items = []
    for t in music_seen.values():
        entry = {
            'type': 'music',
            'title': t.get('track_name', ''),
            'artist': t.get('artist_name', ''),
            'album': t.get('album_name', ''),
            'timestamp': t.get('played_at', ''),
            'source': t.get('source', ''),
            'house': 'roaming',
        }
        if t.get('spotify_uri'):
            entry['uri'] = t['spotify_uri']
        if t.get('context_type'):
            entry['context_type'] = t['context_type']
        if t.get('context_uri'):
            entry['context_uri'] = t['context_uri']
        items.append(entry)
    return items


def process_podcasts(podcasts):
    """Dedup podcasts by title+show, compute progress display. Returns list of item dicts."""
    pod_seen = {}
    for p in podcasts:
        key = (p.get('title', ''), p.get('show_name', ''))
        ts = p.get('timestamp', '') or ''
        if key not in pod_seen or ts > pod_seen[key].get('timestamp', ''):
            pod_seen[key] = p

    items = []
    for p in pod_seen.values():
        dur = p.get('duration_seconds') or 0
        prog = p.get('progress_seconds') or 0
        # Apple resets progress_seconds to 0 after fully completed episodes
        display_pct = (100 if prog == 0 else min(100, round(prog / dur * 100))) if dur > 0 else 0
        listened_label = f"{prog // 60}m listened" if prog > 0 else ("Finished" if dur > 0 else "")
        items.append({
            'type': 'podcast',
            'title': p.get('title', ''),
            'show_name': p.get('show_name', ''),
            'duration_seconds': dur,
            'display_pct': display_pct,
            'listened_label': listened_label,
            'timestamp': p.get('timestamp', ''),
            'source': p.get('source', ''),
        })
    return items


JUNK_TITLES = {'-[break]-', '[break]', 'zpstr_connecting', 'zpstr_buffering', 'zpstr_enqueued', ''}


def process_radio_sessions(sonos_raw):
    """Process radio_session rows into display items.

    - Unexpanded sessions: emit a 'radio_session' item with expanded=false
    - Expanded sessions: emit a 'radio_session' item with expanded=true and embedded child tracks

    Child tracks are matched from sonos_raw by radio_session_id in raw_metadata.

    Handles two input shapes:
      - Publisher queries: fields come from json_extract aliases (end_time, session_rooms, station, expanded, etc.)
      - Direct queries: raw_metadata JSON string with nested fields
    """
    sessions = [t for t in sonos_raw if t.get('type') == 'radio_session']

    # Build lookup of child tracks by radio_session_id
    child_tracks_by_session = defaultdict(list)
    for t in sonos_raw:
        if t.get('type') == 'radio_session':
            continue
        meta = {}
        rm = t.get('raw_metadata')
        if rm:
            try:
                meta = json.loads(rm) if isinstance(rm, str) else rm
            except Exception:
                pass
        rsid = t.get('radio_session_id') or meta.get('radio_session_id')
        if rsid:
            # spotify_id / album_image may come from json_extract aliases (top-level)
            # OR from raw_metadata JSON — check both
            spotify_id = t.get('spotify_id') or meta.get('spotify_id') or ''
            album_img = t.get('album_image') or meta.get('album_image') or ''
            album = t.get('meta_album') or t.get('show_name') or meta.get('album', '') or ''
            child_tracks_by_session[rsid].append({
                'title': t.get('title', ''),
                'artist': t.get('author', ''),
                'album': album,
                'timestamp': t.get('timestamp', ''),
                'uri': f"spotify:track:{spotify_id}" if spotify_id else '',
                'album_image': album_img,
            })

    items = []
    for s in sessions:
        meta = {}
        rm = s.get('raw_metadata')
        if rm:
            try:
                meta = json.loads(rm) if isinstance(rm, str) else rm
            except Exception:
                pass

        expanded = s.get('expanded') or meta.get('expanded')
        is_expanded = (expanded == 'true' or expanded is True)

        # Resolve house: prefer json_extract alias, then raw_metadata, then domain, then source_device_id if it's a known house
        house = s.get('house') or meta.get('house', '') or s.get('domain', '')
        if not house:
            sid = s.get('source_device_id', '')
            if sid in ('caphill', 'vashon'):
                house = sid

        rooms = []
        sr = s.get('session_rooms')
        if sr:
            try:
                rooms = json.loads(sr) if isinstance(sr, str) else sr
            except Exception:
                rooms = []
        if not rooms:
            rooms = meta.get('rooms', [])

        end_time = s.get('end_time') or meta.get('end_time', '')
        station = s.get('station') or meta.get('station', s.get('source', ''))
        enriched_count = s.get('enriched_count') or meta.get('enriched_count', 0)
        session_id = s.get('id')

        entry = {
            'type': 'radio_session',
            'title': s.get('title') or s.get('show_name') or 'Radio',
            'artist': '',
            'album': '',
            'timestamp': s.get('timestamp', ''),
            'end_time': end_time,
            'source': s.get('source', ''),
            'station': station,
            'house': house,
            'room': ', '.join(rooms) if rooms else '',
            'rooms': rooms if len(rooms) > 1 else None,
            'enriched_count': enriched_count,
            'session_id': session_id,
            'expanded': is_expanded,
        }

        if is_expanded and session_id:
            # Embed child tracks, sorted by timestamp
            children = child_tracks_by_session.get(session_id, [])
            children.sort(key=lambda x: x.get('timestamp', ''))
            entry['tracks'] = children

        items.append(entry)
    return items


def process_sonos(sonos_raw):
    """Consolidate Sonos tracks: same title+artist within 2-min window → merge rooms.

    Handles two input shapes:
      - build_head.py: fields come from SQL with json_extract aliases
        (meta_album, house, sonos_uri, sonos_transport_uri, container_name)
      - full publisher: fields come from raw consumption_history columns
        (album from show_name, url as sonos_uri, raw_metadata JSON)
    """
    # Filter out radio_session rows (handled by process_radio_sessions),
    # child tracks of radio sessions (embedded in parent session), and junk
    def _is_radio_child(t):
        if t.get('radio_session_id'):
            return True
        rm = t.get('raw_metadata')
        if rm:
            try:
                meta = json.loads(rm) if isinstance(rm, str) else rm
                if meta.get('radio_session_id'):
                    return True
            except Exception:
                pass
        return False

    sonos_raw = [t for t in sonos_raw if t.get('type') != 'radio_session' and not _is_radio_child(t)]
    sonos_raw = [t for t in sonos_raw if (t.get('title') or '').lower().strip() not in JUNK_TITLES
                 and not (t.get('title') or '').startswith('ZPSTR_')]

    sonos_groups = defaultdict(list)
    for t in sonos_raw:
        bucket = int(ts_key(t.get('timestamp', '')) // 120)
        key = ((t.get('title') or '').lower(), (t.get('author') or '').lower(), bucket)
        sonos_groups[key].append(t)

    items = []
    for key, group in sonos_groups.items():
        best = max(group, key=lambda x: x.get('timestamp') or '')
        rooms = sorted(set(t.get('domain') or '' for t in group if t.get('domain')))

        # Extract container_name — try direct field first, then raw_metadata
        container_name = None
        for t in group:
            cn = t.get('container_name') or ''
            if cn:
                container_name = cn
                break
            rm = t.get('raw_metadata')
            if rm:
                try:
                    meta = json.loads(rm) if isinstance(rm, str) else rm
                    cn = meta.get('container_name', '')
                    if cn:
                        container_name = cn
                        break
                except Exception:
                    pass

        # Extract enriched metadata — try json_extract fields first, then raw_metadata
        enriched = {}
        for t in group:
            # Check json_extract fields (from publisher queries)
            if t.get('enriched'):
                enriched = {
                    'enriched': True,
                    'spotify_id': t.get('spotify_id'),
                    'album_image': t.get('album_image'),
                    'label': t.get('meta_label'),
                    'year': t.get('meta_year'),
                    'album': t.get('meta_album') or '',
                }
                break
            # Fall back to raw_metadata parsing
            rm = t.get('raw_metadata')
            if rm:
                try:
                    meta = json.loads(rm) if isinstance(rm, str) else rm
                    if meta.get('enriched'):
                        enriched = meta
                        break
                except Exception:
                    pass

        # Extract album — prefer enriched, then meta_album, then show_name
        album = enriched.get('album') or best.get('meta_album') or best.get('album') or ''
        if not album:
            # Full publisher puts album in show_name column
            album = best.get('show_name') or ''

        # Extract house — try direct field, then source_device_id
        house = best.get('house') or best.get('source_device_id') or ''

        # Build entry
        entry = {
            'type': 'music',
            'title': best.get('title') or '',
            'artist': best.get('author') or '',
            'album': album,
            'timestamp': best.get('timestamp') or '',
            'source': best.get('source') or 'sonos_unknown',
            'house': house,
            'room': rooms[0] if rooms else '',
            'rooms': rooms if len(rooms) > 1 else None,
        }
        if container_name:
            entry['context'] = container_name

        # Enriched metadata: Spotify ID, album art, label, year
        if enriched.get('spotify_id'):
            entry['uri'] = f"spotify:track:{enriched['spotify_id']}"
        if enriched.get('album_image'):
            entry['album_image'] = enriched['album_image']
        if enriched.get('label'):
            entry['label'] = enriched['label']
        if enriched.get('year'):
            entry['year'] = enriched['year']

        # Extract Spotify URI from Sonos transport URI (non-radio tracks)
        if not entry.get('uri'):
            sonos_uri = best.get('sonos_uri') or best.get('url') or ''
            transport_uri = best.get('sonos_transport_uri') or ''
            spotify_uri = extract_spotify_uri(sonos_uri, transport_uri)
            if spotify_uri:
                entry['uri'] = spotify_uri
        # Include raw Sonos URI for native replay (Qobuz, Apple Music, etc.)
        sonos_uri = best.get('sonos_uri') or best.get('url') or ''
        if sonos_uri and not sonos_uri.startswith('spotify:'):
            entry['sonos_uri'] = sonos_uri
        # Include service type
        src = best.get('source') or ''
        if src:
            entry['service'] = src
        items.append(entry)
    return items


def cross_source_dedup(items):
    """Remove Spotify duplicates of Sonos tracks within 2-hour window.
    Then reclassify remaining Spotify items that belong to a Sonos session.

    Returns the deduplicated+reclassified items list.
    """
    WINDOW = 2 * 60 * 60  # 2 hours

    sonos_items = [i for i in items if i.get('room')]
    spotify_items = [i for i in items if not i.get('room') and i.get('type') == 'music']
    other_items = [i for i in items if i.get('type') != 'music' and not i.get('room')]

    # Phase 1: Remove exact duplicates (same title+artist within window)
    deduped = []
    for sp in spotify_items:
        sp_ts = ts_key(sp.get('timestamp', ''))
        is_dupe = False
        if sp_ts:
            for so in sonos_items:
                so_ts = ts_key(so.get('timestamp', ''))
                if not so_ts or abs(sp_ts - so_ts) > WINDOW:
                    continue
                sp_t = sp.get('title', '').lower()
                so_t = so.get('title', '').lower()
                sp_a = sp.get('artist', '').lower()
                so_a = so.get('artist', '').lower()
                # Prefix match: Sonos may truncate long titles
                min_len = min(len(sp_t), len(so_t))
                titles_match = (min_len >= 10 and sp_t[:min_len] == so_t[:min_len]) or sp_t == so_t
                if titles_match and sp_a == so_a:
                    is_dupe = True
                    break
        if not is_dupe:
            deduped.append(sp)

    # Phase 2: Session inference — reclassify Spotify items that are part of a Sonos session
    for sp in deduped:
        sp_ts = ts_key(sp.get('timestamp', ''))
        if not sp_ts:
            continue
        for so in sonos_items:
            so_ts = ts_key(so.get('timestamp', ''))
            if not so_ts or abs(sp_ts - so_ts) > WINDOW:
                continue
            if sp.get('artist', '').lower() != so.get('artist', '').lower():
                continue
            sp_album = sp.get('album', '').lower()
            so_album = so.get('album', '').lower()
            if sp_album and so_album and sp_album != so_album:
                continue
            # Same artist, compatible album, within window → inherit Sonos source/room
            sp['source'] = so.get('source', 'sonos_spotify')
            sp['house'] = so.get('house', '')
            sp['room'] = so.get('room', '')
            if so.get('rooms'):
                sp['rooms'] = so['rooms']
            break

    return deduped + sonos_items + other_items


def parse_play_commands(play_cmds_raw, exploration_suggestions=None):
    """Parse play_commands rows into clean dicts with suggestions.

    If exploration_suggestions is provided, reconstruct suggestions from normalized
    DB rows instead of parsing JSON blobs. This is the Phase 1 migration path.
    """
    # Build lookup: exploration_id → sorted list of suggestion rows
    sugg_by_id = defaultdict(list)
    if exploration_suggestions:
        for s in exploration_suggestions:
            sugg_by_id[s['exploration_id']].append(s)
        for k in sugg_by_id:
            sugg_by_id[k].sort(key=lambda x: x['suggestion_index'])

    play_commands = []
    for pc in play_cmds_raw:
        entry = {k: pc.get(k) for k in [
            'timestamp', 'title', 'artist', 'album', 'uri', 'room',
            'context', 'narrative', 'source',
            't_requested', 't_command_sent', 't_playing', 't_published'
        ]}

        pc_id = pc.get('id')
        if exploration_suggestions is not None and pc_id in sugg_by_id:
            # Reconstruct from normalized DB rows
            genre = pc.get('genre')
            genre_source = pc.get('genre_source')
            entry['suggestions'] = _reconstruct_suggestions(
                genre, genre_source, sugg_by_id[pc_id])
            entry['exploration_id'] = pc_id
        else:
            # Legacy: parse JSON blob
            try:
                entry['suggestions'] = json.loads(pc['suggestions']) if pc.get('suggestions') else []
            except Exception:
                entry['suggestions'] = []
            # Always set exploration_id so album-expand buttons render
            if pc_id is not None:
                entry['exploration_id'] = pc_id

        play_commands.append(entry)
    return play_commands


def _reconstruct_suggestions(genre, genre_source, rows):
    """Reconstruct the original suggestions structure from normalized DB rows."""
    items = []
    exploration_meta = {}

    for row in rows:
        rtype = row.get('type', 'suggestion')
        meta = {}
        if row.get('metadata'):
            try:
                meta = json.loads(row['metadata']) if isinstance(row['metadata'], str) else row['metadata']
            except Exception:
                pass

        # Extract exploration-level metadata from source_track (index 0)
        if row.get('suggestion_index') == 0:
            for k in ('context', 'work_title'):
                if meta.get(k):
                    exploration_meta[k] = meta.pop(k)
            if meta.get('composer'):
                exploration_meta['composer'] = meta['composer']  # copy, don't pop

        item = {}
        if rtype == 'section_header':
            item['type'] = 'section_header'
            item.update(meta)
            item['label'] = row['label']
        elif rtype == 'narrative':
            item['type'] = 'narrative'
            item.update(meta)
        else:
            if rtype != 'suggestion':
                item['type'] = rtype
            item['label'] = row['label']
            item['uri'] = row.get('uri') or ''
            item['action'] = row.get('action') or 'play'
            item['note'] = row.get('note') or ''
            item.update(meta)

        items.append(item)

    if genre is not None:
        result = {'genre': genre, 'genre_source': genre_source or ''}
        result.update(exploration_meta)
        result['items'] = items
        return result
    else:
        return items


def parse_now_playing(now_playing_rows):
    """Parse now_playing from polling_state row."""
    if now_playing_rows and now_playing_rows[0].get('value'):
        try:
            return json.loads(now_playing_rows[0]['value'])
        except Exception:
            pass
    return None


def extract_rooms_playing(clients):
    """Extract rooms_playing from the commander client's metadata."""
    for c in clients:
        if c.get('sonos_commander'):
            meta = c.get('metadata')
            if meta:
                try:
                    m = json.loads(meta) if isinstance(meta, str) else meta
                    return m.get('rooms_playing', [])
                except Exception:
                    pass
            break
    return []


def get_commander_house(clients):
    """Get the house value from the Sonos commander client."""
    for c in clients:
        if c.get('sonos_commander'):
            return c.get('house', '')
    return ''


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def process_all(data, limit=None):
    """Process all data sources into the final output dict.

    Args:
        data: dict with keys: music, sonos, podcasts, clients, now_playing, play_commands
        limit: max items to include (75 for head, None for full archive)

    Returns:
        dict with: generated_at, now_playing, rooms_playing, items, client_status, play_commands
    """
    music = data.get('music', [])
    sonos_raw = data.get('sonos', [])
    podcasts = data.get('podcasts', [])
    clients_raw = data.get('clients', [])
    # Dedup clients by normalized client_id (prefer lifelog_ prefix over bare name)
    seen_ids = {}
    clients = []
    for c in clients_raw:
        cid = c.get('client_id', '')
        norm = cid if cid.startswith('lifelog_') else f'lifelog_{cid.lower()}'
        if norm in seen_ids:
            # Keep the one with more recent last_seen
            existing = seen_ids[norm]
            if (c.get('last_seen') or '') > (existing.get('last_seen') or ''):
                clients[clients.index(existing)] = c
                seen_ids[norm] = c
        else:
            seen_ids[norm] = c
            clients.append(c)
    now_playing_rows = data.get('now_playing', [])
    play_cmds_raw = data.get('play_commands', [])
    exploration_suggestions = data.get('exploration_suggestions')

    # Parse metadata
    now_playing = parse_now_playing(now_playing_rows)
    play_commands = parse_play_commands(play_cmds_raw, exploration_suggestions)
    rooms_playing = extract_rooms_playing(clients)
    commander_house = get_commander_house(clients)

    # Process each source
    spotify_items = process_spotify(music)
    podcast_items = process_podcasts(podcasts)
    radio_session_items = process_radio_sessions(sonos_raw)
    sonos_items = process_sonos(sonos_raw)

    # Combine and cross-source dedup
    all_items = spotify_items + podcast_items + sonos_items + radio_session_items
    all_items = cross_source_dedup(all_items)

    # Sort by timestamp (newest first)
    all_items.sort(key=lambda x: ts_key(x.get('timestamp', '')), reverse=True)

    # Apply limit
    if limit:
        all_items = all_items[:limit]

    # Infer house for Sonos items from commander
    if commander_house:
        for item in all_items:
            if item.get('room') and not item.get('house'):
                item['house'] = commander_house

    # Fix now_playing.played_at fallback — use most recent item, NOT play_commands
    if now_playing and not now_playing.get('played_at'):
        if all_items:
            now_playing['played_at'] = all_items[0].get('timestamp', '')
        else:
            now_playing['played_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    # Build explored_artists map: normalized artist name → card index
    # Used by frontend to swap 🔍→📖 at render time (no runtime computation)
    explored_artists = {}
    for i, pc in enumerate(play_commands):
        artist = (pc.get('artist') or '').lower().strip()
        if artist and artist not in explored_artists:
            explored_artists[artist] = i  # first exploration wins

    return {
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'now_playing': now_playing,
        'rooms_playing': rooms_playing,
        'items': all_items,
        'client_status': clients,
        'play_commands': play_commands,
        'explored_artists': explored_artists,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ALBUM TRACK FILES (Phase 2)
# ═══════════════════════════════════════════════════════════════════════════════

def build_album_track_files(album_tracks_rows):
    """Build per-exploration album track JSON data from DB rows.

    Args:
        album_tracks_rows: list of dicts with keys:
            exploration_id, album_uri, track_number, track_name, track_uri,
            duration_ms, disc_number

    Returns:
        dict: {exploration_id: {album_uri: [track_dicts]}}
        Each track_dict: {"n": track_number, "name": track_name,
                          "uri": track_uri, "ms": duration_ms}
        disc_number ("d") included only when > 1.
    """
    result = defaultdict(lambda: defaultdict(list))
    for row in album_tracks_rows:
        eid = row['exploration_id']
        auri = row['album_uri']
        track = {
            'n': row['track_number'],
            'name': row['track_name'],
            'uri': row['track_uri'],
            'ms': row.get('duration_ms') or 0,
        }
        disc = row.get('disc_number') or 1
        if disc > 1:
            track['d'] = disc
        result[eid][auri].append(track)

    # Sort tracks by disc then track number within each album
    for eid in result:
        for auri in result[eid]:
            result[eid][auri].sort(key=lambda t: (t.get('d', 1), t['n']))

    # Convert defaultdicts to regular dicts for JSON serialization
    return {eid: dict(albums) for eid, albums in result.items()}
