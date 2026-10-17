# ⚡ Power Grid Monitor // Industrial Hardware Telemetry & Cost Auditor

An autonomous, hardware-level power telemetry monitor, computing job costing calculator, and utility bill auditor built with an **Industrial Skeuomorphic** user interface.

Designed specifically to monitor multi-node homelab & AI workloads (AMD Ryzen RAPL + AMD Radeon ROCm/hwmon), calculate exact billing costs for AI inference jobs (e.g. Qwen 3.8-27B) using real-world marginal utility tariffs (including VAT and fire department taxes), and isolate server consumption from household electricity bills.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-cyan.svg)
![Architecture](https://img.shields.io/badge/design-Industrial%20Skeuomorphism-orange.svg)

---

## ✨ Features

1. **Zero-Hardware-Dongle Power Measurement (Pure Software)**:
   - **CPU Package Power**: Reads AMD & Intel hardware Running Average Power Limit (RAPL) microjoule counters directly from `/sys/class/powercap/intel-rapl/`.
   - **GPU Power & Temperatures**: Polls AMD discrete (e.g. RX 7900 XT) and integrated GPUs via kernel `hwmon` (`amdgpu`).
   - **Motherboard Base & PSU Efficiency Curve**: Models standby motherboards/storage and corrects for 80+ Bronze/Gold power supply loss curves to calculate true wall draw in Watts.

2. **Industrial Skeuomorphic UI (Dieter Rams / Teenage Engineering Aesthetic)**:
   - Tactile light-mode chassis (`#e0e5ec`) with calibrated 45° top-left lighting and neumorphic double shadows.
   - Corner screws (`.panel-bolted` via radial micro-gradients), vertical ventilation slots, and pulsing LED status lenses.
   - High-contrast **Safety Orange (`#ff4757`)** for primary keys, brand badges, and critical readouts.
   - Embedded **CRT screen bezel** with phosphor scanlines and live oscillograph strip chart.
   - Physical mechanical keys that depress 2px on click with shadow inversion, and recessed *data slot* inputs.

3. **AI Inference & Workload Cost Calculator**:
   - Calculate electricity expenses using marginal tariffs rather than base averages (crucial when exceeding residential utility thresholds).
   - One-click presets: *Qwen 3.8-27B Heavy Inference (320W)*, *Fine-tuning / Batch (350W)*, *Daily Idle (95W)*, *24/7 Homelab Server (47W)*.
   - Instant calculation of kWh, marginal power cost, and suggested client price with customizable profit/hardware depreciation margins.
   - Persistent SQLite job history.

4. **Utility Bill Auditor (Homelab vs. Household Isolation)**:
   - Input your total daily kWh reported by your electrical utility app (e.g. Coopeguanacaste, ICE, CNFL).
   - Automatically subtracts measured homelab consumption to calculate net household energy (refrigerators, AC, lighting).
   - Shows dynamic visual split bars and cost distribution in local currency (CRC ₡ or USD).

---

## 🏗️ Architecture

```
               [ Remote Node: B450 Server ]
               (Ryzen 2600 + RAPL + Base)
                           │
                  (HTTP :9120 Telemetry)
                           │
                           ▼
[ Local Node: B650 Rig ] ───────► [ Central Dashboard (:8055) ]
(Ryzen CPU + RX 7900 XT + hwmon)   ├── SQLite DB Persistence
(HTTP :9120 Telemetry)            ├── Telemetry Aggregator Loop
                                  └── Industrial Skeuomorphic Web UI
```

---

## 🚀 Quickstart

### 1. Clone the repository
```bash
git clone https://github.com/calebrio02/power-grid-monitor.git
cd power-grid-monitor
```

### 2. Configure Environment
Copy the example environment file and set your node addresses:
```bash
cp .env.example .env
```

Edit `.env`:
```env
PORT=8055
B650_URL=http://127.0.0.1:9120
B450_URL=http://192.168.1.100:9120 # or Tailscale IP
```

### 3. Deploy with Docker Compose
```bash
docker compose up -d --build
```

Access the dashboard at `http://localhost:8055` (or via your server's LAN/Tailscale IP).

---

## 📡 Deploying Telemetry Agents to Additional Nodes

To monitor another remote Linux machine (e.g. secondary server), run the lightweight agent container on that machine:

```bash
cd agent
docker build -t power-agent:latest .

docker run -d \
  --name power-agent \
  --restart unless-stopped \
  --privileged \
  -v /sys:/sys:ro \
  -p 9120:9120 \
  -e NODE_ID=b450 \
  -e NODE_NAME="Homelab Server" \
  -e BASE_WATTS=18.0 \
  -e PSU_EFFICIENCY=0.85 \
  power-agent:latest
```

Then point `B450_URL` in your `.env` to that node's IP on port `9120`.

---

## ⚙️ Configuration & Tariffs

The system includes Costa Rican electrical utility tariffs by default (Coopeguanacaste T-1):
- **Marginal Rate**: ₡105.25 / kWh *(Tier 2 Energy ₡89.07 + 13% VAT + 1.75% Fire Dept Tax + ₡3.04 Public Lighting)*.
- **Base Tier 1**: ₡63.18 / kWh (first 200 kWh).
- All rates can be adjusted directly from the UI via the **⚙ CONFIG TARIFA** dialog.

---

## 📄 License

MIT License. Free to use, adapt, and extend for your homelab or AI computing setup.
