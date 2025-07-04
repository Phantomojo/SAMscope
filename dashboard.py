import os
import threading
import time
import json
import re
import itertools
import webbrowser
from flask import Flask, jsonify, request, render_template_string
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
CORS(app)

# Global state for session monitoring
data_lock = threading.Lock()
session_data = []
session_active = False
session_thread = None
session_interval = 5  # seconds

# --- ADB helpers ---
def run_adb(cmd, timeout=10):
    import subprocess
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, text=True)
        if result.returncode != 0:
            return ''
        return result.stdout
    except Exception:
        return ''

def strip_ansi(text: str) -> str:
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    return ansi_escape.sub('', text)

def collect_cpu_stats_from_raw(raw: str):
    lines = [strip_ansi(line) for line in raw.splitlines() if line.strip()]
    header_idx = None
    for i, line in enumerate(lines):
        if re.search(r'PID\s+USER', line) and '[%CPU]' in line:
            header_idx = i
            break
    if header_idx is None:
        return []
    processes = []
    for line in lines[header_idx+1:]:
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

def collect_ram_stats_from_raw(raw: str):
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

def collect_thermal_info_from_raw(raw: str):
    sensors = {}
    for line in raw.splitlines():
        m = re.search(r'Temperature\{mValue=([\d.]+), mType=\d+, mName=([A-Z0-9_]+), mStatus=\d+\}', line)
        if m:
            value, name = m.groups()
            sensors[name] = float(value)
    return sensors

def collect_stats():
    # Run ADB commands in parallel for speed
    with ThreadPoolExecutor() as executor:
        future_cpu = executor.submit(run_adb, ["adb", "shell", "top", "-m", "10", "-n", "1"])
        future_ram = executor.submit(run_adb, ["adb", "shell", "dumpsys", "meminfo"], 20)
        future_thermal = executor.submit(run_adb, ["adb", "shell", "dumpsys", "thermalservice"])
        cpu_raw = future_cpu.result()
        ram_raw = future_ram.result()
        thermal_raw = future_thermal.result()
    cpu = collect_cpu_stats_from_raw(cpu_raw) if cpu_raw else []
    ram = collect_ram_stats_from_raw(ram_raw) if ram_raw else []
    thermal = collect_thermal_info_from_raw(thermal_raw) if thermal_raw else {}
    # Memory health indicator
    total_ram = 4096  # Assume 4GB for now; can be detected
    used_ram = sum(a['ram_mb'] for a in ram)
    free_ram = total_ram - used_ram
    mem_health = 'good'
    if free_ram < 500:
        mem_health = 'low'
    elif free_ram < 1000:
        mem_health = 'medium'
    return {
        'cpu': cpu[:5],
        'ram': ram[:5],
        'thermal': thermal,
        'timestamp': time.time(),
        'last_updated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'mem_health': mem_health,
        'free_ram': free_ram,
        'total_ram': total_ram,
        'jank': {},
        'storage': {},
    }

def clear_cache_all():
    output = run_adb(["adb", "shell", "pm", "trim-caches", "1K"])
    if output.strip() == '':
        return {'status': 'ok', 'message': 'Cache cleared for all apps.'}
    else:
        return {'status': 'ok', 'message': f'Cache clear output: {output.strip()}'}

# --- Session Monitoring Thread ---
def session_monitor():
    global session_active, session_data
    while session_active:
        stats = collect_stats()
        with data_lock:
            session_data.append(stats)
        time.sleep(session_interval)

# --- API Endpoints ---
@app.route('/')
def dashboard():
    # Serve a modern dark mode dashboard with cards and charts
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Android Performance Dashboard</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://fonts.googleapis.com/css?family=Inter:400,700&display=swap" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {
                background: #181c24;
                color: #f4f4f4;
                font-family: 'Inter', Arial, sans-serif;
                margin: 0;
                padding: 0;
            }
            h1 {
                color: #fff;
                font-weight: 700;
                margin: 2rem 0 1rem 0;
                text-align: center;
            }
            .dashboard {
                display: flex;
                flex-wrap: wrap;
                justify-content: center;
                gap: 2rem;
                margin: 2rem auto;
                max-width: 1200px;
            }
            .card {
                background: #23283a;
                border-radius: 18px;
                box-shadow: 0 2px 12px #0002;
                padding: 2rem 1.5rem;
                min-width: 260px;
                flex: 1 1 300px;
                max-width: 350px;
                display: flex;
                flex-direction: column;
                align-items: center;
                transition: box-shadow 0.2s, border 0.2s;
            }
            .card.mem-alert {
                border: 2px solid #ff5252;
                box-shadow: 0 0 16px #ff525288;
            }
            .card h2 {
                color: #8ecfff;
                font-size: 1.2rem;
                margin-bottom: 1rem;
            }
            .metric {
                font-size: 2.2rem;
                font-weight: 700;
                margin-bottom: 0.5rem;
            }
            .mem-good { color: #4caf50; }
            .mem-medium { color: #ffc107; }
            .mem-low { color: #ff5252; }
            .warning { color: #ff5252; font-weight: bold; margin-top: 0.5rem; }
            .actions {
                display: flex;
                justify-content: center;
                gap: 1rem;
                margin: 2rem 0 1rem 0;
            }
            button {
                background: #23283a;
                color: #8ecfff;
                border: 2px solid #8ecfff;
                border-radius: 8px;
                padding: 0.7em 1.5em;
                font-size: 1rem;
                font-family: inherit;
                cursor: pointer;
                transition: background 0.2s, color 0.2s;
            }
            button:hover {
                background: #8ecfff;
                color: #23283a;
            }
            .kill-btn {
                background: #ff5252;
                color: #fff;
                border: none;
                margin-left: 0.5em;
                padding: 0.3em 0.8em;
                border-radius: 6px;
                font-size: 0.95em;
                cursor: pointer;
                transition: background 0.2s;
            }
            .kill-btn:hover {
                background: #ff8888;
            }
            .timestamp {
                text-align: center;
                color: #aaa;
                margin-bottom: 1.5rem;
            }
            @media (max-width: 900px) {
                .dashboard { flex-direction: column; align-items: center; }
            }
        </style>
    </head>
    <body>
        <h1>Android Performance Dashboard</h1>
        <div class="actions">
            <button onclick="startSession()">Start Monitoring</button>
            <button onclick="stopSession()">Stop & Export</button>
            <button onclick="clearCache()">Clear Cache</button>
        </div>
        <div class="timestamp" id="lastUpdated">Last updated: --</div>
        <div class="dashboard">
            <div class="card" id="memCard">
                <h2>Memory Health</h2>
                <div class="metric" id="memHealth">--</div>
                <div id="memDetail">--</div>
                <div class="warning" id="memWarning" style="display:none;">Low memory! Consider closing heavy apps.</div>
            </div>
            <div class="card">
                <h2>Top CPU Apps</h2>
                <ul id="cpuList"></ul>
            </div>
            <div class="card">
                <h2>Top RAM Apps</h2>
                <ul id="ramList"></ul>
            </div>
            <div class="card">
                <h2>Thermal Sensors</h2>
                <ul id="thermalList"></ul>
            </div>
        </div>
        <div class="card" style="margin:2rem auto;max-width:700px;">
            <canvas id="cpuChart" height="80"></canvas>
        </div>
        <script>
        let interval = null;
        let cpuHistory = [];
        function fetchStats() {
            fetch('/api/stats').then(r => r.json()).then(data => {
                document.getElementById('lastUpdated').textContent = 'Last updated: ' + data.last_updated;
                // Memory health
                let memClass = 'mem-good';
                if (data.mem_health === 'medium') memClass = 'mem-medium';
                if (data.mem_health === 'low') memClass = 'mem-low';
                document.getElementById('memHealth').textContent = data.mem_health.toUpperCase();
                document.getElementById('memHealth').className = 'metric ' + memClass;
                document.getElementById('memDetail').textContent = `Free: ${data.free_ram.toFixed(1)} MB / ${data.total_ram} MB`;
                // Memory alert
                let memCard = document.getElementById('memCard');
                let memWarning = document.getElementById('memWarning');
                if (data.mem_health === 'low') {
                    memCard.classList.add('mem-alert');
                    memWarning.style.display = '';
                } else {
                    memCard.classList.remove('mem-alert');
                    memWarning.style.display = 'none';
                }
                // CPU list
                let cpuList = data.cpu.map(p => `<li>${p.name} (PID ${p.pid}): <b>${p.cpu}%</b> CPU <button class='kill-btn' onclick='killApp(${p.pid})'>Kill</button></li>`).join('');
                document.getElementById('cpuList').innerHTML = cpuList || '<li>No data</li>';
                // RAM list
                let ramList = data.ram.map(a => `<li>${a.name} (PID ${a.pid}): <b>${a.ram_mb.toFixed(1)} MB</b> RAM <button class='kill-btn' onclick='killApp(${a.pid})'>Kill</button></li>`).join('');
                document.getElementById('ramList').innerHTML = ramList || '<li>No data</li>';
                // Thermal
                let thermalList = Object.entries(data.thermal).map(([k,v]) => `<li>${k}: <b>${v}°C</b></li>`).join('');
                document.getElementById('thermalList').innerHTML = thermalList || '<li>No data</li>';
                // CPU chart
                if (data.cpu.length > 0) {
                    let total = data.cpu.reduce((acc, p) => acc + p.cpu, 0);
                    cpuHistory.push({t: data.last_updated, v: total});
                    if (cpuHistory.length > 30) cpuHistory.shift();
                    updateCpuChart();
                }
            });
        }
        function startSession() {
            fetch('/api/session/start', {method: 'POST'});
            if (!interval) interval = setInterval(fetchStats, 1000);
        }
        function stopSession() {
            fetch('/api/session/stop', {method: 'POST'}).then(r => r.json()).then(data => {
                alert('Session saved: ' + data.filename);
                clearInterval(interval); interval = null;
            });
        }
        function clearCache() {
            fetch('/api/clear_cache', {method: 'POST'}).then(r => r.json()).then(data => {
                alert(data.message);
            });
        }
        function killApp(pid) {
            if (confirm('Are you sure you want to kill app with PID ' + pid + '?')) {
                fetch('/api/kill_app', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pid: pid })
                }).then(r => r.json()).then(data => {
                    alert(data.message);
                    fetchStats();
                });
            }
        }
        // Chart.js for CPU
        let cpuChart = null;
        function updateCpuChart() {
            let ctx = document.getElementById('cpuChart').getContext('2d');
            let labels = cpuHistory.map(x => x.t.split(' ')[1]);
            let dataPoints = cpuHistory.map(x => x.v);
            if (!cpuChart) {
                cpuChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Total CPU %',
                            data: dataPoints,
                            borderColor: '#8ecfff',
                            backgroundColor: 'rgba(142,207,255,0.1)',
                            tension: 0.3,
                        }]
                    },
                    options: {
                        plugins: { legend: { labels: { color: '#fff' } } },
                        scales: {
                            x: { ticks: { color: '#aaa' } },
                            y: { ticks: { color: '#aaa' }, beginAtZero: true, max: 800 }
                        }
                    }
                });
            } else {
                cpuChart.data.labels = labels;
                cpuChart.data.datasets[0].data = dataPoints;
                cpuChart.update();
            }
        }
        fetchStats();
        </script>
    </body>
    </html>
    ''')

@app.route('/api/stats')
def api_stats():
    return jsonify(collect_stats())

@app.route('/api/session/start', methods=['POST'])
def api_session_start():
    global session_active, session_thread, session_data
    if not session_active:
        session_active = True
        session_data = []
        session_thread = threading.Thread(target=session_monitor, daemon=True)
        session_thread.start()
    return jsonify({'status': 'started'})

@app.route('/api/session/stop', methods=['POST'])
def api_session_stop():
    global session_active, session_thread, session_data
    session_active = False
    if session_thread:
        session_thread.join(timeout=2)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    outdir = f'run_{timestamp}_session'
    os.makedirs(outdir, exist_ok=True)
    filename = os.path.join(outdir, 'session.json')
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(session_data, f, indent=2)
    return jsonify({'status': 'stopped', 'filename': filename})

@app.route('/api/clear_cache', methods=['POST'])
def api_clear_cache():
    result = clear_cache_all()
    return jsonify(result)

@app.route('/api/kill_app', methods=['POST'])
def api_kill_app():
    data = request.get_json()
    pid = data.get('pid')
    if not pid:
        return jsonify({'status': 'error', 'message': 'No PID provided'}), 400
    output = run_adb(["adb", "shell", "kill", str(pid)])
    if output.strip() == '':
        return jsonify({'status': 'ok', 'message': f'App with PID {pid} killed.'})
    else:
        return jsonify({'status': 'ok', 'message': f'Kill output: {output.strip()}'})

# TODO: Add endpoints for storage, junk cleaning, app list/kill, jank analysis, etc.

if __name__ == '__main__':
    print("[INFO] Starting Android Performance Dashboard...")
    # Open the dashboard in the default web browser
    import threading as _threading
    def _open_browser(port):
        time.sleep(1)
        url = f'http://localhost:{port}'
        print(f"[INFO] Attempting to open browser at {url}")
        try:
            webbrowser.open(url)
        except Exception as e:
            print(f"[WARN] Could not open browser automatically: {e}")
    # Try to start on port 5000, fallback to 5050 if in use
    port = 5000
    try:
        _threading.Thread(target=_open_browser, args=(port,), daemon=True).start()
        app.run(debug=True, port=port)
    except OSError as e:
        print(f"[WARN] Port {port} in use or unavailable: {e}")
        port = 5050
        print(f"[INFO] Trying fallback port {port}...")
        _threading.Thread(target=_open_browser, args=(port,), daemon=True).start()
        app.run(debug=True, port=port)
    except Exception as e:
        print(f"[ERROR] Failed to start Flask server: {e}") 