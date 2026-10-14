"""Read current Wi-Fi connections; never infer SSIDs from adapter names."""
import time

from . import diagnostics as terminal_tools


def parse_interfaces(output):
    interfaces = []
    current = None
    fields = {'description': 'adapter_description', 'state': 'state',
              'ssid': 'ssid', 'signal': 'signal', 'band': 'band'}
    for line in output.splitlines():
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        key, value = key.strip().casefold(), value.strip()
        if key == 'name':
            current = {'adapter_name': value, 'ssid': None, 'state': None}
            interfaces.append(current)
        elif current is not None and key in fields:
            current[fields[key]] = value or None
    for item in interfaces:
        state = (item['state'] or '').casefold()
        item['connected'] = True if state == 'connected' else False if state == 'disconnected' else None
        if item['connected'] is not True:
            item['ssid'] = None
    return interfaces


def inspect(check):
    result = terminal_tools.run('netsh wlan show interfaces', check)
    check()
    interfaces = parse_interfaces(result.get('stdout', '')) if result.get('exit_code') == 0 else []
    verified = bool(interfaces) and all(
        item['connected'] is not None and (not item['connected'] or item['ssid'] is not None)
        for item in interfaces
    )
    response = {'interfaces': interfaces, 'verified': verified, 'read_only': True,
                'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'source': 'live netsh wlan show interfaces',
                'notice': 'adapter_name identifies the interface; ssid is the connected Wi-Fi network name. No saved snapshot or profile-name inference.'}
    if not verified:
        response['error'] = ('Current Wi-Fi connection could not be verified. The adapter may be absent, '
                             'Windows may restrict access, or the output language may be unsupported. '
                             'Inspect Windows Settings for the connection; do not infer an SSID.')
        response['diagnostic_output'] = (result.get('stderr', '') + '\n' + result.get('stdout', ''))[:3000]
    return response
