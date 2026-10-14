"""Bounded, local system inventory and explicitly supplied user memory."""
import json
import os
import re
from pathlib import Path
import subprocess
import time
import storage_info

ROOT = Path(__file__).resolve().parent / 'memory'
PROFILE = ROOT / 'system-profile.json'
NOTES = ROOT / 'user-notes.json'
AUTO_MEMORY = ROOT / 'agent-memories.json'
SECTIONS = ('hardware', 'applications', 'services', 'startup', 'network', 'processes', 'folders', 'storage')
INVENTORY = r'''
$ErrorActionPreference = 'Stop'
function Collect($block) { try { @(& $block) } catch { @{ error = $_.Exception.Message } } }
$result = @{
 applications = Collect {
  Get-ChildItem 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall','HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue |
  ForEach-Object { try { Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction Stop } catch {} } |
  Where-Object DisplayName | Sort-Object DisplayName -Unique | Select-Object -First 250 DisplayName,DisplayVersion,Publisher
 }
 services = Collect { Get-CimInstance Win32_Service | Sort-Object Name | Select-Object -First 250 Name,State,StartMode }
 startup = Collect { Get-CimInstance Win32_StartupCommand | Select-Object -First 100 Name,Location }
 network = Collect { Get-NetAdapter | Select-Object -First 32 Name,InterfaceDescription,Status,LinkSpeed }
 processes = Collect { Get-Process | Sort-Object ProcessName -Unique | Select-Object -First 150 ProcessName }
 cpu = Collect { Get-CimInstance Win32_Processor | Select-Object -First 4 Name,NumberOfCores,NumberOfLogicalProcessors }
}
$result | ConvertTo-Json -Depth 6 -Compress
'''

def load(path):
    try:
        if path.stat().st_size > 1_000_000:
            return {}
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}

def save(path, data):
    ROOT.mkdir(exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)

def refresh(agent):
    agent.check()
    hardware = agent.inspect_system()
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; ' + INVENTORY
    process = subprocess.Popen([str(executable), '-NoProfile', '-NonInteractive', '-Command', script],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                               creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 30
    try:
        while True:
            agent.check()
            if time.monotonic() >= deadline:
                raise TimeoutError('System inventory took longer than 30 seconds')
            try:
                output, error = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise RuntimeError(error.strip()[:500])
        data = json.loads(output)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    hardware['cpu_details'] = data.pop('cpu', [])
    data['hardware'] = hardware
    try:
        data['storage'] = storage_info.inspect(agent.check)
    except Exception as exc:
        data['storage'] = {'error': str(exc)}
    data['folders'] = {name: str(Path.home() / name) for name in ('Desktop', 'Documents', 'Downloads', 'Pictures', 'Videos') if (Path.home() / name).exists()}
    data['folders']['agent_project'] = str(Path(__file__).resolve().parent)
    data['captured_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    data['unavailable_sections'] = [key for key, value in data.items() if isinstance(value, dict) and 'error' in value]
    data['notice'] = 'Read-only snapshot; lists are capped. Installed apps cover uninstall registry entries only. Folder paths are candidates, not a recursive index. Verify changing state live.'
    save(PROFILE, data)
    return data

def read_section(section='hardware'):
    if section not in SECTIONS:
        raise ValueError('Unknown profile section')
    data = load(PROFILE)
    return {'captured_at': data.get('captured_at'), 'section': section, 'data': data.get(section), 'notice': data.get('notice', 'Run /refresh-system first')}

def context(task):
    data = load(PROFILE)
    selected = {'captured_at': data.get('captured_at'), 'hardware': data.get('hardware'), 'folders': data.get('folders')}
    for section, keywords in {'applications': ('app', 'installed', 'software'), 'services': ('service',), 'startup': ('startup', 'boot'), 'network': ('network', 'connection', 'internet'), 'processes': ('running', 'process')}.items():
        if any(word in task.lower() for word in keywords):
            selected[section] = data.get(section)
    words = set(re.findall(r'\w{3,}', task.lower()))
    memories = load(AUTO_MEMORY).get('memories', [])
    relevant = [m for m in memories if m.get('category') == 'preference' or words.intersection(re.findall(r'\w{3,}', m.get('fact', '').lower()))]
    relevant.sort(key=lambda m: len(words.intersection(re.findall(r'\w{3,}', m.get('fact', '').lower()))), reverse=True)
    return json.dumps({'snapshot': json.dumps(selected, ensure_ascii=False)[:2500],
                       'user_notes': [str(n)[:500] for n in load(NOTES).get('notes', [])[-4:]],
                       'agent_memories': relevant[:4]}, ensure_ascii=False)

def remember_fact(fact, category, source):
    fact = fact.strip()
    if not fact or len(fact) > 500:
        raise ValueError('A memory must contain 1 to 500 characters')
    if category not in {'preference', 'system_fact', 'path', 'resolution'} or source not in {'user', 'local_tool'}:
        raise ValueError('Invalid memory category or source')
    if re.search(r'password|credential|secret|api[ _-]?key|access[ _-]?token|private[ _-]?key|BEGIN .*KEY', fact, re.I):
        raise ValueError('Do not store credentials or secrets in memory')
    memories = load(AUTO_MEMORY).get('memories', [])
    if any(m.get('fact', '').casefold() == fact.casefold() for m in memories):
        return {'memory': 'already saved', 'fact': fact}
    memories.append({'fact': fact, 'category': category, 'source': source,
                     'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')})
    save(AUTO_MEMORY, {'memories': memories[-30:]})
    return {'memory': 'saved locally', 'fact': fact}

def remember(note):
    notes = load(NOTES).get('notes', [])
    if len(note) > 1000 or len(notes) >= 30:
        raise ValueError('Memory is limited to 30 notes of 1000 characters each')
    notes.append(note)
    save(NOTES, {'notes': notes})
