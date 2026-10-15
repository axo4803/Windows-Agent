"""Native Windows window hosting the existing local browser interface."""
import argparse
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import sys
import threading
import subprocess

from windows_agent import PROJECT_ROOT
from windows_agent.web.server import LocalServer

from windows_agent import PROJECT_ROOT
from windows_agent.web.server import LocalServer

SETTINGS = PROJECT_ROOT / 'memory' / 'desktop-window.json'
LOCK = PROJECT_ROOT / 'memory' / 'desktop-window.lock'
DEFAULT_SIZE = (1360, 860)
MIN_SIZE = (960, 640)


def load_size(path=SETTINGS):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        width, height = data['width'], data['height']
        if type(width) is int and type(height) is int and 960 <= width <= 3840 and 640 <= height <= 2160:
            return width, height
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return DEFAULT_SIZE


def save_size(width, height, path=SETTINGS):
    if type(width) is not int or type(height) is not int:
        return
    width, height = max(MIN_SIZE[0], min(width, 3840)), max(MIN_SIZE[1], min(height, 2160))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'width': width, 'height': height}), encoding='utf-8')
    temporary.replace(path)


@contextmanager
def single_instance(path=LOCK):
    """OS releases the byte lock even if the desktop process crashes."""
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if not handle.seek(0, 2):
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('Windows-Agent desktop is already running. Switch to its existing window.') from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class DesktopHost:
    def __init__(self, model='qwen3.5:4b', server_factory=LocalServer):
        # An OS-assigned port leaves an existing browser server on 8765 untouched.
        self.server = server_factory(0, default_model=model)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.1}, daemon=True)
        self.thread.start()
        self.closed = False
        self.server.desktop_size_callback = save_size

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_address[1]}'

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.server.session.close()
        finally:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)


def run_desktop(webview, model='qwen3.5:4b', host_factory=DesktopHost, settings_path=SETTINGS):
    host = host_factory(model=model)
    window = None
    try:
        width, height = load_size(settings_path)
        webview.settings['ALLOW_FILE_URLS'] = False
        window = webview.create_window('Windows-Agent', host.url, width=width, height=height,
                                      min_size=MIN_SIZE, background_color='#f8faf9', text_select=True)
        # No Python methods are exposed to page scripts; the existing HTTP controls stay in use.
        webview.start(gui='edgechromium', debug=False, private_mode=True)
    finally:
        try:
            if window is not None:
                save_size(window.width, window.height, settings_path)
        except (OSError, AttributeError, RuntimeError):
            logging.getLogger(__name__).exception('Could not save desktop window size')
        finally:
            host.close()


def report_error(message):
    logging.getLogger(__name__).error(message)
    if os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'Windows-Agent', 0x10)
    elif sys.stderr:
        print(message, file=sys.stderr)


def edge_executable():
    for variable, fallback in (('ProgramFiles(x86)', r'C:\Program Files (x86)'), ('ProgramFiles', r'C:\Program Files')):
        candidate = Path(os.environ.get(variable, fallback)) / 'Microsoft/Edge/Application/msedge.exe'
        if candidate.is_file():
            return candidate
    raise FileNotFoundError('Microsoft Edge was not found. Browser mode is available with python -m windows_agent.web.')


def run_edge(model='qwen3.5:4b', host_factory=DesktopHost, process_factory=subprocess.Popen):
    executable = edge_executable()
    width, height = load_size()
    profile = PROJECT_ROOT / 'memory' / 'desktop-edge-profile'
    profile.mkdir(parents=True, exist_ok=True)
    host = host_factory(model=model)
    process = None
    try:
        process = process_factory([str(executable), '--app=' + host.url,
                                   '--user-data-dir=' + str(profile), '--no-first-run',
                                   '--disable-background-mode', f'--window-size={width},{height}'],
                                  shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        code = process.wait()
        if code:
            raise RuntimeError(f'The desktop window exited with code {code}')
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        host.close()


def main():
    parser = argparse.ArgumentParser(description='Windows-Agent desktop interface')
    parser.add_argument('--model', default='qwen3.5:4b')
    parser.add_argument('--renderer', choices=['edge', 'webview'], default='edge')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.exit(1, 'The desktop launcher requires Windows. Use python -m windows_agent.web for browser mode.\n')
    log_dir = PROJECT_ROOT / 'logs'
    log_dir.mkdir(exist_ok=True)
    stream = None
    if sys.stdout is None or sys.stderr is None:
        stream = (log_dir / 'desktop-app.log').open('a', encoding='utf-8', buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    try:
        with single_instance():
            if args.renderer == 'edge':
                run_edge(model=args.model)
            else:
                import webview
                run_desktop(webview, model=args.model)
        return 0
    except Exception as exc:
        logging.getLogger(__name__).exception('Desktop startup or shutdown failed')
        report_error(f'Could not start Windows-Agent: {exc}\n\nDetails are in logs/desktop-app.log when launched without a terminal. Browser mode is also available with python -m windows_agent.web.')
        return 1
    finally:
        if stream:
            stream.flush()


if __name__ == '__main__':
    raise SystemExit(main())
