"""Bounded, local system inventory and explicitly supplied user memory."""
import json
import ctypes
import os
import re
from pathlib import Path
import subprocess
import time
from .. import PROJECT_ROOT
from ..powershell import PS_DAEMON
from .. import runtime

ROOT = PROJECT_ROOT / 'memory'
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
 cpu = Collect { Get-CimInstance Win32_Processor | Select-Object -First 4 Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed }
 memory_modules = Collect { Get-CimInstance Win32_PhysicalMemory | Select-Object -First 16 BankLabel,Capacity,Speed,ConfiguredClockSpeed,Manufacturer,PartNumber }
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
    data = None
    warm_out = PS_DAEMON.run_script(INVENTORY, check=agent.check, timeout=25.0)
    if warm_out:
        try:
            data = json.loads(warm_out.lstrip('\ufeff'))
        except Exception:
            data = None
    if data is None:
        executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        script = '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; ' + INVENTORY
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        process = subprocess.Popen([str(executable), '-NoProfile', '-NonInteractive', '-Command', script],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                   creationflags=creationflags)
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
    hardware['memory_modules'] = data.pop('memory_modules', [])
    data['hardware'] = hardware
    try:
        data['storage'] = inspect_storage(agent.check)
    except Exception as exc:
        data['storage'] = {'error': str(exc)}
    data['folders'] = {name: str(Path.home() / name) for name in ('Desktop', 'Documents', 'Downloads', 'Pictures', 'Videos') if (Path.home() / name).exists()}
    data['folders']['agent_project'] = str(PROJECT_ROOT)
    data['captured_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    data['unavailable_sections'] = [key for key, value in data.items() if isinstance(value, dict) and 'error' in value]
    data['notice'] = 'Read-only snapshot; lists are capped. Installed apps cover uninstall registry entries only. Folder paths are candidates, not a recursive index. Verify changing state live.'
    save(PROFILE, data)
    return data

def read_section(section='hardware'):
    if section not in SECTIONS:
        raise ValueError('Unknown profile section')
    data = load(PROFILE)
    notice = data.get('notice', 'Run /refresh-system first')
    if section == 'network':
        notice += ' Network Name is an adapter label, NOT an SSID. Use inspect_wifi for the current connected network name.'
    return {'captured_at': data.get('captured_at'), 'section': section, 'data': data.get(section), 'notice': notice}

def context(task):
    data = load(PROFILE)
    selected = {'captured_at': data.get('captured_at'), 'hardware': data.get('hardware'), 'folders': data.get('folders')}
    keyword_map = {
        'applications': ('app', 'installed', 'software', 'program', 'tool'),
        'services': ('service', 'daemon'),
        'startup': ('startup', 'boot', 'autorun'),
        'network': ('network', 'connection', 'internet', 'wifi', 'wi-fi', 'ethernet', 'adapter'),
        'processes': ('running', 'process', 'task'),
        'storage': ('drive', 'disk', 'ssd', 'hdd', 'nvme', 'storage', 'partition', 'migrate', 'health', 'smart'),
    }
    for section, keywords in keyword_map.items():
        if any(word in task.lower() for word in keywords):
            selected[section] = data.get(section)
            if section == 'network':
                selected['network_notice'] = 'Adapter Name is NOT an SSID. This is a saved inventory; inspect_wifi reads the current connection.'
    words = set(re.findall(r'\w{3,}', task.lower()))
    memories = load(AUTO_MEMORY).get('memories', [])
    relevant = [m for m in memories if m.get('category') == 'preference' or words.intersection(re.findall(r'\w{3,}', m.get('fact', '').lower()))]
    relevant.sort(key=lambda m: len(words.intersection(re.findall(r'\w{3,}', m.get('fact', '').lower()))), reverse=True)
    return json.dumps({'snapshot': json.dumps(selected, ensure_ascii=False)[:3200],
                       'user_notes': [str(n)[:500] for n in load(NOTES).get('notes', [])[-4:]],
                       'agent_memories': relevant[:5],
                       'experience_replay_hints': runtime.recall_task_trajectories(task)}, ensure_ascii=False)

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
    save(AUTO_MEMORY, {'memories': memories[-40:]})
    return {'memory': 'saved locally', 'fact': fact}

def remember(note):
    notes = load(NOTES).get('notes', [])
    if len(note) > 1000 or len(notes) >= 30:
        raise ValueError('Memory is limited to 30 notes of 1000 characters each')
    notes.append(note)
    save(NOTES, {'notes': notes})


STORAGE_SCRIPT = r'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$physical = @()
$errors = @()
try { $physical = @(Get-PhysicalDisk) } catch { $errors += $_.Exception.Message }
$disks = @()
try {
 foreach ($disk in @(Get-Disk | Select-Object -First 32)) {
  $match = @()
  $basis = 'unavailable'
  if ($disk.SerialNumber -and $disk.SerialNumber.Trim()) {
   $match = @($physical | Where-Object { $_.SerialNumber -and $_.SerialNumber.Trim() -eq $disk.SerialNumber.Trim() })
   if ($match.Count -eq 1) { $basis = 'unique serial number' }
  }
  if ($match.Count -ne 1) {
   $byId = @($physical | Where-Object { $null -ne $_.DeviceId -and [string]$_.DeviceId -eq [string]$disk.Number })
   if ($byId.Count -eq 1) { $match = $byId; $basis = 'physical disk DeviceId' }
  }
  $media = 'Unknown'
  $health = 'Unknown'
  $opStatus = [string]$disk.OperationalStatus
  $reliability = $null
  if ($match.Count -eq 1) {
   if ($match[0].MediaType) { $media = [string]$match[0].MediaType }
   if ($match[0].HealthStatus) { $health = [string]$match[0].HealthStatus }
   if ($match[0].OperationalStatus) { $opStatus = [string]$match[0].OperationalStatus }
   try {
    $rel = Get-StorageReliabilityCounter -PhysicalDisk $match[0] -ErrorAction Stop
    if ($rel) {
     $reliability = @{
      temperature_c = $rel.Temperature
      wear_percent = $rel.Wear
      power_on_hours = $rel.PowerOnHours
      read_errors_total = $rel.ReadErrorsTotal
      write_errors_total = $rel.WriteErrorsTotal
     }
    }
   } catch {}
  }
  if ($health -eq 'Unknown' -and $disk.HealthStatus) { $health = [string]$disk.HealthStatus }
  $partitions = @()
  try { $partitions = @(Get-Partition -DiskNumber $disk.Number | Where-Object DriveLetter | Select-Object DriveLetter,Size) }
  catch { $errors += $_.Exception.Message }
  $entry = @{ disk_number = $disk.Number; model = $disk.FriendlyName; bus_type = [string]$disk.BusType;
              size_gb = [math]::Round($disk.Size / 1GB, 1); media_type = $media;
              health_status = $health; operational_status = $opStatus;
              mapping_evidence = $basis; drive_letters = @($partitions | ForEach-Object { "$($_.DriveLetter):" }) }
  if ($null -ne $reliability) { $entry['smart_reliability'] = $reliability }
  $disks += $entry
 }
} catch { $errors += $_.Exception.Message }
@{ disks = $disks; physical_disks = @($physical | Select-Object -First 32 FriendlyName,MediaType,BusType,HealthStatus,OperationalStatus,Size);
   errors = $errors; notice = 'Drive letters map through partitions. Media type and health map by unique serial number or DeviceId. SMART reliability counters require Windows Storage support.' } | ConvertTo-Json -Depth 6 -Compress
'''

def inspect_storage(check):
    check()
    warm_out = PS_DAEMON.run_script(STORAGE_SCRIPT, check=check, timeout=20.0)
    if warm_out:
        try:
            result = json.loads(warm_out.lstrip('\ufeff'))
            result['captured_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
            return result
        except (ValueError, TypeError):
            pass
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen([str(executable), '-NoProfile', '-NonInteractive', '-Command', STORAGE_SCRIPT],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                               creationflags=creationflags)
    deadline = time.monotonic() + 25
    try:
        while True:
            check()
            if time.monotonic() > deadline:
                raise TimeoutError('Storage inspection exceeded 25 seconds')
            try:
                output, error = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise RuntimeError(error.strip()[:500])
        result = json.loads(output.lstrip('\ufeff'))
        result['captured_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        return result
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def query_nvml_gpus():
    """Query NVIDIA GPUs directly in-process via nvml.dll ctypes without spawning a subprocess."""
    if os.name != 'nt':
        return []
    candidates = [
        Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'nvml.dll',
        Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'NVIDIA Corporation' / 'NVSMI' / 'nvml.dll',
    ]
    dll_path = next((p for p in candidates if p.exists()), None)
    if dll_path is None:
        return []
    try:
        nvml = ctypes.WinDLL(str(dll_path))
    except Exception:
        return []

    class _NvmlMemory(ctypes.Structure):
        _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]

    class _NvmlUtilization(ctypes.Structure):
        _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

    signatures = {
        'nvmlInit_v2': [], 'nvmlShutdown': [],
        'nvmlDeviceGetCount_v2': [ctypes.POINTER(ctypes.c_uint)],
        'nvmlSystemGetDriverVersion': [ctypes.POINTER(ctypes.c_char), ctypes.c_uint],
        'nvmlDeviceGetHandleByIndex_v2': [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)],
        'nvmlDeviceGetName': [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char), ctypes.c_uint],
        'nvmlDeviceGetMemoryInfo': [ctypes.c_void_p, ctypes.POINTER(_NvmlMemory)],
        'nvmlDeviceGetUtilizationRates': [ctypes.c_void_p, ctypes.POINTER(_NvmlUtilization)],
        'nvmlDeviceGetTemperature': [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)],
        'nvmlDeviceGetPowerUsage': [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
    }
    try:
        for name, argtypes in signatures.items():
            function = getattr(nvml, name)
            function.argtypes = argtypes
            function.restype = ctypes.c_int
    except AttributeError:
        return []

    initialized = False
    try:
        if nvml.nvmlInit_v2() != 0:
            return []
        initialized = True
        count = ctypes.c_uint(0)
        if nvml.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0:
            return []
        drv_buf = ctypes.create_string_buffer(80)
        driver_version = None
        if nvml.nvmlSystemGetDriverVersion(drv_buf, 80) == 0:
            driver_version = drv_buf.value.decode('utf-8', errors='replace')
        gpus = []
        for idx in range(min(count.value, 32)):
            handle = ctypes.c_void_p()
            if nvml.nvmlDeviceGetHandleByIndex_v2(idx, ctypes.byref(handle)) != 0:
                continue
            name_buf = ctypes.create_string_buffer(96)
            name = "NVIDIA GPU"
            if nvml.nvmlDeviceGetName(handle, name_buf, 96) == 0:
                name = name_buf.value.decode('utf-8', errors='replace')
            mem = _NvmlMemory()
            total_mb = used_mb = free_mb = None
            if nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem)) == 0:
                total_mb = int(mem.total // (1024 * 1024))
                used_mb = int(mem.used // (1024 * 1024))
                free_mb = int(mem.free // (1024 * 1024))
            util = _NvmlUtilization()
            gpu_util = None
            if nvml.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util)) == 0:
                gpu_util = int(util.gpu)
            temp = ctypes.c_uint(0)
            temp_c = None
            if nvml.nvmlDeviceGetTemperature(handle, 0, ctypes.byref(temp)) == 0:
                temp_c = int(temp.value)
            power_mw = ctypes.c_uint(0)
            power_w = None
            if nvml.nvmlDeviceGetPowerUsage(handle, ctypes.byref(power_mw)) == 0:
                power_w = round(power_mw.value / 1000.0, 1)
            gpus.append({
                "index": idx,
                "name": name,
                "vram_total_mb": total_mb,
                "vram_total_gb": round(total_mb / 1024, 2) if total_mb else None,
                "vram_used_mb": used_mb,
                "vram_free_mb": free_mb,
                "utilization_gpu_percent": gpu_util,
                "temperature_c": temp_c,
                "power_watts": power_w,
                "driver_version": driver_version,
                "telemetry_source": "nvml.dll (direct ctypes)",
            })
        return gpus
    except Exception:
        return []
    finally:
        if initialized:
            try:
                nvml.nvmlShutdown()
            except Exception:
                pass
