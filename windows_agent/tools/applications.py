"""Launch only opaque IDs drawn from Windows' registered application catalog."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from ..powershell import PS_DAEMON

BLOCKED = re.compile(r'powershell|terminal|command prompt|\bcmd\b|python|wscript|cscript|regedit|registry editor|uninstall|reset|recover|format|erase|wipe', re.I)
SCRIPT = 'Get-StartApps | Select-Object -First 500 Name,AppID | ConvertTo-Json -Compress'


def _parse_catalog(output: str) -> dict:
    rows = json.loads((output or '').lstrip('\ufeff') or '[]')
    if isinstance(rows, dict):
        rows = [rows]
    result = {}
    for row in rows:
        name, app_id = row.get('Name', ''), row.get('AppID', '')
        if not name or not app_id or BLOCKED.search(name + ' ' + app_id):
            continue
        key = 'app:' + hashlib.sha256(app_id.encode('utf-8')).hexdigest()[:16]
        result[key] = {'name': name, 'app_id': app_id}
    return result


def catalog(check):
    check()
    warm_out = PS_DAEMON.run_script(SCRIPT, check=check, timeout=15.0)
    if warm_out:
        try:
            return _parse_catalog(warm_out)
        except Exception:
            pass
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ' + SCRIPT
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen([str(executable), '-NoProfile', '-NonInteractive', '-Command', script],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                               creationflags=creationflags)
    deadline = time.monotonic() + 20
    try:
        while True:
            check()
            if time.monotonic() > deadline:
                raise TimeoutError('Application discovery exceeded 20 seconds')
            try:
                output, error = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pass
        if process.returncode:
            raise RuntimeError(error.strip()[:500])
        return _parse_catalog(output)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def launch(entry):
    if BLOCKED.search(entry['name'] + ' ' + entry['app_id']):
        raise ValueError('Terminal, destructive or recovery application blocked')
    os.startfile('shell:AppsFolder\\' + entry['app_id'])
