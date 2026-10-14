"""A lazy, serialized PowerShell process for fixed internal inspection scripts."""
import atexit
import base64
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid


class PersistentPowerShell:
    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._output = None

    def _ensure_proc(self):
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        self._close_unlocked()
        executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        if not executable.exists():
            return None
        proc = subprocess.Popen(
            [str(executable), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', '-'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        output = queue.Queue()
        def drain():
            try:
                for line in proc.stdout:
                    output.put(line)
            finally:
                output.put(None)
        threading.Thread(target=drain, daemon=True).start()
        self._proc, self._output = proc, output
        return proc

    def run_script(self, script, check=None, timeout=20.0):
        deadline = time.monotonic() + timeout
        def guard():
            if check is not None:
                check()
            if time.monotonic() >= deadline:
                raise TimeoutError('Persistent PowerShell inspection timed out')
        acquired = False
        try:
            while not acquired:
                guard()
                acquired = self._lock.acquire(timeout=0.05)
            guard()
            proc = self._ensure_proc()
            if proc is None:
                return None
            marker = '__AGENT_EOF_' + uuid.uuid4().hex + '__'
            encoded = base64.b64encode(script.encode('utf-8')).decode('ascii')
            # One complete statement avoids stdin's multiline-parser buffering.
            statement = (
                "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                "$ErrorActionPreference='Stop'; try { & ([ScriptBlock]::Create("
                "[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "')))) "
                "} catch { Write-Output (@{__ps_error=$_.Exception.Message} | ConvertTo-Json -Compress) }; "
                "Write-Output '" + marker + "'\n"
            )
            proc.stdin.write(statement)
            proc.stdin.flush()
            lines = []
            size = 0
            while True:
                guard()
                try:
                    line = self._output.get(timeout=0.05)
                except queue.Empty:
                    continue
                if line is None:
                    self._close_unlocked()
                    return None
                if line.strip() == marker:
                    guard()
                    out = ''.join(lines).strip()
                    if '"__ps_error"' in out:
                        return None
                    return out
                size += len(line)
                if size > 1_000_000:
                    raise ValueError('PowerShell inspection output exceeded its limit')
                lines.append(line)
        except TimeoutError:
            if acquired:
                self._close_unlocked()
            raise
        except (OSError, BrokenPipeError):
            if acquired:
                self._close_unlocked()
            return None
        except BaseException:
            if acquired:
                self._close_unlocked()
            raise
        finally:
            if acquired:
                self._lock.release()

    def _close_unlocked(self):
        proc, self._proc = self._proc, None
        self._output = None
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                return
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    stream.close()

    def close(self):
        with self._lock:
            self._close_unlocked()


PS_DAEMON = PersistentPowerShell()
atexit.register(PS_DAEMON.close)
