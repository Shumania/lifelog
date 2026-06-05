import soco
from soco.plugins.sharelink import ShareLinkPlugin
import traceback

print(f'soco {soco.__version__}')

devices = {d.player_name: d for d in soco.discover(timeout=5) or []}
print(f'Found speakers: {list(devices.keys())}')

for name in ['Basement Study', 'Living Room']:
    dev = devices.get(name)
    if not dev:
        print(f'{name}: NOT FOUND')
        continue
    print(f'\n=== {name} (IP: {dev.ip_address}) ===')
    info = dev.get_speaker_info()
    print(f'Firmware: {info.get("software_version", "unknown")}')
    plugin = ShareLinkPlugin(dev)

    # Test 1: Single track
    try:
        dev.clear_queue()
        pos = plugin.add_share_link_to_queue('https://open.spotify.com/track/1Q3YAMql2Uj7OlNmOOoRJE')
        print(f'Track enqueue: OK (pos={pos})')
    except Exception as e:
        print(f'Track enqueue FAILED: {e}')
        traceback.print_exc()

    # Test 2: Album
    try:
        dev.clear_queue()
        pos = plugin.add_share_link_to_queue('https://open.spotify.com/album/4vVDNMbR2dJxsCla0v6TBf')
        print(f'Album enqueue: OK (pos={pos})')
    except Exception as e:
        print(f'Album enqueue FAILED: {e}')
        traceback.print_exc()

    # Test 3: Playlist
    try:
        dev.clear_queue()
        pos = plugin.add_share_link_to_queue('https://open.spotify.com/playlist/37i9dQZF1DX2VgMYP6hVjt')
        print(f'Playlist enqueue: OK (pos={pos})')
    except Exception as e:
        print(f'Playlist enqueue FAILED: {e}')
        traceback.print_exc()

print('\nDone.')
