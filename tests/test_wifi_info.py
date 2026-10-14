from types import SimpleNamespace

import pytest

from windows_agent import runtime as agent_runtime
import desktop_agent
from windows_agent.tools import network as wifi_info


def test_ssid_is_separate_from_adapter_name_and_bssid():
    rows = wifi_info.parse_interfaces('''
    Name : Wi-Fi
    Description : Intel Wi-Fi adapter
    State : connected
    SSID : My Home: Upstairs
    AP BSSID : 00:11:22:33:44:55
    Profile : Old Profile
''')
    assert rows[0]['adapter_name'] == 'Wi-Fi'
    assert rows[0]['ssid'] == 'My Home: Upstairs'
    assert rows[0]['connected'] is True


def test_disconnected_adapter_does_not_supply_a_network_name():
    rows = wifi_info.parse_interfaces('Name : Wi-Fi\nState : disconnected\nProfile : SavedNetwork\n')
    assert rows[0]['ssid'] is None
    assert rows[0]['connected'] is False


def test_multiple_adapters_and_unicode_ssid():
    rows = wifi_info.parse_interfaces('Name : Wi-Fi\nState : connected\nSSID : 家庭网络\nName : Wi-Fi 2\nState : disconnected\n')
    assert len(rows) == 2
    assert rows[0]['ssid'] == '家庭网络'
    assert rows[1]['ssid'] is None


@pytest.mark.parametrize('output,exit_code', [
    ('Location permission is required', 0),
    ('Name : Wi-Fi\nState : connected\nDescription : Intel adapter', 0),
    ('Name : Wi-Fi\nState : connected\nSSID : Home', 1),
    ('Name : Wi-Fi\nState : inconnu\nSSID : Home', 0),
])
def test_incomplete_or_failed_query_is_not_verified(monkeypatch, output, exit_code):
    monkeypatch.setattr(wifi_info.terminal_tools, 'run', lambda *args: {'stdout': output, 'exit_code': exit_code})
    result = wifi_info.inspect(lambda: None)
    assert result['verified'] is False
    assert result['error']


def test_live_query_returns_verified_ssid(monkeypatch):
    calls = []
    def run(command, check):
        calls.append(command)
        return {'stdout': 'Name : Wi-Fi\nState : connected\nSSID : Home', 'exit_code': 0}
    monkeypatch.setattr(wifi_info.terminal_tools, 'run', run)
    result = wifi_info.inspect(lambda: None)
    assert calls == ['netsh wlan show interfaces']
    assert result['verified'] is True
    assert result['interfaces'][0]['ssid'] == 'Home'


def test_wifi_tool_available_in_read_only_offline_mode():
    args = SimpleNamespace(read_only=True, online_docs=False)
    names = [tool['function']['name'] for tool in agent_runtime.available_tools(desktop_agent.TOOLS, args)]
    assert 'inspect_wifi' in names
