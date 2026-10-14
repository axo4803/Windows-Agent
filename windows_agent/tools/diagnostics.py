"""Safe, read-only terminal diagnostics: fixed executables, no shell injection."""
import locale
import os
from pathlib import Path
import shutil
import subprocess
import time

COMMANDS = {
    'hostname': ('hostname.exe',),
    'whoami': ('whoami.exe',),
    'ipconfig': ('ipconfig.exe',),
    'ipconfig /all': ('ipconfig.exe', '/all'),
    'netsh wlan show interfaces': ('netsh.exe', 'wlan', 'show', 'interfaces'),
    'tasklist': ('tasklist.exe', '/FO', 'CSV'),
    'systeminfo': ('systeminfo.exe', '/FO', 'LIST'),
    'nvidia-smi': ('nvidia-smi.exe',),
    'nvidia-smi -L': ('nvidia-smi.exe', '-L'),
    'nvidia-smi --query-gpu': (
        'nvidia-smi.exe',
        '--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw,driver_version',
        '--format=csv,noheader,nounits',
    ),
    'ollama list': ('ollama.exe', 'list'),
    'ollama ps': ('ollama.exe', 'ps'),
    'netstat -ano': ('netstat.exe', '-ano'),
    'arp -a': ('arp.exe', '-a'),
    'route print': ('route.exe', 'print'),
    'driverquery': ('driverquery.exe', '/FO', 'CSV'),
    'powercfg /list': ('powercfg.exe', '/list'),
    'wmic cpu': ('wbem/wmic.exe', 'cpu', 'get', 'Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed', '/format:list'),
    'wmic memorychip': ('wbem/wmic.exe', 'memorychip', 'get', 'BankLabel,Capacity,Speed,Manufacturer,PartNumber', '/format:list'),
    'wmic gpu': ('wbem/wmic.exe', 'path', 'win32_videocontroller', 'get', 'Name,AdapterRAM,DriverVersion,VideoModeDescription', '/format:list'),
    'wmic diskdrive': ('wbem/wmic.exe', 'diskdrive', 'get', 'Model,Size,Status,InterfaceType,MediaType', '/format:list'),
}

ALIASES = {
    'nvidia-smi --query': 'nvidia-smi --query-gpu',
    'wmic memorychip get capacity,speed,manufacturer,partnumber': 'wmic memorychip',
    'wmic cpu get name,numberofcores,numberoflogicalprocessors,maxclockspeed': 'wmic cpu',
    'wmic path win32_videocontroller get name,adapterram,driverversion,videomodedescription': 'wmic gpu',
    'wmic diskdrive get model,size,status,interfacetype,mediatype': 'wmic diskdrive',
}


def _resolve_executable(exe_name):
    sys32 = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32'
    candidate = sys32 / exe_name
    if candidate.exists():
        return str(candidate)
    if exe_name.lower() == 'nvidia-smi.exe':
        nv_path = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'NVIDIA Corporation' / 'NVSMI' / 'nvidia-smi.exe'
        if nv_path.exists():
            return str(nv_path)
    if exe_name.lower() == 'ollama.exe':
        local_app = os.environ.get('LOCALAPPDATA', '')
        if local_app:
            ol_path = Path(local_app) / 'Programs' / 'Ollama' / 'ollama.exe'
            if ol_path.exists():
                return str(ol_path)
    found = shutil.which(exe_name)
    if found:
        return found
    return str(candidate)


def normalize_command(command):
    cleaned = ' '.join((command or '').strip().split())
    lower = cleaned.casefold()
    for canonical in COMMANDS:
        if lower == canonical.casefold():
            return canonical
    if lower in ALIASES:
        return ALIASES[lower]
    if lower.startswith('nvidia-smi') and '--query-gpu' in lower:
        return 'nvidia-smi --query-gpu'
    return cleaned


def arguments(command):
    canonical = normalize_command(command)
    if canonical not in COMMANDS:
        raise ValueError('Unsupported terminal command. Allowed: ' + ', '.join(COMMANDS))
    fixed = COMMANDS[canonical]
    return [_resolve_executable(fixed[0]), *fixed[1:]]


def run(command, check):
    canonical = normalize_command(command)
    args = arguments(canonical)
    check()
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen(args, shell=False, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=creationflags)
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
        decoded_out = output.decode(encoding, errors='replace')
        decoded_err = error.decode(encoding, errors='replace')
        return {'command': canonical, 'exit_code': process.returncode,
                'stdout': decoded_out[:16000],
                'stderr': decoded_err[:2000],
                'truncated': len(decoded_out) > 16000,
                'read_only': True}
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
