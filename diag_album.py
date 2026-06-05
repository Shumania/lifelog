import soco
from soco.music_services import MusicService
import xml.etree.ElementTree as ET

devices = {d.player_name: d for d in soco.discover(timeout=5) or []}
dev = devices.get('Living Room') or devices.get('Basement Study')
if not dev:
    print('No speaker found')
    exit(1)

print(f'Speaker: {dev.player_name} ({dev.ip_address})')

# Method 1: Query available music services
print('\n=== Available Music Services ===')
try:
    services = MusicService.get_subscribed_services_names(dev)
    print(f'Subscribed: {services}')
except Exception as e:
    print(f'Error getting services: {e}')

# Method 2: Direct SOAP query for service list
print('\n=== Direct Service Query ===')
try:
    resp = dev.musicServices.ListAvailableServices()
    # Parse the descriptor XML to find Spotify entries
    desc = resp.get('AvailableServiceDescriptorList', '')
    root = ET.fromstring(f'<root>{desc}</root>')
    for svc in root.findall('.//'):
        name = svc.get('Name', '')
        if name and ('potif' in name.lower() or 'spotify' in name.lower()):
            print(f'  Name={name}, Id={svc.get("Id")}, SecureUri={svc.get("SecureUri", "?")}')
            for child in svc:
                print(f'    {child.tag}: {child.attrib}')
except Exception as e:
    print(f'Error: {e}')

# Method 3: Check the account credentials stored on the speaker
print('\n=== Stored Account Info ===')
try:
    from soco.services import Service
    system_props = dev.systemProperties
    # Try to enumerate stored accounts
    print(dir(system_props))
except Exception as e:
    print(f'Error: {e}')

# Method 4: Try to find what SN the speaker actually uses for Spotify
print('\n=== Account Serial Numbers ===')
try:
    resp = dev.musicServices.ListAvailableServices()
    desc = resp.get('AvailableServiceDescriptorList', '')
    root = ET.fromstring(f'<root>{desc}</root>')
    for svc in root.iter():
        if svc.tag == 'Service':
            name = svc.get('Name', '')
            sid = svc.get('Id', '')
            print(f'  {name}: Id={sid}')
except Exception as e:
    print(f'Error: {e}')

print('\nDone.')
