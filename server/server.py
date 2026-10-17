#!/usr/bin/env python3
"""
Power Monitor Central Server & Telemetry Aggregator
Manages B650 and B450 telemetry, SQLite persistence, energy/cost accounting,
job pricing calculations, and Coopeguanacaste tariff isolation.
"""

import datetime
import json
import os
import sqlite3
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8055"))
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
DB_PATH = os.path.join(DATA_DIR, "power_monitor.db")
STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")

B650_URL = os.environ.get("B650_URL", "http://127.0.0.1:9120")
B450_URL = os.environ.get("B450_URL", "http://127.0.0.1:9120")

os.makedirs(DATA_DIR, exist_ok=True)

# Global lock for DB operations
db_lock = threading.Lock()

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        
        # Settings table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # Energy hourly / periodic rollups
        cur.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_rollups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER,
                node_id TEXT,
                wall_watts REAL,
                cpu_watts REAL,
                gpu_watts REAL,
                kwh_delta REAL,
                cost_colones REAL
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rollups_time ON telemetry_rollups(timestamp)")
        
        # Coopeguanacaste daily comparison logs
        cur.execute("""
            CREATE TABLE IF NOT EXISTS coope_logs (
                date TEXT PRIMARY KEY,
                total_coope_kwh REAL,
                homelab_kwh REAL,
                household_kwh REAL,
                homelab_pct REAL,
                total_cost_colones REAL,
                homelab_cost_colones REAL,
                notes TEXT
            )
        """)
        
        # Saved jobs / inference tasks
        cur.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                node_id TEXT,
                created_at TEXT,
                avg_watts REAL,
                duration_minutes REAL,
                kwh REAL,
                electric_cost REAL,
                suggested_client_price REAL,
                margin_pct REAL
            )
        """)
        
        # Seed default settings
        default_settings = {
            "marginal_rate": "105.25",
            "tier1_base_rate": "63.18",
            "tier2_base_rate": "89.07",
            "iva_pct": "13.0",
            "bomberos_pct": "1.75",
            "public_lighting_rate": "3.04",
            "currency": "CRC"
        }
        for k, v in default_settings.items():
            cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            
        conn.commit()
        conn.close()

# Live In-Memory Cache
live_state = {
    "b650": {"online": False, "wall_watts": 0.0, "cpu_watts": 0.0, "gpu_watts": 0.0, "cpu_temp": 0.0, "gpu_temp": 0.0, "last_seen": 0},
    "b450": {"online": False, "wall_watts": 0.0, "cpu_watts": 0.0, "gpu_watts": 0.0, "cpu_temp": 0.0, "last_seen": 0},
    "recent_chart": [],
    "today_kwh": {"b650": 0.0, "b450": 0.0, "total": 0.0},
    "today_cost": 0.0,
    "last_poll": 0
}

def fetch_node(url):
    try:
        req = urllib.request.Request(f"{url}/api/telemetry", headers={"User-Agent": "PowerMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=3) as res:
            if res.status == 200:
                return json.loads(res.read().decode('utf-8'))
    except Exception:
        pass
    return None

def get_setting(key, default="0"):
    with db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        conn.close()
        return row["value"] if row else default

def telemetry_aggregator_worker():
    init_db()
    last_rollup_time = time.time()
    prev_kwh = {"b650": None, "b450": None}
    
    while True:
        try:
            now = time.time()
            today_str = datetime.date.today().isoformat()
            marginal_rate = float(get_setting("marginal_rate", "105.25"))
            
            d_b650 = fetch_node(B650_URL)
            d_b450 = fetch_node(B450_URL)
            
            if d_b650:
                live_state["b650"] = {
                    "online": True,
                    "node_name": d_b650.get("node_name", "B650-AERO-G"),
                    "wall_watts": d_b650.get("wall_watts", 0.0),
                    "cpu_watts": d_b650.get("cpu_watts", 0.0),
                    "gpu_watts": d_b650.get("gpu_watts", 0.0),
                    "gpu_discrete_watts": d_b650.get("gpu_discrete_watts", 0.0),
                    "gpu_integrated_watts": d_b650.get("gpu_integrated_watts", 0.0),
                    "base_watts": d_b650.get("base_watts", 25.0),
                    "cpu_temp": d_b650.get("cpu_temp_c", 0.0),
                    "gpu_temp": d_b650.get("gpu_temp_c", 0.0),
                    "cpu_usage_pct": d_b650.get("cpu_usage_pct", 0.0),
                    "session_kwh": d_b650.get("session_kwh", 0.0),
                    "last_seen": now
                }
            else:
                live_state["b650"]["online"] = False
                
            if d_b450:
                live_state["b450"] = {
                    "online": True,
                    "node_name": d_b450.get("node_name", "B450M-DS3H"),
                    "wall_watts": d_b450.get("wall_watts", 0.0),
                    "cpu_watts": d_b450.get("cpu_watts", 0.0),
                    "gpu_watts": d_b450.get("gpu_watts", 0.0),
                    "base_watts": d_b450.get("base_watts", 18.0),
                    "cpu_temp": d_b450.get("cpu_temp_c", 0.0),
                    "cpu_usage_pct": d_b450.get("cpu_usage_pct", 0.0),
                    "session_kwh": d_b450.get("session_kwh", 0.0),
                    "last_seen": now
                }
            else:
                live_state["b450"]["online"] = False
                
            # Append to running chart (max 60 points = 5 min at 5s interval)
            chart_entry = {
                "t": int(now),
                "b650_w": live_state["b650"]["wall_watts"],
                "b450_w": live_state["b450"]["wall_watts"],
                "total_w": round(live_state["b650"]["wall_watts"] + live_state["b450"]["wall_watts"], 1)
            }
            live_state["recent_chart"].append(chart_entry)
            if len(live_state["recent_chart"]) > 60:
                live_state["recent_chart"].pop(0)
                
            # Rollup every 60 seconds into SQLite
            if now - last_rollup_time >= 60.0:
                dt_hours = (now - last_rollup_time) / 3600.0
                with db_lock:
                    conn = get_db()
                    cur = conn.cursor()
                    
                    # B650 record
                    if live_state["b650"]["online"]:
                        w650 = live_state["b650"]["wall_watts"]
                        kwh650 = (w650 / 1000.0) * dt_hours
                        cost650 = kwh650 * marginal_rate
                        cur.execute("""
                            INSERT INTO telemetry_rollups (timestamp, node_id, wall_watts, cpu_watts, gpu_watts, kwh_delta, cost_colones)
                            VALUES (?, 'b650', ?, ?, ?, ?, ?)
                        """, (int(now), w650, live_state["b650"]["cpu_watts"], live_state["b650"]["gpu_watts"], kwh650, cost650))
                        
                    # B450 record
                    if live_state["b450"]["online"]:
                        w450 = live_state["b450"]["wall_watts"]
                        kwh450 = (w450 / 1000.0) * dt_hours
                        cost450 = kwh450 * marginal_rate
                        cur.execute("""
                            INSERT INTO telemetry_rollups (timestamp, node_id, wall_watts, cpu_watts, gpu_watts, kwh_delta, cost_colones)
                            VALUES (?, 'b450', ?, ?, ?, ?, ?)
                        """, (int(now), w450, live_state["b450"]["cpu_watts"], live_state["b450"]["gpu_watts"], kwh450, cost450))
                        
                    # Calculate Today's Totals from midnight
                    midnight_ts = int(time.mktime(datetime.date.today().timetuple()))
                    cur.execute("""
                        SELECT node_id, SUM(kwh_delta) as sum_kwh, SUM(cost_colones) as sum_cost
                        FROM telemetry_rollups
                        WHERE timestamp >= ?
                        GROUP BY node_id
                    """, (midnight_ts,))
                    
                    k_650 = 0.0
                    k_450 = 0.0
                    total_c = 0.0
                    for r in cur.fetchall():
                        if r["node_id"] == 'b650':
                            k_650 = r["sum_kwh"] or 0.0
                        elif r["node_id"] == 'b450':
                            k_450 = r["sum_kwh"] or 0.0
                        total_c += (r["sum_cost"] or 0.0)
                        
                    live_state["today_kwh"] = {
                        "b650": round(k_650, 3),
                        "b450": round(k_450, 3),
                        "total": round(k_650 + k_450, 3)
                    }
                    live_state["today_cost"] = round(total_c, 2)
                    
                    conn.commit()
                    conn.close()
                    
                last_rollup_time = now
                
            live_state["last_poll"] = now
        except Exception as e:
            print(f"[AggregatorError] {e}")
            
        time.sleep(3.0)

class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/status":
            marginal_rate = float(get_setting("marginal_rate", "105.25"))
            total_w = live_state["b650"]["wall_watts"] + live_state["b450"]["wall_watts"]
            cost_per_hour = (total_w / 1000.0) * marginal_rate
            cost_per_day_proj = cost_per_hour * 24.0
            
            resp = {
                "status": "ok",
                "timestamp": time.time(),
                "nodes": {
                    "b650": live_state["b650"],
                    "b450": live_state["b450"]
                },
                "total_wall_watts": round(total_w, 1),
                "cost_per_hour_colones": round(cost_per_hour, 2),
                "cost_per_day_proj_colones": round(cost_per_day_proj, 2),
                "today_kwh": live_state["today_kwh"],
                "today_cost_colones": live_state["today_cost"],
                "marginal_rate": marginal_rate,
                "recent_chart": live_state["recent_chart"]
            }
            self._send_json(resp)
            return
            
        elif self.path.startswith("/api/history"):
            # Return hourly data for the last 24h
            now_ts = int(time.time())
            start_ts = now_ts - 86400
            with db_lock:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""
                    SELECT (timestamp / 300) * 300 as t_bucket,
                           node_id,
                           AVG(wall_watts) as avg_watts,
                           SUM(kwh_delta) as sum_kwh,
                           SUM(cost_colones) as sum_cost
                    FROM telemetry_rollups
                    WHERE timestamp >= ?
                    GROUP BY t_bucket, node_id
                    ORDER BY t_bucket ASC
                """, (start_ts,))
                rows = [dict(r) for r in cur.fetchall()]
                conn.close()
            self._send_json({"history": rows})
            return
            
        elif self.path == "/api/coope":
            with db_lock:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT * FROM coope_logs ORDER BY date DESC LIMIT 30")
                rows = [dict(r) for r in cur.fetchall()]
                conn.close()
            self._send_json({"logs": rows})
            return
            
        elif self.path == "/api/jobs":
            with db_lock:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 50")
                rows = [dict(r) for r in cur.fetchall()]
                conn.close()
            self._send_json({"jobs": rows})
            return
            
        elif self.path == "/api/settings":
            with db_lock:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT key, value FROM settings")
                s = {r["key"]: r["value"] for r in cur.fetchall()}
                conn.close()
            self._send_json(s)
            return
            
        # Static file delivery
        req_file = self.path.split("?")[0].lstrip("/")
        if not req_file or req_file == "index.html":
            target_path = os.path.join(STATIC_DIR, "index.html")
            mime = "text/html; charset=utf-8"
        else:
            target_path = os.path.join(STATIC_DIR, req_file)
            if req_file.endswith(".css"):
                mime = "text/css"
            elif req_file.endswith(".js"):
                mime = "application/javascript"
            elif req_file.endswith(".svg"):
                mime = "image/svg+xml"
            else:
                mime = "text/plain"
                
        if os.path.exists(target_path) and os.path.isfile(target_path):
            try:
                with open(target_path, 'rb') as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except Exception:
                pass
                
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"404 Not Found")

    def do_POST(self):
        content_len = int(self.headers.get('Content-Length', 0))
        body_bytes = self.rfile.read(content_len)
        try:
            data = json.loads(body_bytes.decode('utf-8')) if body_bytes else {}
        except Exception:
            data = {}
            
        if self.path == "/api/coope":
            # Register daily reading from Coopeguanacaste app
            # Body: {"date": "YYYY-MM-DD", "total_kwh": 24.5, "notes": "..."}
            dt_str = data.get("date", datetime.date.today().isoformat())
            total_kwh = float(data.get("total_kwh", 0.0))
            notes = data.get("notes", "")
            
            marginal_rate = float(get_setting("marginal_rate", "105.25"))
            
            # Calculate homelab energy on that date
            try:
                date_obj = datetime.date.fromisoformat(dt_str)
                start_ts = int(time.mktime(date_obj.timetuple()))
                end_ts = start_ts + 86400
            except Exception:
                start_ts = int(time.time()) - 86400
                end_ts = int(time.time())
                
            with db_lock:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""
                    SELECT SUM(kwh_delta) as lab_kwh
                    FROM telemetry_rollups
                    WHERE timestamp >= ? AND timestamp < ?
                """, (start_ts, end_ts))
                row = cur.fetchone()
                homelab_kwh = round(row["lab_kwh"] or live_state["today_kwh"]["total"], 3)
                
                # If DB has very few records yet, allow minimum
                household_kwh = max(0.0, round(total_kwh - homelab_kwh, 3))
                pct = round((homelab_kwh / total_kwh * 100.0), 1) if total_kwh > 0 else 0.0
                
                tot_cost = round(total_kwh * marginal_rate, 2)
                lab_cost = round(homelab_kwh * marginal_rate, 2)
                
                cur.execute("""
                    INSERT OR REPLACE INTO coope_logs
                    (date, total_coope_kwh, homelab_kwh, household_kwh, homelab_pct, total_cost_colones, homelab_cost_colones, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (dt_str, total_kwh, homelab_kwh, household_kwh, pct, tot_cost, lab_cost, notes))
                conn.commit()
                conn.close()
                
            self._send_json({
                "success": True,
                "date": dt_str,
                "total_coope_kwh": total_kwh,
                "homelab_kwh": homelab_kwh,
                "household_kwh": household_kwh,
                "homelab_pct": pct,
                "homelab_cost_colones": lab_cost
            })
            return
            
        elif self.path == "/api/jobs":
            # Save or calculate a job
            name = data.get("name", "Inference Task")
            node_id = data.get("node_id", "b650")
            watts = float(data.get("watts", 320.0))
            duration_min = float(data.get("duration_minutes", 60.0))
            margin_pct = float(data.get("margin_pct", 50.0))
            
            marginal_rate = float(get_setting("marginal_rate", "105.25"))
            
            kwh = round((watts * (duration_min / 60.0)) / 1000.0, 4)
            electric_cost = round(kwh * marginal_rate, 2)
            suggested_price = round(electric_cost * (1.0 + margin_pct / 100.0), 2)
            
            save = data.get("save", False)
            if save:
                with db_lock:
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT INTO jobs (name, node_id, created_at, avg_watts, duration_minutes, kwh, electric_cost, suggested_client_price, margin_pct)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (name, node_id, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), watts, duration_min, kwh, electric_cost, suggested_price, margin_pct))
                    conn.commit()
                    conn.close()
                    
            self._send_json({
                "name": name,
                "node_id": node_id,
                "avg_watts": watts,
                "duration_minutes": duration_min,
                "kwh": kwh,
                "electric_cost_colones": electric_cost,
                "suggested_client_price_colones": suggested_price,
                "marginal_rate": marginal_rate
            })
            return
            
        elif self.path == "/api/settings":
            with db_lock:
                conn = get_db()
                cur = conn.cursor()
                for k, v in data.items():
                    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (str(k), str(v)))
                conn.commit()
                conn.close()
            self._send_json({"success": True})
            return
            
        self.send_response(404)
        self.end_headers()

    def _send_json(self, data):
        body = json.dumps(data).encode('utf-8')
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass

def run():
    t = threading.Thread(target=telemetry_aggregator_worker, daemon=True)
    t.start()
    server = ThreadingHTTPServer(('0.0.0.0', PORT), DashboardHTTPHandler)
    print(f"[PowerServer] Server listening on port {PORT}")
    server.serve_forever()

if __name__ == '__main__':
    run()
