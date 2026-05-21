# Retrieve the Sonos transport debug dump
python3 -c "
import soco
for d in soco.discover(timeout=5) or []:
    try:
        info = d.get_current_transport_info()
        state = info.get('current_transport_state','?')
        track = d.get_current_track_info()
        title = track.get('title','')
        print(f'{d.player_name}: {state} | {title}')
    except Exception as e:
        print(f'{d.player_name}: ERROR {e}')
"
