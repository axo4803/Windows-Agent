"""Desktop window lifecycle and host ownership, without running native UI in unit tests."""
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from windows_agent import desktop


def test_window_size_validation_and_roundtrip(tmp_path):
    path = tmp_path / 'window.json'
    assert desktop.load_size(path) == desktop.DEFAULT_SIZE
    path.write_text('{"width":true,"height":900}')
    assert desktop.load_size(path) == desktop.DEFAULT_SIZE
    desktop.save_size(1200, 750, path)
    assert desktop.load_size(path) == (1200, 750)
    desktop.save_size(1, 1, path)
    assert desktop.load_size(path) == desktop.MIN_SIZE
    assert not path.with_suffix('.tmp').exists()


@pytest.mark.parametrize('fail', [False, True])
def test_window_owns_backend_and_closes_it_on_exit_or_failure(tmp_path, fail):
    host = Mock(url='http://127.0.0.1:49123')
    factory = Mock(return_value=host)
    view = Mock(settings={})
    view.create_window.return_value = Mock(width=1100, height=700)
    if fail:
        view.start.side_effect = RuntimeError('WebView2 unavailable')
        with pytest.raises(RuntimeError):
            desktop.run_desktop(view, host_factory=factory, settings_path=tmp_path / 'size.json')
    else:
        desktop.run_desktop(view, host_factory=factory, settings_path=tmp_path / 'size.json')
    view.start.assert_called_once_with(gui='edgechromium', debug=False, private_mode=True)
    assert view.create_window.call_args.args == ('Windows-Agent', host.url)
    assert 'js_api' not in view.create_window.call_args.kwargs
    host.close.assert_called_once()


def test_failed_size_save_still_closes_backend(tmp_path, monkeypatch):
    host = Mock(url='http://127.0.0.1:1')
    view = Mock(settings={})
    view.create_window.return_value = Mock(width=1100, height=700)
    monkeypatch.setattr(desktop, 'save_size', Mock(side_effect=OSError('Read only')))
    desktop.run_desktop(view, host_factory=lambda **kwargs: host, settings_path=tmp_path / 'size.json')
    host.close.assert_called_once()


def test_host_cleanup_stops_only_its_own_session():
    server = Mock(server_address=('127.0.0.1', 49123))
    host = desktop.DesktopHost(server_factory=lambda *args, **kwargs: server)
    assert host.url == 'http://127.0.0.1:49123'
    host.close()
    host.close()
    server.session.close.assert_called_once()
    server.shutdown.assert_called_once()
    server.server_close.assert_called_once()


def test_duplicate_launch_is_blocked_then_lock_is_released(tmp_path):
    lock = tmp_path / 'desktop.lock'
    with desktop.single_instance(lock):
        with pytest.raises(RuntimeError, match='already running'):
            with desktop.single_instance(lock):
                pass
    with desktop.single_instance(lock):
        pass


@pytest.mark.parametrize('failure', [False, True])
def test_edge_window_uses_private_profile_and_releases_backend(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(desktop, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(desktop, 'edge_executable', lambda: Path('C:/Edge/msedge.exe'))
    host = Mock(url='http://127.0.0.1:49123')
    process = Mock()
    process.wait.return_value = 0
    process.poll.return_value = 0
    factory = Mock(return_value=process)
    if failure:
        factory.side_effect = OSError('Cannot launch')
        with pytest.raises(OSError):
            desktop.run_edge(host_factory=lambda **kwargs: host, process_factory=factory)
    else:
        desktop.run_edge(host_factory=lambda **kwargs: host, process_factory=factory)
        args = factory.call_args.args[0]
        assert '--app=' + host.url in args
        assert '--user-data-dir=' + str(tmp_path / 'memory/desktop-edge-profile') in args
        assert factory.call_args.kwargs['shell'] is False
    host.close.assert_called_once()


def test_startup_error_is_not_masked_by_window_size(tmp_path):
    class UninitializedWindow:
        @property
        def width(self):
            raise AttributeError('No renderer')
    view = Mock(settings={})
    view.create_window.return_value = UninitializedWindow()
    view.start.side_effect = RuntimeError('Original renderer failure')
    host = Mock(url='http://127.0.0.1:1')
    with pytest.raises(RuntimeError, match='Original renderer failure'):
        desktop.run_desktop(view, host_factory=lambda **kwargs: host, settings_path=tmp_path / 'size.json')
    host.close.assert_called_once()
