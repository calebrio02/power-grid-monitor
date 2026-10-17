#!/usr/bin/env python3
"""
Lightweight Hardware Power Telemetry Agent
Measures CPU package power (AMD/Intel RAPL) and AMD GPU power (hwmon),
calculating accurate wall power draw without external dependencies.
"""

import glob
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NODE_ID = os.environ.get("NODE_ID", "b650")
NODE_NAME = os.environ.get("NODE_NAME", "B650-AERO-G (Workstation)")
BASE_WATTS = float(os.environ.get("BASE_WATTS", "25.0"))
PSU_EFFICIENCY = float(os.environ.get("PSU_EFFICIENCY", "0.88"))
PORT = int(os.environ.get("PORT", "9120"))

# Global Telemetry State
state_lock = threading.Lock()
current_telemetry = {
    "node_id": NODE_ID,
    "node_name": NODE_NAME,
    "timestamp": time.time(),
    "cpu_watts": 0.0,
    "gpu_watts": 0.0,
    "gpu_discrete_watts": 0.0,
    "gpu_integrated_watts": 0.0,
    "base_watts": BASE_WATTS,
    "psu_efficiency": PSU_EFFICIENCY,
    "wall_watts": BASE_WATTS / PSU_EFFICIENCY,
    "cpu_temp_c": 0.0,
    "gpu_temp_c": 0.0,
    "cpu_usage_pct": 0.0,
    "uptime_seconds": 0,
    "session_kwh": 0.0,
    "recent_samples": []
}

def read_file_safe(path):
    try:
        with open(path, 'r') as f:
            return f.read().strip()
    except Exception:
        return None

def find_rapl_path():
    candidates = [
        "/sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/energy_uj",
        "/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj",
        "/sys/class/powercap/intel-rapl:0/energy_uj"
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    for p in glob.glob("/sys/**/intel-rapl:0/energy_uj", recursive=True):
        return p
    return None

def find_rapl_max_range(rapl_path):
    if not rapl_path:
        return None
    base_dir = os.path.dirname(rapl_path)
    range_file = os.path.join(base_dir, "max_energy_range_uj")
    val = read_file_safe(range_file)
    if val:
        try:
            return int(val)
        except ValueError:
            pass
    return 2**64 - 1

def get_cpu_temp():
    # Search k10temp or coretemp
    for h in glob.glob("/sys/class/hwmon/hwmon*"):
        name = read_file_safe(os.path.join(h, "name"))
        if name in ["k10temp", "coretemp", "zenpower"]:
            t = read_file_safe(os.path.join(h, "temp1_input"))
            if t:
                try:
                    return round(float(t) / 1000.0, 1)
                except ValueError:
                    pass
    return 0.0

def get_gpu_telemetry():
    discrete_w = 0.0
    integrated_w = 0.0
    max_temp = 0.0
    
    for h in glob.glob("/sys/class/hwmon/hwmon*"):
        name = read_file_safe(os.path.join(h, "name"))
        if name == "amdgpu":
            p_in = read_file_safe(os.path.join(h, "power1_input")) or read_file_safe(os.path.join(h, "power1_average"))
            t_in = read_file_safe(os.path.join(h, "temp1_input")) or read_file_safe(os.path.join(h, "temp2_input"))
            
            w = 0.0
            if p_in:
                try:
                    w = float(p_in) / 1e6
                except ValueError:
                    pass
            
            if t_in:
                try:
                    t = float(t_in) / 1000.0
                    if t > max_temp:
                        max_temp = t
                except ValueError:
                    pass
            
            # Check if discrete (>15W typical cap/input or check device)
            if w > 12.0:
                if w > discrete_w:
                    discrete_w = w
            else:
                integrated_w += w
                
    total_gpu = discrete_w + integrated_w
    return total_gpu, discrete_w, integrated_w, round(max_temp, 1)

def get_cpu_stat():
    content = read_file_safe("/proc/stat")
    if not content:
        return (0, 0)
    for line in content.splitlines():
        if line.startswith("cpu "):
            parts = [int(x) for x in line.split()[1:]]
            idle = parts[3] + parts[4]  # idle + iowait
            total = sum(parts)
            return (idle, total)
    return (0, 0)

def telemetry_collector_thread():
    rapl_path = find_rapl_path()
    rapl_max = find_rapl_max_range(rapl_path)
    
    prev_rapl = None
    if rapl_path:
        val = read_file_safe(rapl_path)
        if val:
            try:
                prev_rapl = int(val)
            except ValueError:
                pass
                
    prev_idle, prev_total = get_cpu_stat()
    last_time = time.time()
    start_time = time.time()
    session_kwh = 0.0
    samples = []
    
    while True:
        time.sleep(1.0)
        now = time.time()
        dt = now - last_time
        if dt <= 0:
            dt = 1.0
        last_time = now
        
        # 1. CPU Power via RAPL
        cpu_watts = 0.0
        if rapl_path:
            curr_rapl_str = read_file_safe(rapl_path)
            if curr_rapl_str:
                try:
                    curr_rapl = int(curr_rapl_str)
                    if prev_rapl is not None:
                        delta_uj = curr_rapl - prev_rapl
                        if delta_uj < 0 and rapl_max:
                            delta_uj += rapl_max
                        if delta_uj >= 0:
                            cpu_watts = (delta_uj / 1e6) / dt
                    prev_rapl = curr_rapl
                except Exception:
                    pass
                    
        # 2. CPU Usage %
        curr_idle, curr_total = get_cpu_stat()
        total_diff = curr_total - prev_total
        idle_diff = curr_idle - prev_idle
        cpu_pct = 0.0
        if total_diff > 0:
            cpu_pct = round((1.0 - (idle_diff / total_diff)) * 100.0, 1)
        prev_idle, prev_total = curr_idle, curr_total
        
        # If RAPL is zero or unavailable, estimate from CPU usage
        if cpu_watts <= 0.0 and cpu_pct > 0:
            # Rough fallback: 15W idle + up to 65W scaled with load
            cpu_watts = 15.0 + (cpu_pct / 100.0) * 55.0
            
        # 3. GPU Power and Temp
        gpu_total, gpu_disc, gpu_integ, gpu_temp = get_gpu_telemetry()
        cpu_temp = get_cpu_temp()
        
        # 4. Total Wall Power Calculation
        internal_watts = cpu_watts + gpu_total + BASE_WATTS
        wall_watts = internal_watts / PSU_EFFICIENCY
        
        # 5. Energy Accumulation (kWh)
        # kWh = (watts * seconds) / (1000 * 3600)
        kwh_step = (wall_watts * dt) / 3600000.0
        session_kwh += kwh_step
        
        sample_entry = {
            "t": int(now),
            "w": round(wall_watts, 1),
            "cpu_w": round(cpu_watts, 1),
            "gpu_w": round(gpu_total, 1)
        }
        samples.append(sample_entry)
        if len(samples) > 60:
            samples.pop(0)
            
        with state_lock:
            current_telemetry["timestamp"] = now
            current_telemetry["cpu_watts"] = round(cpu_watts, 2)
            current_telemetry["gpu_watts"] = round(gpu_total, 2)
            current_telemetry["gpu_discrete_watts"] = round(gpu_disc, 2)
            current_telemetry["gpu_integrated_watts"] = round(gpu_integ, 2)
            current_telemetry["wall_watts"] = round(wall_watts, 1)
            current_telemetry["cpu_temp_c"] = cpu_temp
            current_telemetry["gpu_temp_c"] = gpu_temp
            current_telemetry["cpu_usage_pct"] = cpu_pct
            current_telemetry["uptime_seconds"] = int(now - start_time)
            current_telemetry["session_kwh"] = round(session_kwh, 4)
            current_telemetry["recent_samples"] = list(samples)

class TelemetryHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/telemetry" or self.path == "/api/status":
            with state_lock:
                data = dict(current_telemetry)
            body = json.dumps(data).encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            body = b'{"status": "healthy"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress noisy HTTP request logging
        pass

def run():
    t = threading.Thread(target=telemetry_collector_thread, daemon=True)
    t.start()
    server = ThreadingHTTPServer(('0.0.0.0', PORT), TelemetryHTTPHandler)
    print(f"[PowerAgent] Running on port {PORT} for node: {NODE_ID} ({NODE_NAME})")
    server.serve_forever()

if __name__ == '__main__':
    run()
