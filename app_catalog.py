"""Launch only opaque IDs drawn from Windows' registered application catalog."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

BLOCKED = re.compile(r'powershell|terminal|command prompt|\bcmd\b|python|wscript|cscript|regedit|registry editor|uninstall|reset|recover|format|erase|wipe', re.I)

def catalog(check):
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-StartApps | Select-Object -First 500 Name,AppID | ConvertTo-Json -Compress'
    process = subprocess.Popen([str(executable), '-NoProfile', '-NonInteractive', '-Command', script],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                               creationflags=subprocess.CREATE_NO_WINDOW)
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
        rows = json.loads(output.lstrip('\ufeff') or '[]')
        if isinstance(rows, dict):
            rows = [rows]
        result = {}
        for row in rows:
            name, app_id = row['Name'], row['AppID']
            if BLOCKED.search(name + ' ' + app_id):
                continue
            key = 'app:' + hashlib.sha256(app_id.encode('utf-8')).hexdigest()[:16]
            result[key] = {'name': name, 'app_id': app_id}
        return result
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

def launch(entry):
    if BLOCKED.search(entry['name'] + ' ' + entry['app_id']):
        raise ValueError('Terminal, destructive or recovery application blocked')
    os.startfile('shell:AppsFolder\\' + entry['app_id'])
