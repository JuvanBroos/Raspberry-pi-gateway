const wsStatus = document.getElementById('ws-status');
const wsText = document.getElementById('ws-text');
const tbody = document.querySelector('#telemetry-table tbody');
const logBox = document.getElementById('log-box');

// Node State
const nodes = {};
let activeChartNodeId = null;
let currentChartData = [];

// Charts
let mainChart;

function initCharts() {
    const ctx = document.getElementById('mainChart').getContext('2d');
    Chart.defaults.color = '#94a3b8';
    Chart.defaults.borderColor = 'rgba(255,255,255,0.05)';

    mainChart = new Chart(ctx, {
        type: 'line',
        data: { labels: [], datasets: [{ label: 'Metric', data: [], borderColor: '#38bdf8', tension: 0.3, backgroundColor: 'rgba(56, 189, 248, 0.1)', fill: true }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: true } } }
    });
}

function updateChartDisplay() {
    if (!mainChart) return;
    
    const metric = document.getElementById('chart-metric-select').value;
    const metricLabels = {
        'temp_c': 'Temperature (°C)',
        'batt_mv': 'Battery (mV)',
        'rssi': 'RSSI (dBm)',
        'gnss_sats': 'GNSS Sats'
    };
    
    mainChart.data.datasets[0].label = metricLabels[metric];
    mainChart.data.labels = currentChartData.map(row => new Date(row.timestamp_ms).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}));
    mainChart.data.datasets[0].data = currentChartData.map(row => row[metric]);
    mainChart.update();
}

document.getElementById('chart-metric-select').onchange = updateChartDisplay;

function updateTable(nodeId, data) {
    nodes[nodeId] = data;
    
    let row = document.getElementById(`row-${nodeId}`);
    if (!row) {
        row = document.createElement('tr');
        row.id = `row-${nodeId}`;
        row.onclick = () => showNodeDetails(nodeId);
        tbody.appendChild(row);
    }
    
    const d = new Date(data.timestamp_ms);
    const timeStr = d.toLocaleTimeString();

    row.innerHTML = `
        <td><strong>${data.node_id}</strong></td>
        <td>${data.protocol}</td>
        <td>${data.rssi}</td>
        <td>${data.batt_mv}</td>
        <td>${data.temp_c}</td>
        <td>${data.gnss_sats}</td>
        <td>${data.lat} / ${data.lon}</td>
        <td>${timeStr}</td>
    `;
}

function appendLog(msg) {
    const d = new Date();
    const div = document.createElement('div');
    div.className = 'log-entry';
    div.innerHTML = `<span class="log-time">[${d.toLocaleTimeString()}]</span> ${msg}`;
    logBox.appendChild(div);
    logBox.scrollTop = logBox.scrollHeight;
}

// WebSocket connection
function connectWS() {
    const wsUrl = `ws://${window.location.host}/ws/live`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        wsStatus.className = 'dot connected';
        wsText.innerText = 'Live';
        appendLog("WebSocket Connected.");
    };

    ws.onclose = () => {
        wsStatus.className = 'dot disconnected';
        wsText.innerText = 'Disconnected';
        appendLog("WebSocket Disconnected. Reconnecting in 3s...");
        setTimeout(connectWS, 3000);
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        const topic = msg.topic;
        const payload = msg.payload;

        if (topic === "wsn/uplink/data") {
            updateTable(payload.node_id, payload);
            
            // Live update the chart if it's currently open
            if (activeChartNodeId === payload.node_id && document.getElementById('node-details').style.display === 'block') {
                currentChartData.push(payload);
                if (currentChartData.length > 30) {
                    currentChartData.shift(); // keep max 30 points on chart
                }
                updateChartDisplay(); // Re-render the selected metric
            }
        } else if (topic === "wsn/uplink/status") {
            appendLog(`Status update from ${payload.node_id}: ${payload.status}`);
        } else if (topic === "wsn/uplink/log") {
            const idStr = payload.node_id ? `${payload.node_id}: ` : '';
            appendLog(`Log (${payload.protocol}) ${idStr}${payload.message}`);
        }
    };
}

async function showNodeDetails(nodeId) {
    activeChartNodeId = nodeId;
    document.getElementById('node-details').style.display = 'block';
    document.getElementById('chart-title').innerText = `History: ${nodeId}`;
    
    // Auto-fill command form
    document.getElementById('target_addr').value = nodeId;

    try {
        const res = await fetch(`/api/history/${nodeId}?limit=30`);
        const history = await res.json();
        
        history.reverse(); // oldest first for chart
        currentChartData = history;
        updateChartDisplay();

    } catch (e) {
        console.error("Failed to fetch history", e);
    }
}

document.getElementById('close-charts').onclick = () => {
    document.getElementById('node-details').style.display = 'none';
};

// Form logic
const cmdTypeSelect = document.getElementById('cmd_type');
const sleepInputs = document.getElementById('sleep-inputs');
const protocolInputs = document.getElementById('protocol-inputs');

cmdTypeSelect.onchange = () => {
    const val = cmdTypeSelect.value;
    sleepInputs.style.display = val === "3" ? "block" : "none";
    protocolInputs.style.display = val === "2" ? "block" : "none";
};

document.getElementById('command-form').onsubmit = async (e) => {
    e.preventDefault();
    
    const cmdType = parseInt(document.getElementById('cmd_type').value);
    let val3 = 0;
    
    if (cmdType === 3) {
        val3 = parseInt(document.getElementById('val3').value || 0);
    } else if (cmdType === 2) {
        val3 = parseInt(document.getElementById('proto_val3').value);
    }

    const payload = {
        target_addr: document.getElementById('target_addr').value,
        cmd_type: cmdType,
        proto: parseInt(document.getElementById('proto').value),
        val1: 0,
        val2: 0,
        val3: val3
    };

    try {
        const res = await fetch('/api/command', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        appendLog(`Command sent: ${JSON.stringify(payload)}`);
    } catch (e) {
        appendLog(`Failed to send command: ${e.message}`);
    }
};

// Initialize
initCharts();
connectWS();

// Fetch initial latest reading for all nodes from history
fetch('/api/history?limit=100')
    .then(res => res.json())
    .then(history => {
        const seen = new Set();
        for (const row of history) {
            if (!seen.has(row.node_id)) {
                updateTable(row.node_id, row);
                seen.add(row.node_id);
            }
        }
    })
    .catch(e => console.error("History error", e));
