"""Host telemetry for the dashboard and the system_status tool."""

from __future__ import annotations

import os
import platform
import time

import psutil

_BOOT = psutil.boot_time()
_last_net = (time.time(), psutil.net_io_counters())


def snapshot() -> dict:
    global _last_net
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    now = time.time()
    net = psutil.net_io_counters()
    prev_t, prev = _last_net
    dt = max(now - prev_t, 1e-6)
    _last_net = (now, net)
    try:
        load = os.getloadavg()
    except OSError:
        load = (0.0, 0.0, 0.0)
    return {
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "load": [round(x, 2) for x in load],
        "mem_percent": vm.percent,
        "mem_used_gb": round(vm.used / 1e9, 2),
        "mem_total_gb": round(vm.total / 1e9, 2),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / 1e9, 1),
        "disk_total_gb": round(disk.total / 1e9, 1),
        "net_rx_kbps": round((net.bytes_recv - prev.bytes_recv) / dt / 1024, 1),
        "net_tx_kbps": round((net.bytes_sent - prev.bytes_sent) / dt / 1024, 1),
        "uptime_hours": round((now - _BOOT) / 3600, 1),
        "processes": len(psutil.pids()),
    }
