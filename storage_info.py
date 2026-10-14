"""Fixed read-only Windows storage and SMART health query; no model-provided commands."""
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

def inspect(check):
    check()
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen([str(executable), '-NoProfile', '-NonInteractive', '-Command', SCRIPT],
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
