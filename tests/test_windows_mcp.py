"""Exercise the MCP interface without loading a model or operating the desktop."""
import asyncio
from contextlib import nullcontext
import sys
import threading
from unittest.mock import Mock

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError
import pytest

from windows_agent import PROJECT_ROOT
from windows_agent import mcp_server as mcp


def run_client(operation, *, mode='auto'):
    async def run():
        async with Client(mcp.server, cache=None, mode=mode, read_timeout_seconds=5) as client:
            return await operation(client)
    return asyncio.run(run())


@pytest.mark.parametrize('mode', ['auto', 'legacy'])
def test_tool_catalog_and_read_only_annotations(mode):
    async def check(client):
        result = await client.list_tools()
        names = {tool.name for tool in result.tools}
        assert names == set(mcp.get_capabilities()['tools'])
        assert not names.intersection({'click_element', 'type_text', 'press_keys', 'launch_application',
                                       'activate_window', 'create_text_file', 'set_wallpaper', 'run_terminal_command'})
        for tool in result.tools:
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
            assert tool.output_schema is not None
        result = await client.call_tool('get_capabilities')
        assert not result.is_error
        assert result.structured_content['mode'] == 'read-only'
    run_client(check, mode=mode)


def test_file_reads_searches_and_binary_errors(tmp_path):
    text = tmp_path / 'notes.txt'
    text.write_text('hello world', encoding='utf-8')
    binary = tmp_path / 'binary.dat'
    binary.write_bytes(b'hello\0world')
    async def check(client):
        result = await client.call_tool('read_text_file', {'path': str(text), 'offset': 6})
        assert not result.is_error
        assert result.structured_content['text'] == 'world'
        result = await client.call_tool('search_files', {'path': str(tmp_path), 'pattern': '*.txt'})
        assert result.structured_content['matches'][0]['name'] == 'notes.txt'
        for args in ({'path': str(binary)}, {'path': str(tmp_path / 'missing.txt')},
                     {'path': str(text), 'offset': -1}):
            result = await client.call_tool('read_text_file', args)
            assert result.is_error
        assert text.read_text(encoding='utf-8') == 'hello world'
    run_client(check)


def test_live_wifi_result_and_failure_are_not_confused(monkeypatch):
    result = {'interfaces': [{'adapter_name': 'Wi-Fi', 'ssid': 'Actual Network'}], 'verified': True}
    monkeypatch.setattr(mcp.network, 'inspect', Mock(return_value=result))
    async def check(client):
        response = await client.call_tool('inspect_wifi')
        assert response.structured_content['interfaces'][0]['ssid'] == 'Actual Network'
        mcp.network.inspect.return_value = {'verified': False, 'error': 'Location access unavailable'}
        response = await client.call_tool('inspect_wifi')
        assert response.is_error
        assert 'Location access unavailable' in response.content[0].text
    run_client(check)


def test_read_only_backend_skips_agent_startup(monkeypatch):
    from desktop_agent import DesktopAgent
    constructor = Mock(side_effect=AssertionError('Agent startup must not run'))
    monkeypatch.setattr(DesktopAgent, '__init__', constructor)
    check = Mock()
    first = mcp._inspection(check)
    second = mcp._inspection(check)
    assert first.check is check
    assert first.args.read_only
    first.windows['test'] = object()
    assert second.windows == {}
    assert not hasattr(first, 'log_path')
    constructor.assert_not_called()


def test_window_inspection_never_activates_or_clicks(monkeypatch):
    import uiautomation as auto
    window = Mock()
    backend = Mock()
    backend.windows = {'123': window}
    backend.inspect_elements.return_value = {'window': 'Test', 'elements': [{'name': str(n)} for n in range(5)], 'truncated': False}
    monkeypatch.setattr(mcp, '_inspection', lambda check: backend)
    monkeypatch.setattr(auto, 'UIAutomationInitializerInThread', nullcontext)
    async def check(client):
        result = await client.call_tool('inspect_window', {'window_id': '123', 'max_elements': 2})
        assert not result.is_error
        assert len(result.structured_content['elements']) == 2
        assert result.structured_content['truncated'] is True
        window.SetActive.assert_not_called()
        window.Click.assert_not_called()
        backend.activate_window.assert_not_called()
        result = await client.call_tool('inspect_window', {'window_id': 'missing'})
        assert result.is_error
    run_client(check)


def test_cancellation_stops_cooperative_worker():
    started = threading.Event()
    stopped = threading.Event()
    def work(check):
        started.set()
        try:
            while True:
                check()
                stopped.wait(0.01)
        finally:
            stopped.set()
    async def run():
        task = asyncio.create_task(mcp._run(work))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(stopped.wait, 2)
    asyncio.run(run())


def test_request_timeout_is_reported(monkeypatch):
    monkeypatch.setattr(mcp, 'CALL_TIMEOUT', 0.03)
    stopped = threading.Event()
    def work(check):
        try:
            while True:
                check()
                stopped.wait(0.005)
        finally:
            stopped.set()
    async def run():
        with pytest.raises(ToolError):
            await mcp._run(work)
        assert await asyncio.to_thread(stopped.wait, 2)
    asyncio.run(run())


def test_stdio_server_roundtrip(tmp_path):
    params = StdioServerParameters(
        command=sys.executable, args=['-m', 'windows_agent.mcp_server'],
        cwd=str(tmp_path), env={'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(PROJECT_ROOT)},
    )
    async def run():
        async with Client(params, cache=None, read_timeout_seconds=10) as client:
            catalog = await client.list_tools()
            assert 'inspect_wifi' in {tool.name for tool in catalog.tools}
            result = await client.call_tool('get_capabilities')
            assert not result.is_error
            assert result.structured_content['model_required'] is False
    asyncio.run(run())
