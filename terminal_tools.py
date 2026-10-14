"""Basic terminal diagnostics: exact commands, fixed executables, no shell."""
import locale
import os
from pathlib import Path
import subprocess
import time

COMMANDS = {
    'hostname': ('hostname.exe',),
    'whoami': ('whoami.exe',),
    'ipconfig': ('ipconfig.exe',),
    'ipconfig /all': ('ipconfig.exe', '/all'),
    'tasklist': ('tasklist.exe', '/FO', 'CSV'),
    'systeminfo': ('systeminfo.exe', '/FO', 'LIST'),
}

def arguments(command):
    if command not in COMMANDS:
        raise ValueError('Unsupported terminal command. Allowed: ' + ', '.join(COMMANDS))
    fixed = COMMANDS[command]
    return [str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / fixed[0]), *fixed[1:]]

def run(command, check):
    args = arguments(command)
    check()
    process = subprocess.Popen(args, shell=False, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 25
    try:
        while True:
            check()
            if time.monotonic() > deadline:
                raise TimeoutError('Terminal diagnostic exceeded 25 seconds')
            try:
                output, error = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        encoding = locale.getpreferredencoding(False)
        return {'command': command, 'exit_code': process.returncode,
                'stdout': output.decode(encoding, errors='replace')[:12000],
                'stderr': error.decode(encoding, errors='replace')[:2000],
                'truncated': len(output.decode(encoding, errors='replace')) > 12000,
                'read_only': True}
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
