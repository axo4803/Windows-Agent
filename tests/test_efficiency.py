"""Regression checks for routed tools, batches, cancellable queries, and replay."""
import base64
import ctypes
import json
import queue
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import desktop_agent
from windows_agent import powershell, runtime
from windows_agent.tools import system, visual


def agent():
    instance = desktop_agent.DesktopAgent.__new__(desktop_agent.DesktopAgent)
    instance.args = SimpleNamespace(read_only=False, online_docs=False, max_tools=10)
    instance.check = Mock()
    instance.calls = 0
    return instance


def test_wifi_remains_available_with_routing():
    args = SimpleNamespace(read_only=True, online_docs=False, dynamic_routing=True)
    tools = runtime.select_tools_for_task(desktop_agent.TOOLS, args, 'Tell me which wireless network I am connected to')
    names = {tool['function']['name'] for tool in tools}
    assert 'inspect_wifi' in names
    assert 'batch_actions' not in names
    assert 'create_text_file' not in names


def test_batch_consumes_budget_for_each_step():
    instance = agent()
    instance.args.max_tools = 3
    instance.inspect_system = Mock(return_value={'verified': True})
    result = instance._execute_tool('batch_actions', {'actions': [{'tool': 'inspect_system'}] * 3})
    assert result['batch_completed'] is False
    assert result['stopped_at_step'] == 3
    assert instance.calls == 3  # Wrapper plus two steps.
    assert instance.inspect_system.call_count == 2


def test_batch_denial_cannot_be_retried_as_direct_call():
    instance = agent()
    instance.create_text_file = Mock(side_effect=PermissionError('User denied'))
    args = {'path': 'notes.txt', 'text': 'hello'}
    result = instance.batch_actions([{'tool': 'create_text_file', 'arguments': args}])
    assert result['denied'] is True
    assert instance._execute_tool('create_text_file', args)['denied'] is True
    instance.create_text_file.assert_called_once()


def test_batch_reuses_failure_limits_and_validates_all_steps():
    instance = agent()
    instance.inspect_system = Mock(side_effect=ValueError('Unavailable'))
    instance._execute_tool('inspect_system', {})
    instance.batch_actions([{'tool': 'inspect_system'}])
    assert instance._execute_tool('inspect_system', {})['retry_blocked'] is True
    assert instance.inspect_system.call_count == 2
    with pytest.raises(ValueError):
        instance.batch_actions([{'tool': 'inspect_system'}, {'tool': 'batch_actions'}])
    assert instance.inspect_system.call_count == 2


def test_batch_retains_real_results_without_claiming_task_verification():
    instance = agent()
    instance.inspect_wifi = Mock(return_value={'verified': True, 'interfaces': [{'ssid': 'Actual Network'}]})
    result = instance.batch_actions([{'tool': 'inspect_wifi'}])
    assert result['steps'][0]['result']['interfaces'][0]['ssid'] == 'Actual Network'
    assert 'verified' not in result


def test_fast_review_requires_verified_evidence_and_single_inspection():
    args = SimpleNamespace(fast_review=True)
    evidence = [{'tool': 'inspect_wifi', 'outcome': 'returned', 'summary': {'verified': True}}]
    assert runtime.should_skip_completion_review('What Wi-Fi is connected?', 'Home', evidence, args)
    assert not runtime.should_skip_completion_review('What Wi-Fi is connected?', 'Home', [], args)
    assert not runtime.should_skip_completion_review('Check Wi-Fi and storage', 'Home', evidence, args)
    evidence[0]['summary'] = {'adapter_name': 'Wi-Fi'}
    assert not runtime.should_skip_completion_review('What Wi-Fi is connected?', 'Wi-Fi', evidence, args)


def test_replay_does_not_promote_partial_failure_or_unverified_returns(tmp_path, monkeypatch):
    monkeypatch.setenv('SCREEN_USE_HOME', str(tmp_path))
    runtime.record_task_trajectory('Check GPU', [{'tool': 'inspect_gpu', 'outcome': 'returned', 'summary': {}}], success=True)
    runtime.record_task_trajectory('Check GPU', [{'tool': 'inspect_gpu', 'outcome': 'error', 'summary': {'verified': True}}], success=True)
    assert runtime.recall_task_trajectories('Check GPU') == []
    runtime.clear_task_trajectories()
    assert json.loads(runtime._trajectory_path().read_text()) == []


def fake_shell(output=None):
    shell = powershell.PersistentPowerShell()
    process = Mock()
    process.poll.return_value = None
    shell._proc = process
    shell._output = queue.Queue()
    shell._ensure_proc = Mock(return_value=process)
    if output is not None:
        def write(statement):
            marker = re.search(r'__AGENT_EOF_[a-f0-9]+__', statement).group()
            shell._output.put(output + '\n')
            shell._output.put(marker + '\n')
        process.stdin.write.side_effect = write
    return shell, process


def test_powershell_timeout_does_not_block_on_readline():
    shell, process = fake_shell()
    with pytest.raises(TimeoutError):
        shell.run_script('Get-Date', timeout=0.03)
    process.kill.assert_called_once()
    assert shell._lock.acquire(blocking=False)
    shell._lock.release()


def test_powershell_cancellation_closes_process_and_releases_lock():
    shell, process = fake_shell()
    check = Mock(side_effect=[None, None, None, KeyboardInterrupt])
    with pytest.raises(KeyboardInterrupt):
        shell.run_script('Get-Date', check=check, timeout=1)
    process.kill.assert_called_once()
    assert shell._lock.acquire(blocking=False)
    shell._lock.release()


def test_powershell_errors_are_not_successful_query_results():
    shell, _ = fake_shell('{"__ps_error":"No access"}')
    assert shell.run_script('Get-Date', timeout=1) is None
    shell.close()


def test_powershell_lock_wait_honors_check_without_killing_another_query():
    shell, process = fake_shell()
    shell._lock.acquire()
    try:
        with pytest.raises(TimeoutError):
            shell.run_script('Get-Date', timeout=0.01)
        process.kill.assert_not_called()
    finally:
        shell._lock.release()
        shell.close()


def test_nvml_uses_full_width_handles_and_shuts_down(monkeypatch):
    dll = Mock()
    dll.nvmlInit_v2.return_value = 0
    dll.nvmlDeviceGetCount_v2.side_effect = lambda pointer: (setattr(pointer._obj, 'value', 1) or 0)
    dll.nvmlSystemGetDriverVersion.side_effect = lambda buf, size: (setattr(buf, 'value', b'test-driver') or 0)
    dll.nvmlDeviceGetHandleByIndex_v2.side_effect = lambda index, pointer: (setattr(pointer._obj, 'value', 0x100000042) or 0)
    dll.nvmlDeviceGetName.side_effect = lambda handle, buf, size: (setattr(buf, 'value', b'Example GPU') or 0)
    def memory(handle, pointer):
        assert handle.value == 0x100000042
        pointer._obj.total = 8 * 1024**3
        pointer._obj.used = 1024**3
        pointer._obj.free = 7 * 1024**3
        return 0
    dll.nvmlDeviceGetMemoryInfo.side_effect = memory
    dll.nvmlDeviceGetUtilizationRates.return_value = 1
    dll.nvmlDeviceGetTemperature.return_value = 1
    dll.nvmlDeviceGetPowerUsage.return_value = 1
    monkeypatch.setattr(system.Path, 'exists', lambda path: True)
    monkeypatch.setattr(system.ctypes, 'WinDLL', lambda path: dll)
    result = system.query_nvml_gpus()
    assert result[0]['vram_total_mb'] == 8192
    assert dll.nvmlDeviceGetMemoryInfo.argtypes[0] is ctypes.c_void_p
    dll.nvmlShutdown.assert_called_once()


def test_visual_capture_has_real_image_and_correct_element_shape(monkeypatch):
    from PIL import Image
    from screen_use.perception import screen, uia_tree
    element = SimpleNamespace(to_dict=lambda: {'id': 1, 'name': 'Example', 'bbox': [0, 0, 20, 20]})
    monkeypatch.setattr(uia_tree, 'list_ui_elements', lambda: [element])
    monkeypatch.setattr(screen, 'screenshot', lambda: Image.new('RGB', (100, 100)))
    from screen_use.perception import som
    monkeypatch.setattr(som, 'annotate', lambda image, elements, scale: image)
    result = visual.inspect_screen('Find Example')
    assert base64.b64decode(result['image_base64']).startswith(b'\xff\xd8')
    assert result['elements'][0]['name'] == 'Example'
