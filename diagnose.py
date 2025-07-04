#!/usr/bin/env python3
"""
Android Diagnostic CLI Tool (codename: DroidScout)
--------------------------------------------------
- Detects connected Android device (adb devices)
- Collects:
  - CPU usage (adb shell top -m 10 -n 1)
  - RAM usage (adb shell dumpsys meminfo)
  - Thermal info (adb shell dumpsys thermalservice)
  - Frame rendering stats for a target app (adb shell dumpsys gfxinfo <package>)
  - Running services (adb shell dumpsys activity services)
- Auto-detects and highlights heavy apps (CPU >50%, RAM >300MB)
- Parses data & highlights heavy usage
- Generates report (txt, csv, json)
- Optionally runs repeatedly (--interval)
- Clean, color-coded output (if colorama available)
- Cross-platform (Windows/Linux/macOS)
"""

import subprocess
import argparse
import re
import json
import csv
import os
import sys
import time
import datetime
from collections import defaultdict
from typing import List, Dict, Any, Optional
import itertools

# Optional color output
try:
    from colorama import init
    init(autoreset=True)
    from colorama import Fore, Style
    FORE_RED = Fore.RED
    FORE_YELLOW = Fore.YELLOW
    FORE_GREEN = Fore.GREEN
    FORE_CYAN = Fore.CYAN
    FORE_RESET = Fore.RESET
    STYLE_BRIGHT = Style.BRIGHT
    STYLE_RESET_ALL = Style.RESET_ALL
except ImportError:
    FORE_RED = ''
    FORE_YELLOW = ''
    FORE_GREEN = ''
    FORE_CYAN = ''
    FORE_RESET = ''
    STYLE_BRIGHT = ''
    STYLE_RESET_ALL = ''

# Thresholds for heavy usage (can be made configurable)
HEAVY_CPU = 50.0  # percent
HEAVY_RAM = 300   # MB

# -------------------- Utility Functions --------------------
def run_adb_command(cmd: List[str], timeout: int = 10) -> str:
    """Run an adb command and return its output as a string."""
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ADB error: {' '.join(cmd)}\n{result.stderr.strip()}")
        return result.stdout
    except FileNotFoundError:
        print(f"{FORE_RED}Error: adb not found. Please install Android Platform Tools and add adb to your PATH.{FORE_RESET}")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"{FORE_RED}Error: adb command timed out: {' '.join(cmd)}{FORE_RESET}")
        return ''

# -------------------- Device Detection --------------------
def detect_device() -> Optional[str]:
    """Detects a connected Android device. Returns device ID or None."""
    output = run_adb_command(["adb", "devices"])
    lines = output.strip().splitlines()
    devices = [line.split('\t')[0] for line in lines[1:] if '\tdevice' in line]
    if not devices:
        print(f"{FORE_RED}No Android device detected. Please connect and authorize your device.{FORE_RESET}")
        return None
    if len(devices) > 1:
        print(f"{FORE_YELLOW}Warning: Multiple devices detected. Using the first: {devices[0]}{FORE_RESET}")
    return devices[0]

# -------------------- CPU Stats --------------------
def collect_cpu_stats(device_id: str) -> List[Dict[str, Any]]:
    """Collects CPU usage for top 10 processes."""
    output = run_adb_command(["adb", "-s", device_id, "shell", "top", "-m", "10", "-n", "1"])
    processes = []
    for line in output.splitlines():
        # Example: '  1234 user      20   0  123M  45M  20M S  55.0  1.2 com.example.app'
        m = re.match(r"\s*(\d+)\s+\S+\s+\d+\s+\d+\s+\S+\s+\S+\s+\S+\s+\S+\s+([\d.]+)\s+([\d.]+)\s+(\S+)", line)
        if m:
            pid, cpu, mem, name = m.groups()
            processes.append({
                "pid": int(pid),
                "cpu": float(cpu),
                "mem": float(mem),
                "name": name
            })
    return processes

# -------------------- RAM Stats --------------------
def collect_ram_stats(device_id: str) -> List[Dict[str, Any]]:
    """Collects RAM usage for top apps."""
    output = run_adb_command(["adb", "-s", device_id, "shell", "dumpsys", "meminfo"])
    apps = []
    for line in output.splitlines():
        # Example: '  com.example.app:  350000 kB'
        m = re.match(r"\s*(\S+):\s+([\d,]+) kB", line)
        if m:
            name, kb = m.groups()
            mb = int(kb.replace(',', '')) / 1024
            apps.append({
                "name": name,
                "ram_mb": mb
            })
    # Sort by RAM usage descending
    apps.sort(key=lambda x: x["ram_mb"], reverse=True)
    return apps

# -------------------- Thermal Info --------------------
def collect_thermal_info(device_id: str) -> Dict[str, Any]:
    """Collects thermal info from the device."""
    output = run_adb_command(["adb", "-s", device_id, "shell", "dumpsys", "thermalservice"])
    sensors = {}
    for line in output.splitlines():
        m = re.match(r"\s*Sensor: (.+?)\s+Type: (.+?)\s+Temp: ([\d.]+)", line)
        if m:
            name, typ, temp = m.groups()
            sensors[name] = {"type": typ, "temp": float(temp)}
    return sensors

# -------------------- Frame Rendering Stats --------------------
def collect_gfx_stats(device_id: str, package: str) -> Dict[str, Any]:
    """Collects frame rendering stats for a target app."""
    output = run_adb_command(["adb", "-s", device_id, "shell", "dumpsys", "gfxinfo", package])
    frame_times = []
    in_section = False
    for line in output.splitlines():
        if "Profile data in ms:" in line:
            in_section = True
            continue
        if in_section:
            if not line.strip():
                break
            parts = line.strip().split()
            if len(parts) == 3:
                try:
                    draw, process, execute = map(float, parts)
                    frame_times.append(draw + process + execute)
                except ValueError:
                    continue
    if frame_times:
        avg = sum(frame_times) / len(frame_times)
        jank = sum(1 for t in frame_times if t > 16.67)
        return {"avg_frame_time_ms": avg, "jank_frames": jank, "total_frames": len(frame_times)}
    return {"avg_frame_time_ms": None, "jank_frames": None, "total_frames": 0}

# -------------------- Running Services --------------------
def collect_services(device_id: str) -> List[str]:
    """Collects running services."""
    output = run_adb_command(["adb", "-s", device_id, "shell", "dumpsys", "activity", "services"])
    services = []
    for line in output.splitlines():
        m = re.match(r"\s*ServiceRecord\{.*\s(\S+)/(\S+)\}", line)
        if m:
            pkg, svc = m.groups()
            services.append(f"{pkg}/{svc}")
    return services

# -------------------- Heavy App Detection --------------------
def highlight_heavy_cpu(processes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Returns processes with CPU usage above threshold."""
    return [p for p in processes if p["cpu"] >= HEAVY_CPU]

def highlight_heavy_ram(apps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Returns apps with RAM usage above threshold."""
    return [a for a in apps if a["ram_mb"] >= HEAVY_RAM]

# -------------------- Reporting --------------------
def is_system_process(name: str) -> bool:
    # Heuristic: system processes often lack a dot, or are known system names
    system_names = [
        'system_server', 'surfaceflinger', 'audioserver', 'mediaserver', 'zygote', 'init', 'logd', 'statsd', 'adbd',
        'android.hardware', 'vendor.', 'servicemanager', 'hwservicemanager', 'gatekeeperd', 'keystore2', 'rild', 'netd', 'lmkd', 'cameraserver', 'drmserver', 'gpsd', 'vaultkeeperd', 'watchdogd', 'traced', 'traced_probes', 'tombstoned', 'ueventd', 'vold', 'auditd', 'credstore', 'perfsdkserver', 'main_abox', 'abox_log', 'fabric_crypto', 'incidentd', 'iod', 'cass', 'smdexe', 'speg_helper', 'spqr_service', 'emservice', 'tzdaemon', 'prng_seeder', 'smc_server', 'wlbtd', 'connfwexe', 'ddexe', 'diagexe', 'cbd', 'media.swcodec', 'media.extractor', 'media.metrics', 'android.system.suspend-service', 'android.hardware.memtrack-service.exynos', 'android.hardware.bluetooth@1.0-service', 'android.hardware.drm-service.widevine', 'android.hardware.drm-service.clearkey', 'android.hardware.gatekeeper@1.0-service', 'android.hardware.graphics.allocator@4.0-service', 'android.hardware.graphics.composer@2.2-service', 'android.hardware.power.samsung-service', 'android.hardware.sensors-service.multihal', 'android.hardware.usb@1.3-service.coral', 'android.hardware.vibrator-service', 'android.hardware.wifi-service', 'android.hardware.audio.service', 'android.hardware.security.keymint-service', 'android.hardware.security.fkeymaster-service', 'android.hardware.security.drk@2.0-service', 'android.hardware.security.engmode@1.0-service', 'android.hardware.security.proca@2.0-service', 'vendor.samsung.hardware.', 'samsung.hardware.media.c2@1.2-service', 'samsung.software.media.c2@1.0-service', 'vaultkeeperd', 'perfmond', 'pageboostd', 'multiclientd', 'wlbtd', 'kumiho.decoder', 'gpuservice', 'vndservicemanager', 'prey', 'preyproject', 'android.process.media', 'android.process.acore', 'android.process', 'zygote64', 'zygote'
    ]
    if any(name.startswith(n) for n in system_names):
        return True
    if '.' not in name:
        return True
    return False

def print_summary(cpu, ram, thermal, gfx, services, heavy_cpu, heavy_ram, package):
    print(f"\n{STYLE_BRIGHT}{FORE_CYAN}=== ANDROID DIAGNOSTIC SUMMARY ==={STYLE_RESET_ALL}")
    # Separate system and user processes
    cpu_system = [p for p in cpu if is_system_process(p['name'])]
    cpu_user = [p for p in cpu if not is_system_process(p['name'])]
    ram_system = [a for a in ram if is_system_process(a['name'])]
    ram_user = [a for a in ram if not is_system_process(a['name'])]
    # Show top 5 of each
    print(f"{FORE_GREEN}Top 5 User CPU Processes:{FORE_RESET}")
    for p in cpu_user[:5]:
        color = FORE_RED if p in heavy_cpu else FORE_RESET
        print(f"  {color}{p['name']} (PID {p['pid']}): {p['cpu']}% CPU, {p['mem']}% MEM{FORE_RESET}")
    print(f"{FORE_GREEN}Top 5 System CPU Processes:{FORE_RESET}")
    for p in cpu_system[:5]:
        color = FORE_RED if p in heavy_cpu else FORE_RESET
        print(f"  {color}{p['name']} (PID {p['pid']}): {p['cpu']}% CPU, {p['mem']}% MEM{FORE_RESET}")
    print(f"\n{FORE_GREEN}Top 5 User RAM Apps:{FORE_RESET}")
    for a in ram_user[:5]:
        color = FORE_RED if a in heavy_ram else FORE_RESET
        print(f"  {color}{a['name']} (PID {a['pid']}): {a['ram_mb']:.1f} MB RAM{FORE_RESET}")
    print(f"{FORE_GREEN}Top 5 System RAM Apps:{FORE_RESET}")
    for a in ram_system[:5]:
        color = FORE_RED if a in heavy_ram else FORE_RESET
        print(f"  {color}{a['name']} (PID {a['pid']}): {a['ram_mb']:.1f} MB RAM{FORE_RESET}")
    print(f"\n{FORE_GREEN}Thermal Sensors:{FORE_RESET}")
    for name, value in thermal.items():
        warn = ''
        if (name == 'AP' and value > 50) or (name == 'BAT' and value > 45) or (name == 'SKIN' and value > 40):
            warn = f" {FORE_RED}[HIGH]{FORE_RESET}"
        print(f"  {name}: {value}°C{warn}")
    if package:
        print(f"\n{FORE_GREEN}Frame Rendering Stats for {package}:{FORE_RESET}")
        if gfx["avg_frame_time_ms"] is not None:
            print(f"  Avg Frame Time: {gfx['avg_frame_time_ms']:.2f} ms")
            print(f"  Janky Frames (>16.67ms): {gfx['jank_frames']} / {gfx['total_frames']}")
        else:
            print("  No frame data found.")
    print(f"\n{FORE_GREEN}Running Services (first 10):{FORE_RESET}")
    for s in services[:10]:
        print(f"  {s}")
    print(f"\n{FORE_YELLOW}Heavy CPU Processes (>={HEAVY_CPU}%):{FORE_RESET}")
    for p in heavy_cpu:
        print(f"  {p['name']} (PID {p['pid']}): {p['cpu']}% CPU")
    print(f"{FORE_YELLOW}Heavy RAM Apps (>={HEAVY_RAM}MB):{FORE_RESET}")
    for a in heavy_ram:
        print(f"  {a['name']} (PID {a['pid']}): {a['ram_mb']:.1f} MB RAM")
    # Warnings and suggestions
    print(f"\n{STYLE_BRIGHT}{FORE_CYAN}=== DIAGNOSTIC WARNINGS & SUGGESTIONS ==={STYLE_RESET_ALL}")
    # System process warnings
    for p in cpu_system:
        if p['cpu'] > 50:
            print(f"{FORE_RED}Warning: System process {p['name']} is using high CPU ({p['cpu']}%). This may indicate OS or hardware issues.{FORE_RESET}")
    for a in ram_system:
        if a['ram_mb'] > 300:
            print(f"{FORE_RED}Warning: System process {a['name']} is using high RAM ({a['ram_mb']:.1f} MB).{FORE_RESET}")
    # Thermal warnings
    for name, value in thermal.items():
        if (name == 'AP' and value > 50) or (name == 'BAT' and value > 45) or (name == 'SKIN' and value > 40):
            print(f"{FORE_RED}Warning: {name} temperature is high ({value}°C). Consider letting your device cool down.{FORE_RESET}")
    # User app suggestions
    for p in heavy_cpu:
        if not is_system_process(p['name']):
            print(f"{FORE_YELLOW}Suggestion: App {p['name']} is using a lot of CPU. Consider force-stopping or uninstalling if not needed.{FORE_RESET}")
    for a in heavy_ram:
        if not is_system_process(a['name']):
            print(f"{FORE_YELLOW}Suggestion: App {a['name']} is using a lot of RAM. Consider force-stopping or uninstalling if not needed.{FORE_RESET}")
    print(f"\n{STYLE_BRIGHT}{FORE_CYAN}=== END OF SUMMARY ==={STYLE_RESET_ALL}\n")

def save_report_txt(path, cpu, ram, thermal, gfx, services, heavy_cpu, heavy_ram, package):
    with open(path, 'w', encoding='utf-8') as f:
        f.write("=== ANDROID DIAGNOSTIC REPORT ===\n\n")
        f.write("Top 5 User CPU Processes:\n")
        cpu_user = [p for p in cpu if not is_system_process(p['name'])]
        for p in cpu_user[:5]:
            mark = "*" if p in heavy_cpu else ""
            f.write(f"  {p['name']} (PID {p['pid']}): {p['cpu']}% CPU, {p['mem']}% MEM {mark}\n")
        f.write("Top 5 System CPU Processes:\n")
        cpu_system = [p for p in cpu if is_system_process(p['name'])]
        for p in cpu_system[:5]:
            mark = "*" if p in heavy_cpu else ""
            f.write(f"  {p['name']} (PID {p['pid']}): {p['cpu']}% CPU, {p['mem']}% MEM {mark}\n")
        f.write("\nTop 5 User RAM Apps:\n")
        ram_user = [a for a in ram if not is_system_process(a['name'])]
        for a in ram_user[:5]:
            mark = "*" if a in heavy_ram else ""
            f.write(f"  {a['name']} (PID {a['pid']}): {a['ram_mb']:.1f} MB RAM {mark}\n")
        f.write("Top 5 System RAM Apps:\n")
        ram_system = [a for a in ram if is_system_process(a['name'])]
        for a in ram_system[:5]:
            mark = "*" if a in heavy_ram else ""
            f.write(f"  {a['name']} (PID {a['pid']}): {a['ram_mb']:.1f} MB RAM {mark}\n")
        f.write("\nThermal Sensors:\n")
        if thermal:
            for name, value in thermal.items():
                warn = ''
                if (name == 'AP' and value > 50) or (name == 'BAT' and value > 45) or (name == 'SKIN' and value > 40):
                    warn = ' [HIGH]'
                f.write(f"  {name}: {value}°C{warn}\n")
        else:
            f.write("  (No thermal data)\n")
        if package:
            f.write(f"\nFrame Rendering Stats for {package}:\n")
            if gfx["avg_frame_time_ms"] is not None:
                f.write(f"  Avg Frame Time: {gfx['avg_frame_time_ms']:.2f} ms\n")
                f.write(f"  Janky Frames (>16.67ms): {gfx['jank_frames']} / {gfx['total_frames']}\n")
            else:
                f.write("  No frame data found.\n")
        f.write("\nRunning Services (first 10):\n")
        for s in services[:10]:
            f.write(f"  {s}\n")
        f.write(f"\nHeavy CPU Processes (>={HEAVY_CPU}%):\n")
        for p in heavy_cpu:
            f.write(f"  {p['name']} (PID {p['pid']}): {p['cpu']}% CPU\n")
        f.write(f"Heavy RAM Apps (>={HEAVY_RAM}MB):\n")
        for a in heavy_ram:
            f.write(f"  {a['name']} (PID {a['pid']}): {a['ram_mb']:.1f} MB RAM\n")
        # Warnings and suggestions
        f.write("\n=== DIAGNOSTIC WARNINGS & SUGGESTIONS ===\n")
        for```` p in cpu_system:
            if p['cpu'] > 50:
                f.write(f"Warning: System process {p['name']} is using high CPU ({p['cpu']}%). This may indicate OS or hardware issues.\n")
        for a in ram_system:
            if a['ram_mb'] > 300:
                f.write(f"Warning: System process {a['name']} is using high RAM ({a['ram_mb']:.1f} MB).\n")
        for name, value in thermal.items():
            if (name == 'AP' and value > 50) or (name == 'BAT' and value > 45) or (name == 'SKIN' and value > 40):
                f.write(f"Warning: {name} temperature is high ({value}°C). Consider letting your device cool down.\n")
        for p in heavy_cpu:
            if not is_system_process(p['name']):
                f.write(f"Suggestion: App {p['name']} is using a lot of CPU. Consider force-stopping or uninstalling if not needed.\n")
        for a in heavy_ram:
            if not is_system_process(a['name']):
                f.write(f"Suggestion: App {a['name']} is using a lot of RAM. Consider force-stopping or uninstalling if not needed.\n")
        f.write("\n=== END OF REPORT ===\n")

def save_report_csv(path, cpu, ram, heavy_cpu, heavy_ram):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Type", "Name", "PID", "CPU %", "MEM %", "RAM MB", "Heavy"])
        for p in cpu:
            writer.writerow(["CPU", p["name"], p["pid"], p["cpu"], p["mem"], '', p in heavy_cpu])
        for a in ram:
            writer.writerow(["RAM", a["name"], '', '', '', a["ram_mb"], a in heavy_ram])

def save_report_json(path, cpu, ram, thermal, gfx, services, heavy_cpu, heavy_ram, package):
    data = {
        "cpu": cpu,
        "ram": ram,
        "thermal": thermal,
        "gfx": gfx,
        "services": services,
        "heavy_cpu": heavy_cpu,
        "heavy_ram": heavy_ram,
        "package": package
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

# -------------------- Main Diagnostic Routine --------------------
def run_diagnostics(args):
    device_id = detect_device()
    if not device_id:
        sys.exit(1)
    # Data collection
    cpu_raw = run_adb_command(["adb", "-s", device_id, "shell", "top", "-m", "10", "-n", "1"])
    ram_raw = run_adb_command(["adb", "-s", device_id, "shell", "dumpsys", "meminfo"], timeout=30)
    thermal_raw = run_adb_command(["adb", "-s", device_id, "shell", "dumpsys", "thermalservice"])
    gfx_raw = run_adb_command(["adb", "-s", device_id, "shell", "dumpsys", "gfxinfo", args.target]) if args.target else ''
    services_raw = run_adb_command(["adb", "-s", device_id, "shell", "dumpsys", "activity", "services"])

    # Create per-run subdirectory
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(args.outdir, f"run_{timestamp}")
    os.makedirs(run_dir, exist_ok=True)

    # Save raw outputs for debugging
    with open(os.path.join(run_dir, "cpu_raw.txt"), 'w', encoding='utf-8') as f:
        f.write(cpu_raw)
    with open(os.path.join(run_dir, "ram_raw.txt"), 'w', encoding='utf-8') as f:
        f.write(ram_raw)
    with open(os.path.join(run_dir, "thermal_raw.txt"), 'w', encoding='utf-8') as f:
        f.write(thermal_raw)
    if args.target:
        with open(os.path.join(run_dir, f"gfx_raw_{args.target}.txt"), 'w', encoding='utf-8') as f:
            f.write(gfx_raw)
    with open(os.path.join(run_dir, "services_raw.txt"), 'w', encoding='utf-8') as f:
        f.write(services_raw)

    # Parse data (handle possible empty/timeout results)
    cpu = collect_cpu_stats_from_raw(cpu_raw) if cpu_raw else []
    ram = collect_ram_stats_from_raw(ram_raw) if ram_raw else []
    thermal = collect_thermal_info_from_raw(thermal_raw) if thermal_raw else {}
    gfx = collect_gfx_stats_from_raw(gfx_raw) if args.target and gfx_raw else {"avg_frame_time_ms": None, "jank_frames": None, "total_frames": 0}
    services = collect_services_from_raw(services_raw) if services_raw else []
    # Heavy usage detection
    heavy_cpu = highlight_heavy_cpu(cpu)
    heavy_ram = highlight_heavy_ram(ram)
    # Print summary
    print_summary(cpu, ram, thermal, gfx, services, heavy_cpu, heavy_ram, args.target)
    # Save reports
    save_report_txt(os.path.join(run_dir, "summary.txt"), cpu, ram, thermal, gfx, services, heavy_cpu, heavy_ram, args.target)
    save_report_csv(os.path.join(run_dir, "report.csv"), cpu, ram, heavy_cpu, heavy_ram)
    save_report_json(os.path.join(run_dir, "report.json"), cpu, ram, thermal, gfx, services, heavy_cpu, heavy_ram, args.target)
    print(f"{FORE_GREEN}Reports and raw files saved to: {run_dir}{FORE_RESET}")

def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    return ansi_escape.sub('', text)

def collect_cpu_stats_from_raw(raw: str) -> List[Dict[str, Any]]:
    # Remove ANSI codes and split lines
    lines = [strip_ansi(line) for line in raw.splitlines() if line.strip()]
    # Find header
    header_idx = None
    for i, line in enumerate(lines):
        if re.search(r'PID\s+USER', line) and '[%CPU]' in line:
            header_idx = i
            break
    if header_idx is None:
        return []
    processes = []
    for line in lines[header_idx+1:]:
        # Example: 1203 system 18 -2 17G 252M 80M S 109 6.8 615:01.29 system_server
        m = re.match(r"\s*(\d+)\s+(\S+)\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+([\d.]+)\s+([\d.]+)\s+([\d:]+)\s+(\S+)", line)
        if m:
            pid, user, cpu, mem, time, name = m.groups()
            processes.append({
                "pid": int(pid),
                "user": user,
                "cpu": float(cpu),
                "mem": float(mem),
                "time": time,
                "name": name
            })
        else:
            # Try to match truncated process names (e.g., com.google.andr+)
            m2 = re.match(r"\s*(\d+)\s+(\S+)\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+([\d.]+)\s+([\d.]+)\s+([\d:]+)\s+(.+)", line)
            if m2:
                pid, user, cpu, mem, time, name = m2.groups()
                processes.append({
                    "pid": int(pid),
                    "user": user,
                    "cpu": float(cpu),
                    "mem": float(mem),
                    "time": time,
                    "name": name
                })
    return processes

def collect_ram_stats_from_raw(raw: str) -> List[Dict[str, Any]]:
    # Find the 'Total RSS by process:' section
    lines = raw.splitlines()
    start = None
    for i, line in enumerate(lines):
        if 'Total RSS by process:' in line:
            start = i + 1
            break
    if start is None:
        return []
    apps = []
    for line in itertools.islice(lines, start, None):
        m = re.match(r"\s*([\d,]+)K: (.+?) \(pid (\d+)(?: /.+)?\)", line)
        if m:
            kb, name, pid = m.groups()
            mb = int(kb.replace(',', '')) / 1024
            apps.append({
                "name": name,
                "pid": int(pid),
                "ram_mb": mb
            })
        elif line.strip() == '':
            break
    apps.sort(key=lambda x: x["ram_mb"], reverse=True)
    return apps

def collect_thermal_info_from_raw(raw: str) -> Dict[str, Any]:
    # Look for lines like: Temperature{mValue=38.1, mType=0, mName=AP, mStatus=0}
    sensors = {}
    for line in raw.splitlines():
        m = re.search(r'Temperature\{mValue=([\d.]+), mType=\d+, mName=([A-Z0-9_]+), mStatus=\d+\}', line)
        if m:
            value, name = m.groups()
            sensors[name] = float(value)
    return sensors

def collect_gfx_stats_from_raw(raw: str) -> Dict[str, Any]:
    frame_times = []
    in_section = False
    for line in raw.splitlines():
        if "Profile data in ms:" in line:
            in_section = True
            continue
        if in_section:
            if not line.strip():
                break
            parts = line.strip().split()
            if len(parts) == 3:
                try:
                    draw, process, execute = map(float, parts)
                    frame_times.append(draw + process + execute)
                except ValueError:
                    continue
    if frame_times:
        avg = sum(frame_times) / len(frame_times)
        jank = sum(1 for t in frame_times if t > 16.67)
        return {"avg_frame_time_ms": avg, "jank_frames": jank, "total_frames": len(frame_times)}
    return {"avg_frame_time_ms": None, "jank_frames": None, "total_frames": 0}

def collect_services_from_raw(raw: str) -> List[str]:
    # Look for lines like: * ServiceRecord{... u0 com.package/.ServiceName}
    services = []
    for line in raw.splitlines():
        m = re.match(r'\s*\* ServiceRecord\{[a-f0-9]+ u\d+ ([\w\.]+)/(\S+)\}', line)
        if m:
            pkg, svc = m.groups()
            services.append(f"{pkg}/{svc}")
    return services

# -------------------- CLI Argument Parsing --------------------
def main():
    parser = argparse.ArgumentParser(description="Android Diagnostic CLI Tool (DroidScout)")
    parser.add_argument('--target', type=str, help='Target app package for frame stats (e.g. com.instagram.android)')
    parser.add_argument('--interval', type=int, default=0, help='Repeat diagnostics every N seconds (0 = run once)')
    parser.add_argument('--outdir', type=str, default='.', help='Directory to save reports')
    args = parser.parse_args()
    if not os.path.isdir(args.outdir):
        os.makedirs(args.outdir)
    if args.interval > 0:
        print(f"{FORE_CYAN}Monitor mode: running every {args.interval} seconds. Press Ctrl+C to stop.{FORE_RESET}")
        try:
            while True:
                run_diagnostics(args)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nExiting monitor mode.")
    else:
        run_diagnostics(args)

if __name__ == "__main__":
    main() 