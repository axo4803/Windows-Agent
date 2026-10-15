"""Neural tool safety, task facts, stalled loops, and registered-app startup."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

import desktop_agent
from windows_agent import runtime
from windows_agent.tools import files


@pytest.fixture
def installed(tmp_path, monkeypatch):
    program_files = tmp_path / 'programs'
    resources = program_files / 'Upscayl/resources'
    (resources / 'bin').mkdir(parents=True)
    (resources / 'models').mkdir()
    (resources / 'bin/upscayl-bin.exe').touch()
    for extension in ('.bin', '.param'):
        (resources / 'models' / ('digital-art-4x' + extension)).touch()
    monkeypatch.setenv('ProgramFiles', str(program_files))
    source = tmp_path / 'source.jfif'
    Image.new('RGB', (30, 20), 'blue').save(source, format='JPEG')
    return source, tmp_path / 'output.png'


def fake_engine(monkeypatch, scale=2, returncode=0):
    process = Mock()
    process.wait.return_value = returncode
    process.poll.return_value = returncode
    def launch(command, **kwargs):
        assert command[command.index('-n') + 1] == 'digital-art-4x'
        assert command[command.index('-z') + 1] == '4'
        assert command[command.index('-s') + 1] == '2'
        source = Path(command[command.index('-i') + 1])
        assert source.suffix == '.jpg'
        with Image.open(source) as image:
            Image.new('RGB', (image.width * scale, image.height * scale)).save(command[command.index('-o') + 1])
        return process
    factory = Mock(side_effect=launch)
    monkeypatch.setattr(files.subprocess, 'Popen', factory)
    return process, factory


def test_upscale_normalizes_jfif_without_changing_original(installed, monkeypatch):
    source, output = installed
    original = source.read_bytes()
    fake_engine(monkeypatch)
    result = files.upscale_image(str(source), str(output), lambda: None)
    assert result['verified'] and (result['width'], result['height']) == (60, 40)
    assert source.read_bytes() == original
    assert Image.open(output).size == (60, 40)
    assert not list(output.parent.glob('.windows-agent-upscale-*'))


@pytest.mark.parametrize('scale, code', [(4, 0), (2, 1)])
def test_engine_error_or_wrong_dimensions_never_publishes(installed, monkeypatch, scale, code):
    source, output = installed
    fake_engine(monkeypatch, scale=scale, returncode=code)
    with pytest.raises(RuntimeError):
        files.upscale_image(str(source), str(output), lambda: None)
    assert not output.exists()


def test_existing_destination_blocks_engine(installed, monkeypatch):
    source, output = installed
    output.write_bytes(b'keep')
    _, factory = fake_engine(monkeypatch)
    with pytest.raises(ValueError):
        files.upscale_image(str(source), str(output), lambda: None)
    assert output.read_bytes() == b'keep'
    factory.assert_not_called()


def test_stop_kills_engine_and_does_not_publish(installed, monkeypatch):
    source, output = installed
    process, factory = fake_engine(monkeypatch)
    process.poll.return_value = None
    def check():
        if factory.called:
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        files.upscale_image(str(source), str(output), check)
    process.kill.assert_called_once()
    assert not output.exists()


def test_upscale_timeout_kills_engine(installed, monkeypatch):
    source, output = installed
    process, _ = fake_engine(monkeypatch)
    process.poll.return_value = None
    clock = iter([0, 301])
    monkeypatch.setattr(files.time, 'monotonic', lambda: next(clock))
    with pytest.raises(TimeoutError):
        files.upscale_image(str(source), str(output), lambda: None)
    process.kill.assert_called_once()
    assert not output.exists()


def test_jfif_metadata_is_available_to_agent(installed):
    source, _ = installed
    result = files.inspect_images(str(source), lambda: None)
    assert len(result['images']) == 1
    assert result['images'][0]['width'] == 30


def test_file_path_survives_a_long_task():
    path = 'C:/Users/Test/Downloads/UpscaleTest.jfif'
    summary = desktop_agent.DesktopAgent.brief_result({'base_directory':'C:/Users/Test/Downloads', 'pattern':'UpscaleTest*', 'matches':[{'path':path, 'name':'UpscaleTest.jfif'}]})
    evidence = [{'tool':'search_files', 'arguments':{}, 'summary':summary}]
    evidence += [{'tool':'inspect_elements', 'arguments':{}, 'summary':{'elements':['x' * 500]}}] * 30
    encoded = runtime.progress(evidence)
    assert path in encoded and len(encoded) <= 3500


def test_repeated_reads_are_cached_but_live_inspections_are_not():
    guard = runtime.ProgressGuard()
    result = {'matches':[{'path':'C:/file.png'}]}
    args = {'path':'C:/', 'pattern':'file*'}
    guard.observe('search_files', args, result)
    cached = guard.cached('search_files', args)
    assert cached['cached_result'] == result and cached['verified'] is False
    assert runtime.outcome(cached) == 'cached'
    for _ in range(4):
        guard.observe('search_files', args, cached)
    assert guard.stalled == 4
    assert guard.cached('inspect_wifi', {}) is None
    guard.observe('click_element', {'element_id':'1:2'}, {'clicked':True})
    assert guard.cached('search_files', args) is None


def test_file_read_cache_expires(monkeypatch):
    clock = [0]
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: clock[0])
    guard = runtime.ProgressGuard()
    guard.observe('search_files', {}, {'matches':[]})
    clock[0] = 16
    assert guard.cached('search_files', {}) is None


def app_agent():
    agent = desktop_agent.DesktopAgent.__new__(desktop_agent.DesktopAgent)
    agent.args = SimpleNamespace(read_only=False)
    agent.discovered_apps = {'app:test':{'name':'Upscayl','app_id':'C:/Programs/Upscayl.exe'}}
    agent.windows = {}
    agent.launches = 0
    agent.check = Mock()
    agent.stop = Mock()
    agent.process_path = Mock(return_value='C:/Programs/Upscayl.exe')
    agent.activate_window = Mock(return_value={'activated':'Upscayl','elements_preview':[]})
    return agent


def test_registered_window_is_reused_before_launch(monkeypatch):
    agent = app_agent()
    agent.windows['123'] = SimpleNamespace(ProcessId=1)
    agent.discover_applications = Mock(return_value={'windows':[{'window_id':'123','title':'Upscayl'}]})
    monkeypatch.setattr(desktop_agent.app_catalog, 'catalog', lambda check: agent.discovered_apps)
    launch = Mock()
    monkeypatch.setattr(desktop_agent.app_catalog, 'launch', launch)
    result = agent.launch_application('app:test')
    assert result['reused'] and result['active_window_id'] == '123'
    launch.assert_not_called()


def test_slow_registered_app_is_launched_only_once(monkeypatch):
    agent = app_agent()
    agent.discover_applications = Mock(return_value={'windows':[]})
    monkeypatch.setattr(desktop_agent.app_catalog, 'catalog', lambda check: agent.discovered_apps)
    launch = Mock()
    monkeypatch.setattr(desktop_agent.app_catalog, 'launch', launch)
    assert agent.launch_application('app:test')['startup_pending']
    assert agent.launch_application('app:test')['startup_pending']
    launch.assert_called_once()


def test_upscale_is_unavailable_in_read_only_mode():
    args = SimpleNamespace(read_only=True, online_docs=False)
    assert 'upscale_image' not in {t['function']['name'] for t in runtime.available_tools(desktop_agent.TOOLS, args)}


def test_copy_cannot_precreate_mislabelled_upscale_output(tmp_path):
    source, target = tmp_path / 'source.jfif', tmp_path / 'output.png'
    Image.new('RGB', (10, 10)).save(source, format='JPEG')
    with pytest.raises(ValueError, match='does not convert'):
        files.copy_file(str(source), str(target))
    assert not target.exists()
    assert files.copy_file(str(source), str(target.with_suffix('.jpg')))['source_retained']


def test_batch_wrapper_does_not_erase_inner_read_stall():
    guard = runtime.ProgressGuard()
    guard.stalled = 4
    guard.observe('batch_actions', {'actions':[]}, {'steps':[{'outcome':'cached'}]})
    assert guard.stalled == 4


def test_unfinished_verification_is_not_a_final_answer():
    assert runtime.unfinished_reply('I need to verify the completion of each requested outcome against the evidence:')
    assert not runtime.unfinished_reply('Created C:/Downloads/output.png at 2752x1536. Original retained.')
