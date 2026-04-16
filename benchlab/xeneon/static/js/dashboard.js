/**
 * Xeneon Dashboard JavaScript - Three Section Layout
 * Modern responsive dashboard with left sidebar, center sensors, and right graph
 *
 * Fixes applied:
 *  - apiGet now checks res.ok and throws on HTTP errors
 *  - All catch blocks log warnings instead of silently swallowing errors
 *  - Duplicate click listener removed from updateDeviceList (delegate in setupEventListeners is used)
 *  - scheduleReconnect uses clearTimeout to prevent unbounded timer stacking
 *  - Fan status check is now explicit (=== 1)
 *  - window.close() replaced with /api/shutdown fetch
 *  - Polling skips refreshTelemetry when WebSocket is already connected
 *  - refreshFleet throttled to every 15 seconds
 *  - Canvas buffer dimensions synced to CSS size on init and resize
 *  - telemetryHistory entries pruned when device changes
 */

class XeneonDashboard {
    constructor() {
        this.ws = null;
        this.apiBase = '/api';
        this.activeDevice = null;
        this.activeDeviceInfo = null;
        this.connectionStatus = 'disconnected';
        this.sensorData = {};
        this.selectedGraphMetrics = [];
        this.telemetryHistory = new Map();
        this.maxHistory = 100;

        // Timer handles
        this._reconnectTimer = null;
        this._fleetPollCounter = 0;
        this._fleetPollInterval = 15; // refresh fleet every N poll ticks

        this.config = {
            refreshInterval: 1000,
            reconnectDelay: 3000,
        };

        this.init();
    }

    init() {
        this.setupEventListeners();
        this.syncCanvasSize();
        this.refreshFleet();
        this.startPolling();
    }

    // =========================================================
    // CANVAS SIZING
    // =========================================================

    syncCanvasSize() {
        const canvas = document.getElementById('graph-canvas');
        if (!canvas) return;

        const resize = () => {
            // Set the drawing-buffer dimensions to match the CSS layout size
            canvas.width = canvas.offsetWidth;
            canvas.height = canvas.offsetHeight;
        };

        resize();
        window.addEventListener('resize', resize);
    }

    // =========================================================
    // DEVICE SELECTION (LEFT SIDEBAR)
    // =========================================================

    selectDevice(deviceUid) {
        // Clear history when switching devices so stale data doesn't bleed across
        if (this.activeDevice !== deviceUid) {
            this.telemetryHistory.clear();
        }
        this.activeDevice = deviceUid;
        this.connectToWebSocket();
        this.updateDeviceSelection();
    }

    updateDeviceSelection() {
        document.querySelectorAll('.device-item').forEach(item => {
            item.classList.remove('selected');
        });

        const selectedItem = document.querySelector(`.device-item[data-uid="${this.activeDevice}"]`);
        if (selectedItem) {
            selectedItem.classList.add('selected');
        }
    }

    // =========================================================
    // EVENT LISTENERS
    // =========================================================

    setupEventListeners() {
        // Delegated device selection — no per-item listeners needed
        document.getElementById('fleet-device-list').addEventListener('click', (e) => {
            const deviceItem = e.target.closest('.device-item');
            if (deviceItem) {
                const deviceUid = deviceItem.dataset.uid;
                if (deviceUid) {
                    this.selectDevice(deviceUid);
                }
            }
        });

        // Graph controls
        document.getElementById('btn-clear-graph').addEventListener('click', () => {
            this.clearGraph();
        });

        // Shutdown button — window.close() is blocked by browsers; call the server instead
        document.getElementById('btn-shutdown').addEventListener('click', async () => {
            if (confirm('Shutdown dashboard?')) {
                try {
                    await fetch('/api/shutdown', { method: 'POST' });
                } catch (e) {
                    // Server may close before responding — that is expected
                }
                document.body.innerHTML = '<p style="color:#FCE477;font-family:monospace;padding:2rem">Dashboard shut down.</p>';
            }
        });
    }

    clearGraph() {
        const canvas = document.getElementById('graph-canvas');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            // Use the drawing-buffer dimensions, not the CSS dimensions
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }
    }

    // =========================================================
    // WEBSOCKET
    // =========================================================

    connectToWebSocket() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }

        try {
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${proto}//${location.host}/api/device/${this.activeDevice || 'all'}/stream`;
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                this.setConnectionStatus('connected');
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleTelemetryUpdate(data);
                } catch (e) {
                    console.warn('WebSocket message parse error:', e);
                }
            };

            this.ws.onclose = () => {
                this.setConnectionStatus('disconnected');
                this.scheduleReconnect();
            };

            this.ws.onerror = (e) => {
                console.warn('WebSocket error:', e);
                this.setConnectionStatus('error');
            };

        } catch (e) {
            console.warn('WebSocket connection failed:', e);
            this.setConnectionStatus('disconnected');
        }
    }

    scheduleReconnect() {
        // Cancel any pending reconnect before scheduling a new one
        clearTimeout(this._reconnectTimer);
        this._reconnectTimer = setTimeout(() => {
            if (this.connectionStatus !== 'connected' && this.activeDevice) {
                this.connectToWebSocket();
            }
        }, this.config.reconnectDelay);
    }

    // =========================================================
    // POLLING
    // =========================================================

    startPolling() {
        setInterval(() => {
            // Fleet list changes infrequently — poll it every 15 ticks (~15 s)
            this._fleetPollCounter++;
            if (this._fleetPollCounter >= this._fleetPollInterval) {
                this._fleetPollCounter = 0;
                this.refreshFleet();
            }

            // Only fall back to HTTP telemetry polling when WebSocket is not live
            if (this.activeDevice && this.connectionStatus !== 'connected') {
                this.refreshTelemetry();
            }

            document.getElementById('last-update').textContent =
                `Last update: ${new Date().toLocaleTimeString()}`;

        }, this.config.refreshInterval);
    }

    // =========================================================
    // API
    // =========================================================

    async apiGet(endpoint) {
        const res = await fetch(`${this.apiBase}${endpoint}`);
        if (!res.ok) {
            throw new Error(`HTTP ${res.status} for ${endpoint}`);
        }
        return res.json();
    }

    async refreshFleet() {
        try {
            const devices = await this.apiGet('/devices');
            this.updateDeviceList(devices);
        } catch (e) {
            console.warn('refreshFleet failed:', e);
        }
    }

    async refreshTelemetry() {
        try {
            const telemetry = await this.apiGet(`/device/${this.activeDevice}/telemetry`);
            this.handleTelemetryUpdate(telemetry);
        } catch (e) {
            console.warn('refreshTelemetry failed:', e);
        }

        try {
            this.activeDeviceInfo = await this.apiGet(`/device/${this.activeDevice}/info`);
            this.updateFooterInfo();
        } catch (e) {
            console.warn('refreshDeviceInfo failed:', e);
        }
    }

    // =========================================================
    // DATA HANDLING
    // =========================================================

    handleTelemetryUpdate(data) {
        this.sensorData = data;

        for (const [key, value] of Object.entries(data)) {
            if (!this.telemetryHistory.has(key)) {
                this.telemetryHistory.set(key, []);
            }
            const history = this.telemetryHistory.get(key);
            history.push({ timestamp: Date.now(), value });
            if (history.length > this.maxHistory) {
                history.shift();
            }
        }

        this.renderCards();
    }

    // =========================================================
    // FLEET / DEVICE LIST
    // =========================================================

    updateDeviceList(devices) {
        const listEl = document.getElementById('fleet-device-list');
        if (!listEl) return;

        listEl.innerHTML = '';

        if (!devices || devices.length === 0) {
            listEl.innerHTML = `
                <div class="device-item placeholder">
                    <div class="device-info">
                        <span class="device-name">No devices found</span>
                        <span class="device-details">Scanning for Benchlab devices...</span>
                    </div>
                </div>
            `;
            return;
        }

        devices.forEach(device => {
            const item = document.createElement('div');
            item.className = 'device-item';
            item.dataset.uid = device.uid;

            // No per-item addEventListener — handled by the delegated listener in setupEventListeners
            item.innerHTML = `
                <div class="device-info">
                    <span class="device-name">Port: ${device.port} | UID: ${device.uid}</span>
                    <span class="device-details">Firmware: ${device.firmware || 'unknown'}</span>
                </div>
                <span class="device-status connected">CONNECTED</span>
            `;

            listEl.appendChild(item);
        });

        // Re-apply selected highlight after list rebuild
        this.updateDeviceSelection();
    }

    // =========================================================
    // SENSOR CARDS
    // =========================================================

    renderCards() {
        this.renderSummaryCard();
        this.renderTemperaturesCard();
        this.renderFansCard();
        this.renderPowerCard();
        this.renderCurrentCard();
        this.renderVoltageCard();
        this.renderVinsCard();
    }

    renderSummaryCard() {
        const el = document.getElementById('card-summary');
        if (!el) return;

        el.innerHTML = `
            <div class="telemetry-item">
                <span class="telemetry-label">SYS</span>
                <span class="telemetry-value">${(this.sensorData.SYS_Power || 0).toFixed(1)} W</span>
            </div>
            <div class="telemetry-item">
                <span class="telemetry-label">CPU</span>
                <span class="telemetry-value">${(this.sensorData.CPU_Power || 0).toFixed(1)} W</span>
            </div>
            <div class="telemetry-item">
                <span class="telemetry-label">GPU</span>
                <span class="telemetry-value">${(this.sensorData.GPU_Power || 0).toFixed(1)} W</span>
            </div>
            <div class="telemetry-item">
                <span class="telemetry-label">MB</span>
                <span class="telemetry-value">${(this.sensorData.MB_Power || 0).toFixed(1)} W</span>
            </div>
        `;
    }

    renderTemperaturesCard() {
        const el = document.getElementById('card-temperatures');
        if (!el) return;

        el.innerHTML = `
            <div class="telemetry-item">
                <span class="telemetry-label">Chip</span>
                <span class="telemetry-value">${this.sensorData.Chip_Temp || 0}°C</span>
            </div>
            <div class="telemetry-item">
                <span class="telemetry-label">Ambient</span>
                <span class="telemetry-value">${this.sensorData.Ambient_Temp || 0}°C</span>
            </div>
            <div class="telemetry-item">
                <span class="telemetry-label">Humidity</span>
                <span class="telemetry-value">${(this.sensorData.Humidity || 0).toFixed(1)}%</span>
            </div>
            <div class="telemetry-item">
                <span class="telemetry-label">S1</span>
                <span class="telemetry-value">${this.sensorData.Temp_Sensor_1 || 'N/A'}</span>
            </div>
            <div class="telemetry-item">
                <span class="telemetry-label">S2</span>
                <span class="telemetry-value">${this.sensorData.Temp_Sensor_2 || 'N/A'}</span>
            </div>
            <div class="telemetry-item">
                <span class="telemetry-label">S3</span>
                <span class="telemetry-value">${this.sensorData.Temp_Sensor_3 || 'N/A'}</span>
            </div>
            <div class="telemetry-item">
                <span class="telemetry-label">S4</span>
                <span class="telemetry-value">${this.sensorData.Temp_Sensor_4 || 'N/A'}</span>
            </div>
        `;
    }

    renderFansCard() {
        const el = document.getElementById('card-fans');
        if (!el) return;

        let html = '';
        for (let i = 1; i <= 9; i++) {
            const duty = this.sensorData[`Fan${i}_Duty`] || 0;
            const rpm  = this.sensorData[`Fan${i}_RPM`]  || 0;
            // Explicit check: treat numeric 1 as ON, everything else as OFF
            const status = this.sensorData[`Fan${i}_Status`] === 1 ? 'ON' : 'OFF';
            html += `
                <div class="telemetry-item">
                    <span class="telemetry-label">Fan${i}</span>
                    <span class="telemetry-value">${duty}% | ${rpm} RPM | ${status}</span>
                </div>
            `;
        }
        html += `
            <div class="telemetry-item">
                <span class="telemetry-label">Ext Fan Duty</span>
                <span class="telemetry-value">${this.sensorData.FanExtDuty || 0}%</span>
            </div>
        `;
        el.innerHTML = html;
    }

    renderPowerCard() {
        const el = document.getElementById('card-power');
        if (!el) return;

        const rails = ['EPS1','EPS2','12V','5V','5VSB','3.3V','PCIE8_1','PCIE8_2','PCIE8_3','HPWR1','HPWR2'];
        let html = '';

        rails.forEach(rail => {
            const value = this.sensorData[`${rail}_Power`] || 0;
            html += `
                <div class="telemetry-item">
                    <span class="telemetry-label">${rail}</span>
                    <span class="telemetry-value">${value.toFixed(2)} W</span>
                </div>
            `;
        });

        el.innerHTML = html;
    }

    renderCurrentCard() {
        const el = document.getElementById('card-current');
        if (!el) return;

        const rails = ['EPS1','EPS2','12V','5V','5VSB','3.3V','PCIE8_1','PCIE8_2','PCIE8_3','HPWR1','HPWR2'];
        let html = '';

        rails.forEach(rail => {
            const value = this.sensorData[`${rail}_Current`] || 0;
            html += `
                <div class="telemetry-item">
                    <span class="telemetry-label">${rail}</span>
                    <span class="telemetry-value">${value.toFixed(2)} A</span>
                </div>
            `;
        });

        el.innerHTML = html;
    }

    renderVoltageCard() {
        const el = document.getElementById('card-voltage');
        if (!el) return;

        const rails = ['EPS1','EPS2','12V','5V','5VSB','3.3V','PCIE8_1','PCIE8_2','PCIE8_3','HPWR1','HPWR2'];
        let html = '';

        rails.forEach(rail => {
            const value = this.sensorData[`${rail}_Voltage`] || 0;
            html += `
                <div class="telemetry-item">
                    <span class="telemetry-label">${rail}</span>
                    <span class="telemetry-value">${value.toFixed(2)} V</span>
                </div>
            `;
        });

        el.innerHTML = html;
    }

    renderVinsCard() {
        const el = document.getElementById('card-vins');
        if (!el) return;

        let html = '';
        for (let i = 0; i <= 12; i++) {
            const value = this.sensorData[`VIN_${i}`] || 0;
            html += `
                <div class="telemetry-item">
                    <span class="telemetry-label">VIN_${i}</span>
                    <span class="telemetry-value">${value.toFixed(3)} V</span>
                </div>
            `;
        }
        html += `
            <div class="telemetry-item">
                <span class="telemetry-label">Vdd</span>
                <span class="telemetry-value">${(this.sensorData.Vdd || 0).toFixed(3)} V</span>
            </div>
            <div class="telemetry-item">
                <span class="telemetry-label">Vref</span>
                <span class="telemetry-value">${(this.sensorData.Vref || 0).toFixed(3)} V</span>
            </div>
        `;
        el.innerHTML = html;
    }

    // =========================================================
    // STATUS
    // =========================================================

    setConnectionStatus(status) {
        this.connectionStatus = status;
        const indicator = document.getElementById('connection-status');
        const text = document.getElementById('status-text');

        if (indicator) {
            indicator.className = `status-indicator status-${status}`;
        }
        if (text) {
            text.textContent = status.charAt(0).toUpperCase() + status.slice(1);
        }
    }

    updateFooterInfo() {
        const footerInfo = document.getElementById('footer-info');
        if (!footerInfo || !this.activeDeviceInfo) return;

        const parts = [
            `Port: ${this.activeDeviceInfo.port || 'N/A'}`,
            `UID: ${this.activeDevice || 'N/A'}`,
            `FW: 0x${(this.activeDeviceInfo.FwVersion || 0).toString(16).toUpperCase()}`
        ];

        footerInfo.textContent = parts.join(' | ');
    }
}

// =========================================================
// INIT
// =========================================================

document.addEventListener('DOMContentLoaded', () => {
    window.xeneonDashboard = new XeneonDashboard();
});