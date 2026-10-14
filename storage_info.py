"""Fixed read-only Windows storage query; no model-provided commands."""
import json
import os
from pathlib import Path
import subprocess
import time

SCRIPT = r'''
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
  $media = 'Unknown'
  if ($match.Count -eq 1 -and $basis -eq 'unique serial number') { $media = [string]$match[0].MediaType }
  $partitions = @()
  try { $partitions = @(Get-Partition -DiskNumber $disk.Number | Where-Object DriveLetter | Select-Object DriveLetter,Size) }
  catch { $errors += $_.Exception.Message }
  $disks += @{ disk_number = $disk.Number; model = $disk.FriendlyName; bus_type = [string]$disk.BusType;
               size_gb = [math]::Round($disk.Size / 1GB, 1); media_type = $media;
               mapping_evidence = $basis; drive_letters = @($partitions | ForEach-Object { "$($_.DriveLetter):" }) }
 }
} catch { $errors += $_.Exception.Message }
@{ disks = $disks; physical_disks = @($physical | Select-Object -First 32 FriendlyName,MediaType,BusType,Size);
   errors = $errors; notice = 'Drive letters map through partitions. Media type maps only by a unique serial number; Unknown is not HDD. NVMe/SATA bus types alone do not identify HDD versus SSD.' } | ConvertTo-Json -Depth 6 -Compress
'''

def inspect(check):
    check()
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    process = subprocess.Popen([str(executable), '-NoProfile', '-NonInteractive', '-Command', SCRIPT],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                               creationflags=subprocess.CREATE_NO_WINDOW)
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
