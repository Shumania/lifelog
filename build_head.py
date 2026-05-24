#!/usr/bin/env python3
"""Build data-head.json from SQL query results.

Reads /tmp/head_input.json (written by publish-head subagent).
Writes /agent/home/lifelog-web/data-head.json.

Input JSON shape:
{
  "music": [...],       # listening_history rows (Spotify)
  "sonos": [...],       # consumption_history rows (type=track)
  "podcasts": [...],    # consumption_history rows (type=podcast)
  "clients": [...],     # client_status rows
  "now_playing": [...], # polling_state now_playing_json row
  "play_commands": [...] # play_commands rows
}
"""
import json, os, sys
from datetime import datetime, timezone
from collections import defaultdict

def parse_ts(s):
    if not s:
        return datetime.min
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except:
        pass
    for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"]:
        try:
            return datetime.strptime(s, fmt)
        except:
            pass
    return datetime.min

def ts_key(s):
    """Return float timestamp for sorting/comparison."""
    dt = parse_ts(s)
    if dt == datetime.min:
        return 0.0
    return dt.replace(tzinfo=timezone.utc).timestamp()

def main():
    with open('/tmp/head_input.json', 'r') as f:
        data = json.load(f)

    music = data.get('music', [])
    sonos_raw = data.get('sonos', [])
    podcasts = data.get('podcasts', [])
    clients = data.get('clients', [])
    now_playing_rows = data.get('now_playing', [])
    play_cmds_raw = data.get('play_commands', [])

    # --- Parse now_playing ---
    now_playing = None
    if now_playing_rows and now_playing_rows[0].get('value'):
        try:
            now_playing = json.loads(now_playing_rows[0]['value'])
        except:
            pass

    # --- Parse play_commands ---
    play_commands = []
    for pc in play_cmds_raw:
        entry = {k: pc.get(k) for k in [
            'timestamp', 'title', 'artist', 'album', 'uri', 'room',
            'context', 'narrative', 'source',
            't_requested', 't_command_sent', 't_playing', 't_published'
        ]}
        try:
            entry['suggestions'] = json.loads(pc['suggestions']) if pc.get('suggestions') else []
        except:
            entry['suggestions'] = []
        play_commands.append(entry)

    items = []

    # --- Spotify music (dedup within 5-min buckets) ---
    music_seen = {}
    for t in music:
        dt = parse_ts(t.get('played_at', ''))
        bucket = int(ts_key(t.get('played_at', '')) // 300)
        key = (t.get('track_name', ''), t.get('artist_name', ''), bucket)
        if key not in music_seen or (t.get('played_at', '') > music_seen[key].get('played_at', '')):
            music_seen[key] = t

    for t in music_seen.values():
        items.append({
            'type': 'music',
            'title': t.get('track_name', ''),
            'artist': t.get('artist_name', ''),
            'album': t.get('album_name', ''),
            'timestamp': t.get('played_at', ''),
            'source': t.get('source', ''),
            'house': 'vashon' if t.get('source') == 'spotify_teenbloods' else 'caphill',
        })

    # --- Podcasts (dedup by title+show) ---
    pod_seen = {}
    for p in podcasts:
        key = (p.get('title', ''), p.get('show_name', ''))
        ts = p.get('timestamp', '') or ''
        if key not in pod_seen or ts > pod_seen[key].get('timestamp', ''):
            pod_seen[key] = p
    for p in pod_seen.values():
        dur = p.get('duration_seconds') or 0
        prog = p.get('progress_seconds') or 0
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

    # --- Sonos tracks (room consolidation within 2-min windows) ---
    sonos_groups = defaultdict(list)
    for t in sonos_raw:
        bucket = int(ts_key(t.get('timestamp', '')) // 120)
        key = ((t.get('title') or '').lower(), (t.get('author') or '').lower(), bucket)
        sonos_groups[key].append(t)

    for key, group in sonos_groups.items():
        best = max(group, key=lambda x: x.get('timestamp') or '')
        rooms = sorted(set(t.get('domain') or '' for t in group if t.get('domain')))
        container_name = None
        for t in group:
            cn = t.get('container_name') or ''
            if cn:
                container_name = cn
                break
        entry = {
            'type': 'music',
            'title': best.get('title') or '',
            'artist': best.get('author') or '',
            'album': best.get('meta_album') or best.get('album') or '',
            'timestamp': best.get('timestamp') or '',
            'source': best.get('source') or 'sonos_unknown',
            'house': best.get('house') or '',
            'room': rooms[0] if rooms else '',
            'rooms': rooms if len(rooms) > 1 else None,
        }
        if container_name:
            entry['context'] = container_name
        items.append(entry)

    # --- Cross-source dedup (15-min window: keep Sonos, drop Spotify dupe) ---
    sonos_items = [i for i in items if i.get('room')]
    spotify_items = [i for i in items if not i.get('room') and i.get('type') == 'music']
    other_items = [i for i in items if i.get('type') != 'music' and not i.get('room')]

    deduped = []
    for sp in spotify_items:
        sp_ts = ts_key(sp.get('timestamp', ''))
        is_dupe = False
        if sp_ts:
            for so in sonos_items:
                so_ts = ts_key(so.get('timestamp', ''))
                if (so_ts and
                    sp.get('title', '').lower() == so.get('title', '').lower() and
                    sp.get('artist', '').lower() == so.get('artist', '').lower() and
                    abs(sp_ts - so_ts) <= 900):
                    is_dupe = True
                    break
        if not is_dupe:
            deduped.append(sp)

    items = deduped + sonos_items + other_items
    items.sort(key=lambda x: ts_key(x.get('timestamp', '')), reverse=True)
    items = items[:75]

    # --- Extract rooms_playing from commander metadata ---
    rooms_playing = []
    for c in clients:
        if c.get('sonos_commander'):
            meta = c.get('metadata')
            if meta:
                try:
                    m = json.loads(meta) if isinstance(meta, str) else meta
                    rooms_playing = m.get('rooms_playing', [])
                except:
                    pass
            break

    # --- Add played_at to now_playing if missing ---
    if now_playing and not now_playing.get('played_at'):
        # Use most recent play_command timestamp as best guess
        if play_commands:
            now_playing['played_at'] = play_commands[0].get('timestamp', '')
        else:
            now_playing['played_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    # --- Infer house for Sonos items from commander's house ---
    commander_house = ''
    for c in clients:
        if c.get('sonos_commander'):
            commander_house = c.get('house', '')
            break
    if commander_house:
        for item in items:
            if item.get('room') and not item.get('house'):
                item['house'] = commander_house

    # --- Build output ---
    out = {
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'now_playing': now_playing,
        'rooms_playing': rooms_playing,
        'items': items,
        'client_status': clients,
        'play_commands': play_commands,
    }

    os.makedirs('/agent/home/lifelog-web', exist_ok=True)
    with open('/agent/home/lifelog-web/data-head.json', 'w') as f:
        json.dump(out, f)

    sz = os.path.getsize('/agent/home/lifelog-web/data-head.json')
    print(f"OK: {len(items)} items, {sz} bytes")

if __name__ == '__main__':
    main()
